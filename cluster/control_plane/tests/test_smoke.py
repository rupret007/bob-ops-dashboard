from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
from cluster.control_plane.smoke import verify


def job(**changes):
    fields = dict(job_id='real', repo='owner/repo', pr_number=1, created_at='now',
                  start_time='now', state='done', outcome='commit_pushed',
                  outcome_verified=True, head_sha='before', outcome_sha='after')
    return SimpleNamespace(**{**fields, **changes})


@patch('cluster.control_plane.smoke.load_verdict')
@patch('cluster.control_plane.smoke.run', return_value='{"headRefOid":"after"}')
def test_requires_exact_sha_verdict(run, verdict):
    verdict.return_value = SimpleNamespace(head_sha='before', passed=True)
    with pytest.raises(RuntimeError, match='exact-SHA'):
        verify(job(), None)
    verdict.return_value = SimpleNamespace(head_sha='after', passed=True, verdict_type='KARENPASS')
    verify(job(), None)


def test_failed_or_unverified_jobs_never_prove_pipeline():
    for changes in ({'state': 'failed'}, {'outcome_verified': False}, {'start_time': ''}):
        with pytest.raises(RuntimeError, match='queued'):
            verify(job(**changes), None)


@pytest.mark.parametrize('state', ['queued', 'running', 'failed', 'expired'])
def test_incomplete_job_reports_stage_without_worker_error(state, capsys):
    from cluster.control_plane import smoke
    ledger = MagicMock()
    ledger.get_recent_jobs.return_value = [job(state=state, error='private worker output')]
    with patch('sys.argv', ['smoke']), patch.object(smoke, 'get_ledger', return_value=ledger), \
            patch.object(smoke.KarenConfig, 'load'), patch.object(smoke, 'run') as command:
        assert smoke.main() == 1
    output = capsys.readouterr().out
    assert 'Job real (owner/repo#1)' in output
    assert f'state={state}' in output
    assert 'private worker output' not in output
    command.assert_not_called()


def test_hourly_check_does_not_hide_latest_failure_with_older_success():
    from cluster.control_plane import smoke
    ledger = MagicMock()
    ledger.get_recent_jobs.return_value = [
        job(job_id='old', created_at='2026-10-18T10:00:00+00:00'),
        job(job_id='latest', created_at='2026-10-18T11:00:00+00:00', state='failed')]
    with patch('sys.argv', ['smoke']), patch.object(smoke, 'get_ledger', return_value=ledger), \
            patch.object(smoke.KarenConfig, 'load'), patch.object(smoke, 'run') as command:
        assert smoke.main() == 1
    command.assert_not_called()
    ledger.get_recent_jobs.assert_called_once_with(hours=24)


@pytest.mark.parametrize('missing', [False, True])
def test_explicit_smoke_verifies_its_own_job_after_concurrent_dispatch(missing):
    from cluster.control_plane import smoke
    ledger = MagicMock()
    ledger.create_job.return_value = job(state='queued')
    completed = job(created_at='2026-10-18T10:00:00+00:00')
    concurrent = job(job_id='another', created_at='2026-10-18T11:00:00+00:00')
    ledger.get_jobs_for_pr.return_value = [concurrent] if missing else [completed, concurrent]
    with patch('sys.argv', ['smoke', '--repo', 'owner/repo', '--pr', '1']), \
            patch.object(smoke, 'get_ledger', return_value=ledger), \
            patch.object(smoke.KarenConfig, 'load'), \
            patch.object(smoke, 'run', return_value='{"headRefOid":"before"}'), \
            patch.object(smoke, 'execute', return_value=0) as execute, \
            patch('cluster.control_plane.local_karen.gate.run_gate'), \
            patch.object(smoke, 'verify') as verify_job:
        assert smoke.main() == (1 if missing else 0)
    execute.assert_called_once_with('real')
    if missing:
        verify_job.assert_not_called()
    else:
        assert verify_job.call_args.args[0] is completed


@patch('cluster.control_plane.smoke.load_verdict')
@patch('cluster.control_plane.smoke.run', return_value='{"headRefOid":"newer"}')
def test_changed_head_cannot_pass(run, verdict):
    with pytest.raises(RuntimeError, match='no longer matches'):
        verify(job(), None)
    verdict.assert_not_called()


@patch('cluster.control_plane.smoke.load_verdict')
@patch('cluster.control_plane.smoke.run', return_value='{"headRefOid":"after"}')
def test_exact_sha_failing_verdict_cannot_pass(run, verdict):
    verdict.return_value = SimpleNamespace(head_sha='after', passed=False, verdict_type='KARENFAIL')
    with pytest.raises(RuntimeError, match='Karen verdict is FAIL'):
        verify(job(), None)
