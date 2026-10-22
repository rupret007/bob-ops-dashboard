"""Deterministic review admission must feed Karen without author approval."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane import conductor as c
from cluster.control_plane.coordination import CoordinationError

SHA = 'a' * 40
REPO = 'owner/repo'


def pr(**changes):
    return dict(repo=REPO, number=7, headRefOid=SHA, state='OPEN', isDraft=True,
                labels=[], statusCheckRollup=[{'name': 'unit', 'conclusion': 'SUCCESS'}],
                **changes)


@pytest.fixture
def runner(tmp_path):
    worker = c.Conductor(log_dir=tmp_path / 'logs', verdict_dir=tmp_path / 'verdicts',
                         budget_tracker=Mock(), metrics_tracker=Mock(),
                         codex_client=Mock(), gemini_client=Mock())
    return worker


@pytest.fixture
def owned():
    with patch.object(c.ClaimClient, 'from_env') as factory, patch.object(c, 'Lease') as lease:
        yield factory.return_value, lease.return_value.__enter__.return_value


def test_green_draft_is_readied_and_queued_without_approval(runner, owned):
    with patch.object(c, '_get_pr_details', side_effect=[pr(), dict(pr(), isDraft=False)]), \
         patch.object(c.github_reads, 'run', return_value=SimpleNamespace(returncode=0)) as gh, \
         patch.object(c, 'ensure_label_exists'):
        results = runner.deterministic_review_pass(SimpleNamespace(open_prs=[pr()]), live=True)
    assert len(results) == 1 and results[0].executed
    assert results[0].proposal.head_sha == SHA
    assert [call.args[0][:2] for call in gh.call_args_list] == [['pr', 'ready'], ['pr', 'edit']]
    assert owned[0].acquire.call_args.args[:3] == (REPO, 7, SHA)


@pytest.mark.parametrize('changes', [
    {'headRefOid': 'b' * 40}, {'headRefOid': 'short'}, {'state': 'CLOSED'},
    {'statusCheckRollup': []},
    {'statusCheckRollup': [{'name': 'unit', 'conclusion': 'FAILURE'}]},
    {'statusCheckRollup': [{'name': 'unit', 'conclusion': 'SUCCESS', 'status': 'IN_PROGRESS'}]},
])
def test_fresh_head_and_nonempty_green_ci_required(runner, owned, changes):
    with patch.object(c, '_get_pr_details', return_value=dict(pr(), **changes)), \
         patch.object(c.github_reads, 'run') as gh:
        results = runner.deterministic_review_pass(SimpleNamespace(open_prs=[pr()]), live=True)
    assert not results[0].executed
    gh.assert_not_called()


def test_head_change_after_ready_does_not_queue_stale_work(runner, owned):
    with patch.object(c, '_get_pr_details', side_effect=[pr(), dict(pr(), headRefOid='b' * 40, isDraft=False)]), \
         patch.object(c.github_reads, 'run', return_value=SimpleNamespace(returncode=0)) as gh:
        results = runner.deterministic_review_pass(SimpleNamespace(open_prs=[pr()]), live=True)
    assert not results[0].executed
    assert [call.args[0][:2] for call in gh.call_args_list] == [['pr', 'ready']]


def test_coordination_outage_does_not_change_draft_or_label(runner, owned):
    owned[0].acquire.side_effect = CoordinationError('offline')
    with patch.object(c.github_reads, 'run') as gh:
        results = runner.deterministic_review_pass(SimpleNamespace(open_prs=[pr()]), live=True)
    assert not results[0].executed
    gh.assert_not_called()


@pytest.mark.parametrize('repo', ['rupret007/rad-dad-show-night', 'rupret007/HangOut', 'rupret007/bob-brain', 'rupret007/web-bob'])
def test_never_touch_and_manual_readiness_stay_parked(runner, owned, repo):
    with patch.object(c.github_reads, 'run') as gh:
        assert runner.deterministic_review_pass(SimpleNamespace(open_prs=[dict(pr(), repo=repo)]), live=True) == []
    owned[0].acquire.assert_not_called()
    gh.assert_not_called()


def test_shadow_has_no_claims_or_writes(runner, owned):
    with patch.object(c.github_reads, 'run') as gh:
        results = runner.deterministic_review_pass(SimpleNamespace(open_prs=[pr()]), live=False)
    assert not results[0].executed
    owned[0].acquire.assert_not_called()
    gh.assert_not_called()


def test_ready_but_unlabelled_recovers_partial_attempt(runner, owned):
    ready = dict(pr(), isDraft=False)
    with patch.object(c, '_get_pr_details', return_value=ready), \
         patch.object(c.github_reads, 'run', return_value=SimpleNamespace(returncode=0)) as gh, \
         patch.object(c, 'ensure_label_exists'):
        assert runner.deterministic_review_pass(SimpleNamespace(open_prs=[ready]), live=True)[0].executed
    assert [call.args[0][:2] for call in gh.call_args_list] == [['pr', 'edit']]


@pytest.mark.parametrize('evidence_error', [None, OSError('evidence storage unavailable')])
def test_admission_runs_before_optional_model_and_merge(runner, owned, evidence_error):
    order = []
    snapshot = SimpleNamespace(recent_errors=[], open_prs=[])
    runner.build_state_snapshot = Mock(return_value=snapshot)
    runner.deterministic_review_pass = Mock(side_effect=lambda *a, **kw: order.append('review') or [])
    runner.deterministic_merge_pass = Mock(side_effect=lambda *a, **kw: order.append('merge') or [])
    runner.get_llm_proposals = Mock(side_effect=lambda *a, **kw: order.append('llm') or {})
    runner.write_digest = Mock()
    with patch('cluster.control_plane.node_control.is_draining', return_value=False), \
         patch('cluster.control_plane.improvement.refresh', side_effect=evidence_error,
               return_value={'success': 'unproven'}) as evidence:
        runner.run(live=True)
    assert order == ['review', 'merge', 'llm']
    evidence.assert_called_once_with(runner.log_dir.parent, snapshot.open_prs)
    assert snapshot.improvement['success'] == 'unproven'
    if evidence_error:
        assert snapshot.improvement['coverage'] == 'unavailable'


def test_bounded_admission_and_label_failure_are_retryable(runner, owned):
    candidates = [dict(pr(), number=n) for n in range(1, 20)]
    with patch.object(c, '_get_pr_details', return_value=dict(pr(), isDraft=False)), \
         patch.object(c.github_reads, 'run', return_value=SimpleNamespace(returncode=1)), \
         patch.object(c, 'ensure_label_exists'):
        results = runner.deterministic_review_pass(SimpleNamespace(open_prs=candidates), live=True)
    assert len(results) == c.MAX_UNDRAFTS_PER_TICK
    assert all(not r.executed and 'retry' in r.execution_result for r in results)


def test_existing_needs_karen_non_draft_is_not_relabelled(runner, owned):
    ready = dict(pr(), isDraft=False, labels=[{'name': 'needs-karen'}])
    assert runner.deterministic_review_pass(SimpleNamespace(open_prs=[ready]), live=True) == []
    owned[0].acquire.assert_not_called()


def test_dashboard_repo_is_reviewable_but_remains_nonmergeable(runner, owned):
    candidate = dict(pr(), repo='rupret007/bob-ops-dashboard')
    assert not c.check_merge_allowed(candidate['repo'])[0]
    with patch.object(c, '_get_pr_details', side_effect=[candidate, dict(candidate, isDraft=False)]), \
         patch.object(c.github_reads, 'run', return_value=SimpleNamespace(returncode=0)), \
         patch.object(c, 'ensure_label_exists'):
        assert runner.deterministic_review_pass(SimpleNamespace(open_prs=[candidate]), live=True)[0].executed


def test_partial_ready_failure_does_not_label(runner, owned):
    with patch.object(c, '_get_pr_details', return_value=pr()), \
         patch.object(c.github_reads, 'run', return_value=SimpleNamespace(returncode=1)) as gh:
        assert not runner.deterministic_review_pass(SimpleNamespace(open_prs=[pr()]), live=True)[0].executed
    assert len(gh.call_args_list) == 1
