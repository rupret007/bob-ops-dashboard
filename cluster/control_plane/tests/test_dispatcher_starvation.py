"""Skipped PRs must not starve actionable work behind the old shortlist."""
from unittest.mock import MagicMock, patch

import pytest

from cluster.control_plane import dispatcher, fix_worker
from cluster.control_plane.coordination import ClaimClient
from cluster.control_plane.job_ledger import JobLedger


@pytest.mark.parametrize('skip_reason', ['no-findings', 'leased', 'blocked'])
def test_dispatch_looks_past_skipped_prs_without_exceeding_tick_capacity(tmp_path, skip_reason):
    ledger = JobLedger(tmp_path / 'jobs')
    worker = dispatcher.Dispatcher('nucbox', tmp_path / 'dispatch', job_ledger=ledger,
                                   budget_tracker=MagicMock())
    prs = [dict(number=n, headRefOid=f'{n:040x}', isDraft=False) for n in range(1, 20)]
    client = MagicMock()
    client.acquire.side_effect = lambda repo, pr, sha, machine: dict(sha=sha)
    client.retry_state.side_effect = lambda claim, *write: (
        dict(attempts=1, code='missing-lockfile', retry_after=0)
        if not write and int(claim['sha'], 16) <= 15 and skip_reason == 'blocked' else None)

    def findings(repo, pr, sha):
        return [] if pr <= 15 and skip_reason == 'no-findings' else ['repair']

    with patch.object(worker, 'check_machine_guards', return_value=(True, 'ok', {})), \
         patch.object(worker, 'select_provider', return_value='codex'), \
         patch.object(ledger, 'has_active_lease', side_effect=lambda repo, pr: pr <= 15 and skip_reason == 'leased'), \
         patch.object(dispatcher, 'REPOS', ['owner/repo']), \
         patch.object(dispatcher, '_get_open_prs', return_value=prs) as get_prs, \
         patch.object(dispatcher, '_get_active_lanes', return_value={}), \
         patch.object(dispatcher, '_has_codex_work_queued', return_value=False), \
         patch('cluster.control_plane.node_control.is_draining', return_value=False), \
         patch.object(fix_worker, 'findings_for_pr', side_effect=findings), \
         patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(dispatcher, '_spawn_worker', return_value=0) as spawn:
        decisions = worker.evaluate(live=True)

    assert [d.pr_number for d in decisions if d.action == 'dispatch'] == [16, 17, 18]
    assert spawn.call_count == 3
    assert len(ledger._load_jobs()) == 3
    get_prs.assert_called_once_with('owner/repo')
