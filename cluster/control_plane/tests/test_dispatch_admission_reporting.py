"""Idle lanes must identify the admission stage that actually blocked work."""
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from cluster.control_plane import dispatcher, fix_worker
from cluster.control_plane.coordination import ClaimClient
from cluster.control_plane.job_ledger import JobLedger


def _cycle(tmp_path, *, no_provider=False, all_leased=False, spawn_failure=False, no_capacity=False):
    ledger = JobLedger(tmp_path / 'jobs')
    worker = dispatcher.Dispatcher('nucbox', tmp_path / 'dispatch', job_ledger=ledger,
                                   budget_tracker=MagicMock())
    prs = [dict(number=n, headRefOid=f'{n:040x}', isDraft=False) for n in range(1, 20)]
    client = MagicMock()
    client.acquire.side_effect = lambda repo, pr, sha, machine: dict(sha=sha)
    client.retry_state.side_effect = lambda claim, *write: (
        dict(attempts=3, code='transient', retry_after=0) if not write else None)

    def findings(repo, pr, sha):
        return ['repair'] if pr == 19 or spawn_failure else []

    with ExitStack() as stack:
        for target, kwargs in [
            ((worker, 'check_machine_guards'), {'return_value': (True, 'ok', {})}),
            ((worker, 'select_provider'), {'return_value': None if no_provider else 'codex'}),
            ((worker, 'check_provider_allowed'), {'return_value': (False, 'codex weekly plan quota unknown; fresh rate-limit observation required')}),
            ((ledger, 'has_active_lease'), {'return_value': all_leased}),
            ((dispatcher, 'REPOS'), {'new': ['owner/repo']}),
            ((dispatcher, '_get_open_prs'), {'return_value': prs}),
            ((dispatcher, '_get_active_lanes'), {'return_value': {}}),
            ((dispatcher, '_has_codex_work_queued'), {'return_value': False}),
            ((worker, 'get_nucbox_lane_capacity'), {'return_value': 0 if no_capacity else 7}),
            ((fix_worker, 'findings_for_pr'), {'side_effect': findings}),
            ((ClaimClient, 'from_env'), {'return_value': client}),
        ]:
            stack.enter_context(patch.object(*target, **kwargs))
        stack.enter_context(patch('cluster.control_plane.node_control.is_draining', return_value=False))
        if spawn_failure:
            client.retry_state.side_effect = None
            client.retry_state.return_value = None
        spawn = stack.enter_context(patch.object(dispatcher, '_spawn_worker',
            side_effect=OSError('worker unavailable') if spawn_failure else None))
        summary = worker.run(live=True)
    return summary, ledger, client, spawn


def test_missing_findings_and_retry_exhaustion_do_not_blame_providers(tmp_path):
    summary, ledger, client, spawn = _cycle(tmp_path)
    report = summary['decisions'][-1]
    assert report['reason'] == 'No work dispatched; see candidate admission reasons'
    assert report['extra']['candidate_count'] == 19
    assert report['extra']['considered_candidates'] == 19
    assert report['extra']['blocked_candidates'] == {
        'missing-reviewed-findings': 18, 'retry-blocked': 1}
    assert report['extra']['implemented_providers'] == ['codex', 'cursor']
    assert summary['skipped'] == 19 and summary['dispatched'] == 0
    assert ledger._load_jobs() == []
    spawn.assert_not_called()
    assert client.acquire.call_count == 1
    client.call.assert_called_once_with('release', {'sha': f'{19:040x}'})


def test_provider_rejection_explains_quota_failure_without_creating_jobs(tmp_path):
    summary, ledger, client, spawn = _cycle(tmp_path, no_provider=True)
    assert summary['decisions'][-1]['extra']['blocked_candidates'] == {'provider-unavailable': 19}
    assert summary['decisions'][0]['extra']['provider_rejections'] == {
        'codex': 'codex weekly plan quota unknown; fresh rate-limit observation required',
        'cursor': 'codex weekly plan quota unknown; fresh rate-limit observation required'}
    assert summary['skipped'] == 19 and ledger._load_jobs() == []
    client.acquire.assert_not_called()
    spawn.assert_not_called()


def test_active_leases_are_counted_once_and_never_reacquired(tmp_path):
    summary, ledger, client, spawn = _cycle(tmp_path, all_leased=True)
    assert summary['decisions'][-1]['extra']['blocked_candidates'] == {'active-lease': 19}
    assert summary['skipped'] == 19 and ledger._load_jobs() == []
    client.acquire.assert_not_called()
    spawn.assert_not_called()


def test_spawn_errors_are_reported_as_failures_not_successful_admission(tmp_path):
    summary, ledger, client, spawn = _cycle(tmp_path, spawn_failure=True)
    report = summary['decisions'][-1]
    assert report['extra']['blocked_candidates'] == {'spawn-failed': 3}
    assert report['extra']['candidate_count'] == 19
    assert report['extra']['considered_candidates'] == 3
    assert summary['dispatched'] == 0
    assert spawn.call_count == 3
    assert all(j.state == 'failed' for j in ledger._load_jobs())


def test_no_lane_capacity_is_distinct_from_missing_provider_or_findings(tmp_path):
    summary, ledger, client, spawn = _cycle(tmp_path, no_capacity=True)
    report = summary['decisions'][-1]
    assert report['extra']['blocked_candidates'] == {'lane-capacity-unavailable': 19}
    assert report['extra']['considered_candidates'] == 0
    assert summary['dispatched'] == 0 and ledger._load_jobs() == []
    client.acquire.assert_not_called()
    spawn.assert_not_called()
