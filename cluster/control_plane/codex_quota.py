"""Read only Codex plan quota events; session text and paths never leave here."""
from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path

MAX_FILES = 16
MAX_TAIL_BYTES = 262144
MAX_DIRECTORY_ENTRIES = 2048
MAX_AGE_SECONDS = 3600
WEEK_MINUTES = 10080
SOURCE = 'codex_session_rate_limits'


def _empty(status='unknown'):
    return dict(status=status, weekly_pct=None, observed_at=None, resets_at=None,
                window_minutes=None, window=None, plan_type=None, source=SOURCE)


def _entries(path):
    with os.scandir(path) as entries:
        result = list(islice(entries, MAX_DIRECTORY_ENTRIES + 1))
    if len(result) > MAX_DIRECTORY_ENTRIES:
        raise OSError('Session directory exceeds scan bound')
    return result


def _directories(path, digits, count):
    children = [Path(entry.path) for entry in _entries(path)
                if re.fullmatch(r'[0-9]{' + str(digits) + '}', entry.name)
                and entry.is_dir(follow_symlinks=False)]
    return sorted(children, reverse=True)[:count]


def _session_files(root):
    days = []
    for year in _directories(root, 4, 2):
        for month in _directories(year, 2, 2):
            days.extend(_directories(month, 2, 4))
    files = []
    for day in sorted(days, reverse=True)[:4]:
        for entry in _entries(day):
            if entry.name.endswith('.jsonl') and entry.is_file(follow_symlinks=False):
                files.append((entry.stat(follow_symlinks=False).st_mtime_ns,
                              Path(entry.path)))
    return [path for _, path in sorted(files, reverse=True)[:MAX_FILES]]


def _tail_records(path):
    with path.open('rb') as source:
        source.seek(0, 2)
        offset = max(0, source.tell() - MAX_TAIL_BYTES)
        source.seek(offset)
        tail = source.read(MAX_TAIL_BYTES)
    # The first line may start mid-record; the last may still be appending.
    if offset:
        tail = tail.partition(b'\n')[2]
    if not tail.endswith(b'\n'):
        tail = tail.rpartition(b'\n')[0]
    for line in tail.splitlines():
        if line.strip():
            yield json.loads(line)


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _observation(stamp, limits, now, max_age):
    result = _empty('malformed')
    result['observed_at'] = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    windows = [(name, limits.get(name)) for name in ('primary', 'secondary')]
    weekly = [(name, window) for name, window in windows if isinstance(window, dict)
              and type(window.get('window_minutes')) is int
              and window['window_minutes'] == WEEK_MINUTES]
    if not weekly or stamp > now + 60:
        return result
    values = []
    for name, window in weekly:
        pct, reset = window.get('used_percent'), window.get('resets_at')
        if (not _number(pct) or not 0 <= pct <= 100 or type(reset) is not int
                or reset <= stamp or reset > stamp + WEEK_MINUTES * 60 + 60):
            return result
        values.append((float(pct), reset, name))
    # Conflicting weekly windows cannot authorize a call based on the lower one.
    if len({(pct, reset) for pct, reset, _ in values}) > 1:
        return result
    pct, reset, name = values[0]
    plan = limits.get('plan_type')
    result.update(resets_at=reset, window_minutes=WEEK_MINUTES, window=name,
                  plan_type=plan if plan in ('free', 'plus', 'pro', 'team',
                                            'business', 'enterprise', 'edu') else None)
    if now - stamp > max_age or reset <= now:
        result['status'] = 'stale'
    else:
        result.update(status='ok', weekly_pct=pct)
    return result


def read_codex_quota(session_root=None, *, now=None, max_age_seconds=MAX_AGE_SECONDS):
    """Select the latest actual observation from bounded recent session tails.

    File names/mtimes bound discovery only. Older valid data never replaces a
    newer malformed Codex observation. Missing, expired or stale data is unknown
    for admission; no dollar estimates or session content are returned.
    """
    now = time.time() if now is None else now
    if not _number(now) or not _number(max_age_seconds) or max_age_seconds <= 0:
        return _empty('malformed')
    root = Path(session_root) if session_root is not None else Path.home() / '.codex/sessions'
    latest = None
    try:
        for path in _session_files(root):
            for row in _tail_records(path):
                if not isinstance(row, dict) or row.get('type') != 'event_msg':
                    continue
                payload = row.get('payload')
                if not isinstance(payload, dict) or payload.get('type') != 'token_count':
                    continue
                limits = payload.get('rate_limits')
                if not isinstance(limits, dict) or limits.get('limit_id') != 'codex':
                    continue
                raw_stamp = row.get('timestamp')
                if not isinstance(raw_stamp, str) or len(raw_stamp) > 40:
                    return _empty('malformed')
                stamp = datetime.fromisoformat(raw_stamp.replace('Z', '+00:00'))
                if stamp.tzinfo is None:
                    return _empty('malformed')
                observed = stamp.timestamp()
                value = _observation(observed, limits, now, max_age_seconds)
                if latest is None or observed > latest[0]:
                    latest = (observed, value)
                elif observed == latest[0]:
                    if value['status'] != 'ok' or latest[1]['status'] != 'ok':
                        latest = (observed, _empty('malformed'))
                    elif value['weekly_pct'] > latest[1]['weekly_pct']:
                        latest = (observed, value)
    except (OSError, ValueError, TypeError, OverflowError):
        return _empty('malformed')
    return latest[1] if latest is not None else _empty()
