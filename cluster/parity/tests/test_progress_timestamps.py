"""R01 partial evidence: unrelated dispatches cannot fabricate recent activity."""
from unittest.mock import patch
from cluster.control_plane.job_ledger import JobLedger
from cluster.control_plane.dashboard.tachometer import EngineCollector


def test_unrelated_dispatch_preserves_terminal_evidence(tmp_path):
    ledger = JobLedger(tmp_path / 'jobs')
    old = '2026-01-01T00:00:00+00:00'
    with patch('cluster.control_plane.job_ledger._now_iso', return_value=old):
        first = ledger.create_job('owner/repo', 1, 'a' * 40, 'codex', 'nucbox')
        ledger.fail_job(first.job_id, 'historical failure')
    second = ledger.create_job('owner/repo', 2, 'b' * 40, 'codex', 'nucbox')
    before = {j.job_id: j.to_dict() for j in ledger._load_jobs()}
    ledger.start_job(second.job_id, 123)
    after = {j.job_id: j.to_dict() for j in ledger._load_jobs()}
    assert after[first.job_id] == before[first.job_id]
    assert after[first.job_id]['updated_at'] == old
    assert after[second.job_id]['state'] == 'running'
    assert after[second.job_id]['updated_at'] != before[second.job_id]['updated_at']
    snapshot = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()
    assert snapshot['activity'][-1]['timestamp'] == old
    assert snapshot['gauges'][5]['value'] is None
    # An idempotent save also must not fabricate activity.
    ledger._save_jobs(ledger._load_jobs())
    assert {j.job_id: j.to_dict() for j in ledger._load_jobs()} == after
