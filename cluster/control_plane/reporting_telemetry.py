"""Read-only digest evidence. Unknown telemetry must never look like zero usage."""
from __future__ import annotations

import json
import math
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

from .budget_tracker import PROVIDERS
from .guards import MachineGuards, get_load1, get_swap_pct, get_free_disk_gb

FRESH_SECONDS = 900


def _stamp(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return 0


def _read(path):
    try:
        if path.stat().st_size > 2097152:
            return {}
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _jobs(data_dir):
    """Bound work and collapse repeated state rows by job id."""
    jobs = {}
    try:
        with (Path(data_dir) / 'jobs' / 'ledger.jsonl').open('rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 2097152))
            if size > 2097152:
                stream.readline()
            for line in stream:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict) and row.get('job_id'):
                        jobs[row['job_id']] = row
                except ValueError:
                    continue
    except OSError:
        pass
    return list(jobs.values())


def local_resources(data_dir, machine, now=None):
    """Sample this process's host exactly once; never sample a peer locally."""
    now = time.time() if now is None else now
    values = {}
    for name, getter in (('load', get_load1), ('swap', get_swap_pct), ('disk', get_free_disk_gb)):
        try:
            value = getter()
            values[name] = value if math.isfinite(value) else None
        except (OSError, ValueError):
            values[name] = None
    values['lanes'] = sum(j.get('machine') == machine and j.get('state') == 'running'
                          and _stamp(j.get('lease_expires')) > now for j in _jobs(data_dir))
    guards = MachineGuards(machine, load_fn=lambda: values['load'], swap_fn=lambda: values['swap'],
                           disk_fn=lambda: values['disk'], lane_count_fn=lambda: values['lanes'])
    required = ('load', 'swap', 'disk') if machine == 'mini' else ('load', 'lanes')
    values.update(machine=machine, timestamp=datetime.fromtimestamp(now, timezone.utc).isoformat(),
                  source='local', fresh=True,
                  passed=guards.check_all().passed if all(values[k] is not None for k in required) else None)
    return values


def machine_guard_snapshot(data_dir, machine=None, now=None):
    now = time.time() if now is None else now
    machine = machine or os.environ.get('STORYPOOL_MACHINE', 'mini' if platform.system() == 'Darwin' else 'nucbox')
    result = {}
    for name in ('mini', 'nucbox'):
        if name == machine:
            result[name] = local_resources(data_dir, name, now)
            continue
        peer = _read(Path(data_dir) / 'machines' / name / 'engine.json')
        resource = peer.get('resources')
        age = now - _stamp(resource.get('timestamp')) if isinstance(resource, dict) else None
        sync_age = now - _stamp(peer.get('synced_at'))
        fresh = (isinstance(resource, dict) and peer.get('machine') == name
                 and resource.get('machine') == name and resource.get('source') == 'local'
                 and age is not None and 0 <= age < FRESH_SECONDS and 0 <= sync_age < FRESH_SECONDS)
        result[name] = dict(resource, source='peer-sync', fresh=True, age_seconds=int(age)) if fresh else {
            'machine': name, 'load': None, 'swap': None, 'disk': None, 'lanes': None, 'passed': None,
            'source': 'unavailable', 'fresh': False, 'timestamp': resource.get('timestamp') if isinstance(resource, dict) else None,
            'reason': 'Peer resource report stale or unavailable'}
    return result


def lane_budget_snapshot(tracker, data_dir, verdict_dir, now=None):
    """Observed activity, not inferred token prices or subscription percentages.

    Old budget records contain flat per-call estimates. They remain available to
    existing enforcement, but cannot substantiate actual spend or plan quota.
    """
    now = time.time() if now is None else now
    cutoff = now - 86400
    have_jobs = (Path(data_dir) / 'jobs' / 'ledger.jsonl').is_file()
    have_reviews = Path(verdict_dir).is_dir()
    result = {p: dict(cost_usd=None, weekly_pct=v.get('weekly_pct') if p == 'codex' else None,
                       quota=v.get('quota') if p == 'codex' else None, jobs_24h=0 if have_jobs else None,
                       reviews_24h=0 if have_reviews else None,
                       cost_source='unavailable', source='local ledger + observed Karen artifacts (24h)',
                       allowed=v.get('allowed'), reason=v.get('reason'),
                       recorded_estimate_usd=tracker.get_usage(p).cost_usd, recorded_calls=v.get('calls'))
              for p, v in tracker.summary().items()}
    for job in _jobs(data_dir):
        provider = job.get('provider')
        if provider in result and cutoff <= _stamp(job.get('start_time')) <= now:
            result[provider]['jobs_24h'] += 1
    seen = set()
    for path in list(Path(verdict_dir).glob('**/*.json'))[:2000]:
        if path.is_symlink():
            continue
        row = _read(path)
        if not cutoff <= _stamp(row.get('timestamp')) <= now:
            continue
        identity = (row.get('owner'), row.get('repo'), row.get('pr_number'), row.get('head_sha'), row.get('timestamp'))
        if identity in seen:
            continue
        seen.add(identity)
        votes = row.get('review_results')
        for vote in votes if isinstance(votes, list) else []:
            name = str(vote.get('reviewer', '')).lower() if isinstance(vote, dict) else ''
            provider = next((p for p in PROVIDERS if p in name), None)
            if provider in result:
                result[provider]['reviews_24h'] += 1
    return result


def format_budget(provider, data):
    cost = data.get('cost_usd')
    spend = '${:.2f}'.format(cost) if isinstance(cost, (int, float)) else 'unknown (billing unavailable)'
    quota = data.get('weekly_pct')
    quota = '{:.1f}%'.format(quota) if isinstance(quota, (int, float)) else 'unknown'
    activity = '{} jobs, {} reviewer calls / 24h'.format(
        data.get('jobs_24h') if data.get('jobs_24h') is not None else '?',
        data.get('reviews_24h') if data.get('reviews_24h') is not None else '?')
    cap = '; blocked: {}'.format(data.get('reason', 'budget cap')) if data.get('allowed') is False else ''
    return '- {}: spend {}; weekly quota {}; {}; source: {}{}'.format(provider, spend, quota, activity, data.get('source', 'unavailable'), cap)


def format_machine(name, data):
    def value(key):
        v = data.get(key)
        return '?' if v is None else str(round(v, 2)) if isinstance(v, float) else str(v)
    return '- {}: load={}, swap={}%, lanes={}, passed={}; source={}, measured={}'.format(
        name, value('load'), value('swap'), value('lanes'), value('passed'), data.get('source', 'unavailable'),
        data.get('timestamp') or 'unknown')
