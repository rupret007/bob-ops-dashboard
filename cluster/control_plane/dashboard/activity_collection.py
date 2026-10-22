"""Dashboard feature implementation using the caller’s live namespace."""
from __future__ import annotations

def _collect_jobs(_runtime_bindings, self) -> tuple:
    'Collect job ledger data (OUTCOME-VERIFIED).\n        \n        Returns (running_jobs, recent_jobs, stats).\n        '
    running = []
    recent = []
    stats = {'total_jobs': 0, 'running': 0, 'queued': 0, 'done_24h': 0, 'failed_24h': 0, 'verified_24h': 0, 'jobs_last_hour': 0}
    if not _runtime_bindings['LEDGER_AVAILABLE']:
        return (running, recent, stats)
    try:
        ledger = _runtime_bindings['JobLedger']()
        stats = ledger.summary()
        for job in ledger.get_running_jobs():
            running.append(_runtime_bindings['JobRecord'](job_id=job.job_id, repo=job.repo, pr_number=job.pr_number, head_sha=job.head_sha[:8], provider=job.provider, machine=job.machine, state=job.state, outcome=job.outcome, outcome_verified=job.outcome_verified, pid=job.pid, start_time=job.start_time, end_time=job.end_time, error=job.error))
        for job in sorted(ledger.get_recent_jobs(24), key=lambda j: j.end_time or j.start_time or j.created_at, reverse=True)[:20]:
            recent.append(_runtime_bindings['JobRecord'](job_id=job.job_id, repo=job.repo, pr_number=job.pr_number, head_sha=job.head_sha[:8], provider=job.provider, machine=job.machine, state=job.state, outcome=job.outcome, outcome_verified=job.outcome_verified, pid=job.pid, start_time=job.start_time, end_time=job.end_time, error=job.error))
    except Exception:
        pass
    return (running, recent, stats)

def _collect_watchdog_events(_runtime_bindings, self) -> List[WatchdogEvent]:
    """Collect recent watchdog events."""
    events = []
    watchdog_log = self.data_dir / 'watchdog.log'
    from ..health_state import authoritative, read_status
    if authoritative(self.data_dir):
        group = read_status(self.data_dir).get('groups', {}).get('watchdog', {})
        entries = [dict(timestamp=_runtime_bindings['datetime'].fromtimestamp(group.get('finished_at', 0), _runtime_bindings['timezone'].utc).isoformat(), checks=group.get('checks', []))]
    else:
        entries = _runtime_bindings['_parse_jsonl'](watchdog_log, 20)
    for entry in entries:
        for check in entry.get('checks', []):
            events.append(_runtime_bindings['WatchdogEvent'](timestamp=entry.get('timestamp', ''), check_name=check.get('name', 'unknown'), passed=check.get('passed', True), message=check.get('message', '')[:80], repaired=check.get('repaired', False)))
    events.sort(key=lambda e: e.timestamp, reverse=True)
    return events[:30]
