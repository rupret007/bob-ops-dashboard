"""Small, read-only engine snapshot. No shell commands or network calls."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path


def tail(path, limit=262144):
    """Bound disk work even when append-only logs grow indefinitely."""
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            if size > limit:
                stream.readline()
            return stream.read(limit).decode('utf-8', errors='replace').splitlines()
    except OSError:
        return []


def read_json(path):
    try:
        if path.stat().st_size > 2097152:
            return None
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def stamp(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return 0


def rows(path):
    for line in tail(path, 2097152):
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                yield value
        except ValueError:
            continue


class EngineCollector:
    def __init__(self, data_dir, karen_dir, machine):
        self.data_dir = Path(data_dir)
        self.karen_dir = Path(karen_dir)
        self.machine = machine
        self._cached = None
        self._at = 0

    def collect(self):
        # Browsers can poll independently without rescanning files per client.
        if self._cached is not None and time.monotonic() - self._at < 2:
            return self._cached
        self._cached = self._collect()
        self._at = time.monotonic()
        return self._cached

    def _collect(self):
        now = datetime.now(timezone.utc)
        hour = now.timestamp() - 3600
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        jobs = {}
        events = []
        for job in rows(self.data_dir / 'jobs' / 'ledger.jsonl'):
            jobs[job.get('job_id', '')] = job
            # Terminal timestamps survive old ledger rewrites that refreshed updated_at.
            event_time = (job.get('end_time') if job.get('state') in ('done', 'failed', 'expired')
                          else job.get('start_time') if job.get('state') == 'running'
                          else job.get('created_at'))
            events.append({'timestamp': event_time or job.get('updated_at') or job.get('created_at', ''),
                           'text': '{} #{}: {}'.format(job.get('repo', ''), job.get('pr_number', ''), job.get('state', 'unknown'))})
        latest = [j for j in jobs.values() if j.get('machine', self.machine) == self.machine]
        completed = [j for j in latest if j.get('state') == 'done' and j.get('outcome_verified')]
        jobs_hour = sum(stamp(j.get('end_time')) >= hour for j in completed)
        running = sum(j.get('state') == 'running' and stamp(j.get('lease_expires')) > now.timestamp() for j in latest)
        verdicts = []
        verdict_dir = self.karen_dir / 'verdicts'
        if not verdict_dir.is_dir():
            verdict_dir = self.data_dir / 'verdicts'
        # Read real verdict files; LATEST symlinks must not double-count reviews.
        for path in list(verdict_dir.rglob('*.json'))[:2000]:
            if path.is_symlink():
                continue
            value = read_json(path)
            if not isinstance(value, dict):
                continue
            verdicts.append(value)
            events.append({'timestamp': value.get('timestamp', ''), 'text': 'Karen {} #{}: {}'.format(
                value.get('repo', ''), value.get('pr', value.get('pr_number', '')), 'PASS' if value.get('passed') else 'FAIL')})
        recent = [v for v in verdicts if stamp(v.get('timestamp')) >= hour]
        attempts = [j for j in latest if j.get('state') in ('done', 'failed', 'expired') and stamp(j.get('end_time')) >= hour]
        merge_state = read_json(self.data_dir / 'conductor' / 'merge-state.json')
        merge_rows = merge_state.get('merges', []) if isinstance(merge_state, dict) else []
        merges = sum((m.get('ts') or stamp(m.get('time'))) >= midnight for m in merge_rows)
        backlog = read_json(self.data_dir / 'backlog.json')
        backlog_count = len(backlog['prs']) if isinstance(backlog, dict) and isinstance(backlog.get('prs'), list) else None
        if backlog_count is None:
            metrics = read_json(self.data_dir / 'metrics' / 'conductor-metrics.json')
            if isinstance(metrics, dict):
                backlog_count = metrics.get('total_open_prs')
        health = {}
        capacity = None
        for machine in ('mini', 'nucbox'):
            # Remote health uses synced watchdog evidence, never local load under a remote name.
            path = self.data_dir / 'watchdog.log' if machine == self.machine else self.data_dir / 'machines' / machine / 'watchdog.log'
            evidence = list(rows(path))
            last = evidence[-1] if evidence else {}
            age = now.timestamp() - stamp(last.get('timestamp'))
            checks = last.get('checks', [])
            fresh = bool(checks) and 0 <= age < 1800
            failed = [c for c in checks if not c.get('passed')]
            state = ('red' if failed else 'green') if fresh else 'yellow'
            health[machine] = {'state': state, 'timestamp': last.get('timestamp'),
                               'detail': 'stale or unavailable' if not fresh else '{} failed checks'.format(len(failed))}
        dispatch = read_json(self.data_dir / 'dispatch' / 'dispatch-status.json')
        if isinstance(dispatch, dict):
            decisions = dispatch.get('summary', {}).get('decisions', [])
            for decision in decisions:
                guard = decision.get('guard_values', {})
                if 'lanes_max' in guard:
                    capacity = guard['lanes_max']
                    break
        # NucBox has an explicit deterministic capacity; Mini capacity needs evidence.
        if capacity is None and self.machine == 'nucbox':
            from ..guards import NUCBOX_MAX_LANES
            capacity = NUCBOX_MAX_LANES
        overall = 'red' if any(v['state'] == 'red' for v in health.values()) else ('green' if all(v['state'] == 'green' for v in health.values()) else 'yellow')
        monitor = [line for line in tail(self.data_dir / 'MONITOR.md', 32768) if line.strip()]
        def gauge(label, value, maximum=None, unit=''):
            return {'label': label, 'value': value, 'max': maximum, 'unit': unit}
        return {'timestamp': now.isoformat(), 'engine': overall, 'machines': health,
                'gauges': [gauge('Active lanes', running, capacity), gauge('Verified jobs / hour', jobs_hour),
                           gauge('Karen reviews / hour', len(recent)),
                           gauge('PASS rate · last hour', round(100 * sum(bool(v.get('passed')) for v in recent) / len(recent), 1) if recent else None, 100, '%'),
                           gauge('Merges today · UTC', merges if merge_state is not None else None, 30),
                           gauge('Fix success · last hour', round(100 * sum(j.get('state') == 'done' and bool(j.get('outcome_verified')) for j in attempts) / len(attempts), 1) if attempts else None, 100, '%'),
                           gauge('Backlog remaining', backlog_count)],
                'activity': sorted(events, key=lambda e: stamp(e['timestamp']), reverse=True)[:15],
                'monitor': monitor[-1] if monitor else 'No monitor status yet',
                'parity': next(iter(tail(self.data_dir / 'PARITY.md', 32768)), 'Parity audit unavailable'),
                'notes': 'Local ledger tail (2 MiB); up to 2000 verdict files. Backlog is the last conductor snapshot. Unknown values display —. Rates use a rolling hour; merge day uses UTC (the gate enforces its cap over a rolling 24h).'}
