"""Measured feedback for each conductor cycle, never a new execution authority."""
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .health_state import atomic_json
from .retry_policy import REASONS, FAILURE_REASONS


def timestamp(value):
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return date.timestamp() if date.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def measure(rows, prs, now):
    latest = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get('job_id'), str):
            latest[row['job_id']] = row
    terminal, missing_time, stale = [], 0, 0
    for row in latest.values():
        if row.get('state') in ('queued', 'running'):
            expires = timestamp(row.get('lease_expires'))
            stale += int(expires is not None and expires < now)
        if row.get('state') not in ('done', 'failed', 'expired'):
            continue
        end = timestamp(row.get('end_time'))
        if end is None or end > now:
            missing_time += 1
        elif now - 86400 <= end <= now:
            terminal.append(row)
    verified = [r for r in terminal if r.get('state') == 'done' and
                r.get('outcome_verified') is True and r.get('outcome') == 'commit_pushed' and
                re.fullmatch('[0-9a-f]{40}', str(r.get('outcome_sha', '')))]
    failures = Counter()
    allowed = set(REASONS) | set(FAILURE_REASONS) | {'transient', 'stale-session', 'worker-died', 'lease-expired'}
    for row in terminal:
        if row not in verified:
            code = row.get('failure_code')
            failures[code if isinstance(code, str) and code in allowed else 'unclassified'] += 1
    updates = [timestamp(pr.get('updatedAt')) for pr in prs]
    return dict(terminal_jobs=len(terminal), verified_jobs=len(verified),
                verified_job_percent=round(100 * len(verified) / len(terminal), 2)
                if terminal and not missing_time else None,
                undated_terminal_jobs=missing_time, overdue_sessions=stale,
                backlog=len(prs), drafts=sum(pr.get('isDraft') is True for pr in prs),
                inactive_prs_48h=sum(t is not None and t <= now - 172800 for t in updates),
                undated_prs=sum(t is None or t > now for t in updates),
                top_failures=[dict(code=c, count=n) for c, n in failures.most_common(5)])


def refresh(root, prs, now=None):
    root, now = Path(root), time.time() if now is None else now
    path = root / 'jobs/ledger.jsonl'
    coverage = 'complete'
    rows = []
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Oversized ledger')
        for line in path.read_text().splitlines():
            if line.strip():
                try:
                    row = json.loads(line)
                    if (not isinstance(row, dict) or not isinstance(row.get('job_id'), str)
                            or row.get('state') not in ('queued', 'running', 'done', 'failed', 'expired')):
                        raise ValueError('Incomplete job evidence')
                    rows.append(row)
                except ValueError:
                    coverage = 'incomplete'
    except (OSError, ValueError):
        coverage = 'unavailable'
    metrics = measure(rows, prs, now)
    if coverage != 'complete':
        metrics['verified_job_percent'] = None
    recommendations = []
    if coverage != 'complete' or metrics['undated_terminal_jobs']:
        recommendations.append('Repair missing outcome evidence before evaluating improvement.')
    if metrics['overdue_sessions']:
        recommendations.append('Investigate overdue sessions; confirm old worker exit before retrying.')
    for failure in metrics['top_failures'][:3]:
        recommendations.append('Investigate repeated {} outcomes ({}); add a regression check before changing behavior.'.format(
            failure['code'], failure['count']))
    if metrics['inactive_prs_48h']:
        recommendations.append('Prioritize finishing existing work and resolving PRs inactive for 48 hours.')
    report = dict(schema=1, observed_at=now, window_seconds=86400,
                  scope='local job ledger and managed GitHub backlog snapshot',
                  coverage=coverage, metrics=metrics, recommendations=recommendations,
                  success='unproven',
                  evaluation='Tests permit rollout; production outcomes must improve without safety regressions.')
    directory = root / 'conductor'
    baseline_path = directory / 'improvement-baseline.json'
    try:
        baseline = json.loads(baseline_path.read_text())
        if not isinstance(baseline, dict) or not isinstance(baseline.get('metrics'), dict):
            raise ValueError('Invalid baseline')
    except (OSError, ValueError):
        baseline = report
        atomic_json(baseline_path, baseline)
    report['baseline_observed_at'] = baseline.get('observed_at')
    previous = baseline.get('metrics', {})
    current_rate, previous_rate = metrics['verified_job_percent'], previous.get('verified_job_percent')
    if (type(baseline.get('observed_at')) in (int, float) and
            now - baseline['observed_at'] >= 86400 and
            metrics['terminal_jobs'] >= 10 and previous.get('terminal_jobs', 0) >= 10 and
            current_rate is not None and type(previous_rate) in (int, float)):
        report['verified_job_percent_delta'] = round(current_rate - previous_rate, 2)
        report['job_outcome_trend'] = ('improving' if current_rate > previous_rate else
                                       'regressing' if current_rate < previous_rate else 'unchanged')
        # One metric improving is not proof of overall release success.
        if current_rate < previous_rate:
            report['recommendations'].insert(0, 'Verified job rate regressed; hold further rollout and investigate rollback.')
    else:
        report['job_outcome_trend'] = 'insufficient_observation'
    atomic_json(directory / 'improvement.json', report)
    day = datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d')
    history = directory / ('improvement-' + day + '.jsonl')
    with history.open('a') as stream:
        stream.write(json.dumps(report, sort_keys=True) + '\n')
    history.chmod(0o600)
    for old in directory.glob('improvement-????-??-??.jsonl'):
        if old != history and old.stat().st_mtime < now - 7 * 86400:
            old.unlink()
    return report
