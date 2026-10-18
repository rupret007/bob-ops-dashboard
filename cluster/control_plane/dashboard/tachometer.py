"""Small, read-only engine snapshot. No shell commands or network calls."""
from __future__ import annotations

import json
import os
import re
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


def blocked_snapshot(value):
    """Validate the persisted display projection before using identity fields."""
    if not isinstance(value, dict):
        return None
    repo = value.get('repo')
    sha = value.get('head_sha')
    number = value.get('pr_number')
    reason = value.get('reason')
    timestamp = value.get('timestamp')
    if (not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
            or type(number) is not int or number < 1
            or not isinstance(sha, str) or not re.fullmatch(r'[0-9a-fA-F]{40}', sha)
            or not isinstance(reason, str) or not reason.strip() or len(reason) > 4096
            or not isinstance(timestamp, str) or stamp(timestamp) <= 0):
        return None
    return dict(repo=repo, pr_number=number, head_sha=sha, reason=reason, timestamp=timestamp)


def rows(path):
    for line in tail(path, 2097152):
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                yield value
        except ValueError:
            continue


def review_state(value):
    """Normalize display state only; this is never a trusted merge verdict."""
    if value.get('status', 'COMPLETE') != 'COMPLETE':
        return 'ERROR'
    passed = value.get('passed')
    if passed is True or passed == 'True':
        return 'PASS'
    if passed is False or passed == 'False':
        return 'FAIL'
    return 'ERROR'


def observed_reviews(directory):
    """Count a synced LATEST copy and its result as one review attempt.

    Different full heads and retry timestamps remain distinct. The collector
    does not authenticate artifacts or grant merge approval.
    """
    unique = {}
    rank = {'PASS': 0, 'ERROR': 1, 'FAIL': 2}
    for path in list(directory.rglob('*.json'))[:2000]:
        if path.is_symlink():
            continue
        value = read_json(path)
        if not isinstance(value, dict) or not stamp(value.get('timestamp')):
            continue
        identity = (str(value.get('owner', '')), str(value.get('repo', '')),
                    str(value.get('pr_number', value.get('pr', ''))),
                    str(value.get('head_sha', value.get('sha', ''))), value['timestamp'])
        previous = unique.get(identity)
        if previous is None or rank[review_state(value)] > rank[review_state(previous)]:
            unique[identity] = value
    return list(unique.values())


LLM_SERVICES = ('codex', 'claude', 'gemini', 'minimax', 'cursor', 'grok')
PEER_FRESH_SEC = 900


def service_of(name):
    name = str(name or '').lower()
    return next((p for p in LLM_SERVICES if p in name), None)


def pid_state(pid):
    """Signal 0 only checks existence; it never touches the process."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return None


def provider_rules():
    try:
        from ..dispatcher import PROVIDER_RULES
        return PROVIDER_RULES
    except Exception:
        return {}


def running_row(job, now):
    start = stamp(job.get('start_time'))
    return {'job_id': job.get('job_id', ''), 'repo': job.get('repo', ''), 'pr_number': job.get('pr_number'),
            'provider': job.get('provider') or None, 'model': job.get('model') or None,
            'start_time': job.get('start_time') or None,
            'elapsed_sec': int(now - start) if start else None, 'process': None}


def merge_llm(local, peers):
    merged = []
    for gauge in local:
        out = dict(gauge, machines=[m for m in [gauge.get('machine')] if m])
        for peer in peers:
            other = next((g for g in peer if g.get('service') == gauge['service']), None)
            if not other:
                continue
            out['machines'].append(other.get('machine'))
            for key in ('active', 'calls_hour', 'jobs_hour', 'reviews_hour'):
                if other.get(key) is not None:
                    out[key] = (out.get(key) or 0) + other[key]
            if other.get('weekly_pct') is not None and (out.get('weekly_pct') is None or other['weekly_pct'] > out['weekly_pct']):
                out['weekly_pct'] = other['weekly_pct']
            if out.get('max_concurrent') is None and other.get('max_concurrent') is not None:
                out['max_concurrent'] = other['max_concurrent']
        out.pop('machine', None)
        merged.append(out)
    return merged


class EngineCollector:
    def __init__(self, data_dir, karen_dir, machine, budget_file=None):
        self.data_dir = Path(data_dir)
        self.budget_file = Path(budget_file) if budget_file else Path(
            os.environ.get('STORYPOOL_DATA_DIR', '~/.config/storypool')).expanduser() / 'budget_usage.json'

        self.karen_dir = Path(karen_dir)
        self.machine = machine
        self._cached = None
        self._at = 0

    def collect(self):
        # Browsers can poll independently without rescanning files per client.
        # _at == 0 means invalidated (macOS monotonic starts near 0 per process).
        if self._cached is not None and self._at and time.monotonic() - self._at < 2:
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
        heads = {}
        for job in latest:
            key = (job.get('repo'), job.get('pr_number'))
            if stamp(job.get('created_at')) >= stamp(heads.get(key, {}).get('created_at')):
                heads[key] = job
        blocked = [dict(repo=j.get('repo'), pr_number=j.get('pr_number'), head_sha=j.get('head_sha'),
                        reason=j.get('error'), timestamp=j.get('end_time'))
                   for j in heads.values() if j.get('state') == 'failed' and j.get('retryable') is False]
        # Dispatch owns durable blocker snapshots. An outage, midnight, or log
        # rotation cannot silently remove a shared exact-head block from view.
        for path in (self.data_dir / 'dispatch' / 'blocked-fixes').glob('*.json'):
            if path.is_symlink():
                continue
            value = blocked_snapshot(read_json(path))
            if value is None:
                continue
            key = (value['repo'], value['pr_number'])
            observed = heads.get(key, {})
            if (observed.get('head_sha') != value.get('head_sha')
                    and stamp(observed.get('created_at')) > stamp(value.get('timestamp'))):
                continue
            blocked = [b for b in blocked if (b['repo'], b['pr_number']) != key]
            blocked.append(value)
        dispatch_rows = list(rows(self.data_dir / 'dispatch' / ('dispatch-' + now.strftime('%Y-%m-%d') + '.jsonl')))
        for d in dispatch_rows[-100:]:
            if d.get('action') == 'skip_blocked' and stamp(d.get('timestamp')) >= hour:
                key = (d.get('repo'), d.get('pr_number'))
                if heads.get(key, {}).get('head_sha', d.get('head_sha')) == d.get('head_sha'):
                    blocked = [b for b in blocked if (b['repo'], b['pr_number']) != key]
                    blocked.append(dict(repo=d.get('repo'), pr_number=d.get('pr_number'),
                        head_sha=d.get('head_sha'), reason=d.get('reason'), timestamp=d.get('timestamp')))
        # A newer observed full head supersedes every prior blocker, including
        # an old same-day dispatch row when no job exists at the new head yet.
        observed_heads = {key: (stamp(j.get('created_at')), j.get('head_sha'))
                          for key, j in heads.items()}
        for d in dispatch_rows:
            if not d.get('live') or not d.get('head_sha'):
                continue
            key = (d.get('repo'), d.get('pr_number'))
            observed = (stamp(d.get('timestamp')), d.get('head_sha'))
            if observed[0] >= observed_heads.get(key, (0, ''))[0]:
                observed_heads[key] = observed
        blocked = [b for b in blocked if not (
            observed_heads.get((b['repo'], b['pr_number']), (0, b['head_sha']))[0] > stamp(b.get('timestamp'))
            and observed_heads[(b['repo'], b['pr_number'])][1] != b['head_sha'])]
        completed = [j for j in latest if j.get('state') == 'done' and j.get('outcome_verified')]
        jobs_hour = sum(stamp(j.get('end_time')) >= hour for j in completed)
        running = sum(j.get('state') == 'running' and stamp(j.get('lease_expires')) > now.timestamp() for j in latest)
        verdict_dir = self.karen_dir / 'verdicts'
        if not verdict_dir.is_dir():
            verdict_dir = self.data_dir / 'verdicts'
        verdicts = observed_reviews(verdict_dir)
        last_review = max((stamp(v.get('timestamp')) for v in verdicts), default=0)
        review_age = int(now.timestamp() - last_review) if 0 < last_review <= now.timestamp() else None
        review_evidence = {'age_seconds': review_age,
                           'state': 'recent' if review_age is not None and review_age < 900 else 'stale',
                           'reason': 'Recent Karen verdict evidence is available on this dashboard.'
                           if review_age is not None and review_age < 900 else
                           'No recent Karen verdict evidence here. Check the poller and verdict sync; this does not prove either machine is down.'}
        for value in verdicts:
            events.append({'timestamp': value.get('timestamp', ''), 'text': 'Karen {} #{}: {}'.format(
                value.get('repo', ''), value.get('pr', value.get('pr_number', '')), review_state(value))})
        recent = [v for v in verdicts if stamp(v.get('timestamp')) >= hour and review_state(v) != 'ERROR']
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
            evidence = [row for row in evidence if isinstance(row.get("checks"), list)]
            last = evidence[-1] if evidence else {}
            age = now.timestamp() - stamp(last.get('timestamp'))
            checks = last.get('checks', [])
            fresh = bool(checks) and 0 <= age < 1800
            failed = [c for c in checks if not c.get('passed') and not c.get('repaired')]
            state = ('red' if failed else 'green') if fresh else 'yellow'
            peer = read_json(self.data_dir / 'machines' / machine / 'engine.json') or {}
            sync_age = max(0, int(now.timestamp() - stamp(peer.get('synced_at')))) if peer.get('synced_at') else None
            detail = 'stale or unavailable' if not fresh else '{} failed checks'.format(len(failed))
            if machine != self.machine and not fresh and peer.get('synced_at'):
                detail = 'peer sync stale' if sync_age >= PEER_FRESH_SEC else 'peer reachable; health report stale'
            reported_state = state
            sync_status = (read_json(self.data_dir / 'machines' / machine / 'sync-status.json') or {}) if machine != self.machine else {}
            # Watchdog checks can still be recent after peer contact stops. Do
            # not present that old green report as current cluster health.
            # A newer snapshot supersedes a previous failed sync attempt.
            sync_failed = (sync_status.get('ok') is False and
                           stamp(sync_status.get('timestamp')) >= stamp(peer.get('synced_at')))
            sync_issue = ('peer sync failed' if sync_failed else
                          'peer sync stale' if sync_age is not None and sync_age >= PEER_FRESH_SEC else None)
            if machine != self.machine and state == 'yellow' and sync_failed:
                detail = 'peer sync failed; latest machine health unconfirmed'
            if machine != self.machine and state == 'green':
                if sync_failed:
                    state, detail = 'yellow', 'peer sync failed; last health report was green'
                elif sync_age is not None and sync_age >= PEER_FRESH_SEC:
                    state, detail = 'yellow', 'peer sync stale; last health report was green'
            health[machine] = {'state': state, 'timestamp': last.get('timestamp'),
                               'reported_state': reported_state, 'peer_sync': sync_status,
                               'sync_issue': sync_issue if machine != self.machine else None,
                               'detail': detail, 'sync_age_seconds': sync_age if machine != self.machine else None,
                               'guardian': (read_json(self.data_dir / 'guardian-status.json') or {})
                               if machine == self.machine else peer.get('guardian', {})}
            if health[machine]['guardian'].get('dashboard', {}).get('ok') is False:
                health[machine]['state'] = 'red'
                health[machine]['detail'] = 'dashboard HTTP restart failed; needs Jeff'
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
        from ..node_control import read_control
        control = read_control(self.data_dir)
        for name in health:
            peer = read_json(self.data_dir / 'machines' / name / 'engine.json') or {}
            node = control if name == self.machine else peer.get('control', {})
            if node.get('maintenance'):
                health[name].update(state='maintenance', detail=node.get('reason', 'Operator maintenance'))
        overall = 'red' if any(v['state'] == 'red' for v in health.values()) else ('green' if all(v['state'] in ('green', 'maintenance') for v in health.values()) else 'yellow')
        admission = {'state': 'unknown', 'reason': 'Work admission has no recent dispatcher report.'}
        if isinstance(dispatch, dict) and now.timestamp() - 900 <= stamp(dispatch.get('timestamp')) <= now.timestamp() + 60:
            decisions = dispatch.get('summary', {}).get('decisions', [])
            paused = any(d.get('action') == 'dispatch_failed' and 'coordination' in str(d.get('reason', '')).lower()
                         for d in decisions if isinstance(d, dict))
            admission = {'state': 'paused' if paused else 'ready',
                         'reason': 'New fixes paused: shared coordination unavailable. Verify broker permissions and retry storage.'
                         if paused else 'Latest dispatcher report has no coordination blocker.'}
            if paused and overall == 'green':
                overall = 'yellow'
        job_view, llm_local, llm = self._jobs_and_llm(latest, verdicts, verdict_dir.is_dir(), now.timestamp(), capacity)
        monitor = [line for line in tail(self.data_dir / 'MONITOR.md', 32768) if line.strip()]
        def gauge(label, value, maximum=None, unit=''):
            return {'label': label, 'value': value, 'max': maximum, 'unit': unit}
        return {'timestamp': now.isoformat(), 'engine': overall, 'machines': health, 'admission': admission,
                'review_evidence': review_evidence,
                'gauges': [gauge('Active lanes', running, capacity), gauge('Verified jobs / hour', jobs_hour),
                           gauge('Karen reviews / hour', len(recent)),
                           gauge('PASS rate · last hour', round(100 * sum(review_state(v) == 'PASS' for v in recent) / len(recent), 1) if recent else None, 100, '%'),
                           gauge('Merges today · UTC', merges if merge_state is not None else None, 30),
                           gauge('Fix success · last hour', round(100 * sum(j.get('state') == 'done' and bool(j.get('outcome_verified')) for j in attempts) / len(attempts), 1) if attempts else None, 100, '%'),
                           gauge('Backlog remaining', backlog_count)],
                'blocked_fixes': blocked,
                'jobs': job_view, 'llm_local': llm_local, 'llm': llm, 'control': control, 'local_machine': self.machine,
                'audit': tail(self.data_dir / 'HANDOFF.md', 16384)[-20:],
                'activity': sorted(events, key=lambda e: stamp(e['timestamp']), reverse=True)[:15],
                'monitor': monitor[-1] if monitor else 'No monitor status yet',
                'parity': next(iter(tail(self.data_dir / 'PARITY.md', 32768)), 'Parity audit unavailable'),
                'notes': 'Local ledger tail (2 MiB); up to 2000 verdict files, with synced copies counted once. Karen rates describe observed completed reviews; errors are retries, and these counts do not grant merge approval. Backlog is the last conductor snapshot. Unknown values display —. Rates use a rolling hour; merge day uses UTC (the gate enforces its cap over a rolling 24h).'}

    def _jobs_and_llm(self, latest, verdicts, have_verdicts, now, capacity):
        """Running jobs per machine plus per-LLM usage, from ledgers and verdicts only."""
        have_ledger = (self.data_dir / 'jobs').is_dir()
        hour = now - 3600
        live = [j for j in latest if j.get('state') == 'running' and stamp(j.get('lease_expires')) > now]
        rows_now = []
        for job in sorted(live, key=lambda j: stamp(j.get('start_time'))):
            row = running_row(job, now)
            row['process'] = pid_state(job.get('pid'))
            rows_now.append(row)
        counted = [r for r in rows_now if r['process'] is not False]
        from ..guards import NUCBOX_MAX_LANES
        local = {'source': 'local' if have_ledger else 'unavailable', 'synced_at': None,
                 'count': len(counted) if have_ledger else None, 'running': rows_now,
                 'capacity': capacity if capacity is not None else (NUCBOX_MAX_LANES if self.machine == 'nucbox' else None),
                 'reserved_codex': 2 if self.machine == 'nucbox' else None}
        names = {'mini', 'nucbox', self.machine}
        machines_dir = self.data_dir / 'machines'
        if machines_dir.is_dir():
            names.update(p.name for p in machines_dir.iterdir() if p.is_dir())
        view, peers = {}, []
        for name in sorted(names):
            if name == self.machine:
                view[name] = local
                continue
            snap = read_json(machines_dir / name / 'engine.json')
            jobs = snap.get('jobs') if isinstance(snap, dict) else None
            age = now - stamp(snap.get('synced_at')) if isinstance(snap, dict) else None
            if not isinstance(jobs, dict) or age is None or not 0 <= age < PEER_FRESH_SEC:
                view[name] = {'source': 'unavailable', 'synced_at': snap.get('synced_at') if isinstance(snap, dict) else None,
                              'count': None, 'running': [], 'capacity': None, 'reserved_codex': None}
                continue
            running = [dict(r, elapsed_sec=int(now - stamp(r.get('start_time'))) if stamp(r.get('start_time')) else None)
                       for r in jobs.get('running') or [] if isinstance(r, dict)]
            view[name] = dict(jobs, source='peer-sync', synced_at=snap.get('synced_at'), running=running)
            peers.append([dict(g, machine=name) for g in snap.get('llm_local') or [] if isinstance(g, dict)])
        rules = provider_rules()
        budget = read_json(self.budget_file)
        usage = budget.get('usage', {}) if isinstance(budget, dict) else {}
        weekly = read_json(self.karen_dir / 'codex_weekly_usage.json')
        llm_local = []
        for service in LLM_SERVICES:
            rule = rules.get(service, {})
            jobs_hour = sum(service_of(j.get('provider')) == service and
                            stamp(j.get('start_time') or j.get('created_at')) >= hour for j in latest) if have_ledger else None
            reviews_hour = None
            if have_verdicts:
                reviews_hour = 0
                for verdict in verdicts:
                    if stamp(verdict.get('timestamp')) < hour:
                        continue
                    results = verdict.get('review_results')
                    for result in results if isinstance(results, list) else []:
                        if isinstance(result, dict) and service_of(result.get('reviewer')) == service:
                            reviews_hour += 1
            calls = None if jobs_hour is None and reviews_hour is None else (jobs_hour or 0) + (reviews_hour or 0)
            week = usage.get(service) if isinstance(usage, dict) else None
            pct = None
            if service == 'codex' and isinstance(weekly, dict) and isinstance(weekly.get('usage_pct'), (int, float)) \
                    and now - float(weekly.get('week_start') or 0) < 7 * 86400:
                pct = round(float(weekly['usage_pct']), 1)
            llm_local.append({'service': service, 'machine': self.machine,
                              'active': sum(r['provider'] == service for r in counted) if have_ledger else None,
                              'max_concurrent': rule.get('max_concurrent'),
                              'calls_hour': calls, 'jobs_hour': jobs_hour, 'reviews_hour': reviews_hour,
                              'weekly_calls': week.get('calls') if isinstance(week, dict) else None,
                              'weekly_pct': pct, 'weekly_cap_pct': rule.get('weekly_cap_pct'),
                              'conserve': service == 'grok'})
        return view, llm_local, merge_llm(llm_local, peers)
