"""Health snapshots, incident projection and one local recovery lock."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps

_mutex = threading.RLock()
_local = threading.local()
BEGIN = '<!-- storypool-health:start -->'
END = '<!-- storypool-health:end -->'


def data_dir():
    return Path(os.environ.get('STORYPOOL_DATA_DIR', '~/.local/share/storypool')).expanduser()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(value, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_status(root=None):
    try:
        path = Path(root or data_dir()) / 'health-status.json'
        if path.stat().st_size > 8 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def authoritative(root=None):
    return read_status(root).get('rollout', {}).get('phase') in ('active', 'canary', 'switching')


def serialized_recovery(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with recovery_lock():
            return function(*args, **kwargs)
    return wrapped


@contextmanager
def recovery_lock(root=None, blocking=True):
    """Reentrant within a thread; serialized across threads and processes."""
    root = Path(root or data_dir())
    root.mkdir(parents=True, exist_ok=True)
    with _mutex:
        if getattr(_local, 'depth', 0):
            _local.depth += 1
            try:
                yield True
            finally:
                _local.depth -= 1
            return
        with (root / '.health-recovery.lock').open('a') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                yield False
                return
            _local.depth = 1
            try:
                yield True
            finally:
                _local.depth = 0
                fcntl.flock(stream, fcntl.LOCK_UN)


def unknown(check):
    message = check.get('message', '').lower()
    return (check.get('unknown', False) or message.startswith('warning:')
            or 'unverified' in message or 'could not check' in message
            or 'log file not found' in message or message == 'no log files')


def observe(status, machine, group, checks, now):
    incidents = status.setdefault('incidents', {})
    for check in checks:
        key = ':'.join((machine, group, check['name']))
        old = incidents.get(key)
        if unknown(check):
            if old:
                old['failures'] = 0
            continue  # Unknown cannot resolve an existing incident.
        if check.get('passed'):
            if old and old['state'] != 'resolved':
                old.update(state='resolved', resolved_at=now, notify=False, failures=0)
            continue
        if not old or old['state'] == 'resolved':
            old = incidents[key] = dict(machine=machine, group=group, name=check['name'],
                                        first_seen=now, failures=0, state='pending')
        old.update(last_seen=now, failures=old['failures'] + 1)
        transient = check['name'].startswith('labels_') and check.get('execution_error')
        old['notify'] = old['failures'] >= (3 if transient else 1)
        old['state'] = 'firing' if old['notify'] else 'pending'


def render_alerts(root, status):
    """Only our delimited section is replaced; human actions are preserved."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.needs-jeff.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / 'NEEDS_JEFF.md'
        existing = path.read_text() if path.exists() else '# NEEDS_JEFF\n'
        start, end = existing.find(BEGIN), existing.find(END)
        if start >= 0 and end < start:
            raise ValueError('Incomplete managed health section')
        entries = []
        archive = root / 'NEEDS_JEFF.resolved.md'
        archived = archive.read_text() if archive.exists() else ''
        for key, issue in status.get('incidents', {}).items():
            if issue.get('state') == 'resolved':
                identity = '{}@{}'.format(key, issue['first_seen'])
                marker = '<!-- health-resolved:' + identity + ' -->'
                if marker not in archived:
                    archived += '\n{} Resolved: {}\n'.format(marker, key)
            if issue.get('notify') and issue.get('state') == 'firing':
                date = datetime.fromtimestamp(issue['first_seen'], timezone.utc).isoformat()
                entries.append('- {} — Health {} / {} / {}: inspect health-status.json and logs/health.jsonl.'.format(
                    date, issue['machine'], issue['group'], issue['name']))
        if archived:
            archive.write_text(archived)
        section = BEGIN + '\n' + '\n'.join(sorted(entries)) + '\n' + END
        text = (existing[:start] + section + existing[end + len(END):] if start >= 0
                else existing.rstrip() + '\n\n' + section + '\n')
        descriptor, temporary = tempfile.mkstemp(prefix='.health-alerts-', dir=str(root))
        try:
            with os.fdopen(descriptor, 'w') as output:
                output.write(text)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def verdict(status, machine, now):
    """Never use expired, missing, wrong-node or future evidence as green."""
    result = {'state': 'yellow', 'detail': 'health evidence unavailable or stale',
              'timestamp': status.get('timestamp'), 'guardian': {}}
    timestamp = status.get('timestamp', 0)
    if (status.get('schema') != 1 or status.get('machine') != machine
            or not isinstance(timestamp, (int, float)) or not 0 <= now - timestamp <= 150):
        return result
    groups = status.get('groups', {})
    expected = status.get('expected_groups', list(groups))
    if not expected:
        return result
    failures, warnings = [], []
    for name in expected:
        group = groups.get(name, {})
        observed = group.get('finished_at', 0)
        if not group or group.get('error') or not 0 <= now - observed <= group.get('period', 120) + 90:
            warnings.append(name)
            continue
        for check in group.get('checks', []):
            if unknown(check) or check.get('execution_error'):
                warnings.append(check.get('name', name))
            elif not check.get('passed') and not check.get('repaired'):
                failures.append(check.get('name', name))
    result.update(state='red' if failures else 'yellow' if warnings else 'green',
                  detail=('Failed: ' + ', '.join(failures[:5]) if failures else
                          'Unconfirmed: ' + ', '.join(warnings[:5]) if warnings else 'All health checks current'),
                  guardian=groups.get('guardian', {}).get('details', {}))
    return result
