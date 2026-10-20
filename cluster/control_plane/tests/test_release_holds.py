"""Owner release holds remain binding after exact Karen and CI approval."""
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane import conductor as c, github_reads
from cluster.control_plane.github_budget import GitHubDeferred
from cluster.control_plane.release_holds import explicitly_held
from cluster.never_touch import guard_pr_merge

SHA = 'a' * 40
REPO = 'owner/project'


def pr(**changes):
    return dict(dict(repo=REPO, number=7, state='OPEN', isDraft=False,
                     headRefOid=SHA, title='[HELD] Keep this build parked', labels=[],
                     mergeStateStatus='CLEAN', reviews=[],
                     statusCheckRollup=[dict(name='tests', conclusion='SUCCESS')]), **changes)


def worker(tmp_path):
    return c.Conductor(log_dir=tmp_path/'logs', verdict_dir=tmp_path/'verdicts',
                       budget_tracker=Mock(), metrics_tracker=Mock())


def verdict():
    return dict(verdict='KARENPASS', sha=SHA, _latest=True,
                review_results=[dict(reviewer=name, verdict='PASS')
                                for name in ('gemini', 'minimax')])


def test_held_head_cannot_validate_for_merge_with_exact_pass_and_green_ci(tmp_path):
    instance = worker(tmp_path)
    with patch.object(instance, '_check_merge_rate_limit', return_value=(True, 'ok')), \
         patch.object(c, '_read_verdict', return_value=verdict()), \
         patch.object(c, '_get_pr_details', return_value=pr()):
        result = instance.validate_action(c.ProposedAction('merge_pr', repo=REPO,
                                         pr_number=7, head_sha=SHA), None)
    assert not result.validated
    assert 'hold' in result.validation_reason.lower()


def test_held_draft_is_not_automatically_readied(tmp_path):
    instance = worker(tmp_path)
    snapshot = SimpleNamespace(open_prs=[pr(isDraft=True)])
    assert instance.deterministic_review_pass(snapshot, live=False) == []


def test_manual_merge_guard_refuses_current_held_head():
    with patch.object(github_reads, 'run', return_value=SimpleNamespace(
            returncode=0, stdout=json.dumps(pr()))) as request:
        with pytest.raises(PermissionError):
            guard_pr_merge(REPO, 7, SHA)
    assert request.call_count == 1


@pytest.mark.parametrize('title', ['[HELD] parked', ' [hold] parked', '[DO NOT MERGE] parked',
                                  'Held: parked', 'DO-NOT-MERGE parked'])
def test_explicit_title_markers_are_release_holds(title):
    assert explicitly_held(pr(title=title))


@pytest.mark.parametrize('label', ['hold', 'HELD', 'release-hold', 'do not merge', 'do_not_merge'])
def test_explicit_label_markers_are_release_holds(label):
    assert explicitly_held(pr(title='Ready', labels=[dict(name=label)]))


@pytest.mark.parametrize('title', ['Threshold setting', 'Handle keyboard hold duration', 'Ready'])
def test_ordinary_titles_are_not_release_holds(title):
    assert not explicitly_held(pr(title=title))


@pytest.mark.parametrize('change', [dict(state='CLOSED'), dict(isDraft=True), dict(isDraft=None),
    dict(number=True), dict(number=8), dict(headRefOid='b'*40), dict(title=None),
    dict(labels=None), dict(labels=['hold']), dict(labels=[dict(name=None)])])
def test_missing_or_changed_current_metadata_cannot_authorize_merge(change):
    with patch.object(github_reads, 'run', return_value=SimpleNamespace(
            returncode=0, stdout=json.dumps(pr(**dict(dict(title='Ready'), **change))))):
        with pytest.raises(PermissionError):
            guard_pr_merge(REPO, 7, SHA)


@pytest.mark.parametrize('output,code', [('{', 0), ('[]', 0), ('', 1), (None, 0)])
def test_failed_or_malformed_metadata_never_authorizes_merge(output, code):
    with patch.object(github_reads, 'run', return_value=SimpleNamespace(returncode=code, stdout=output)):
        with pytest.raises(PermissionError):
            guard_pr_merge(REPO, 7, SHA)


def test_budget_deferral_is_preserved_without_release():
    with patch.object(github_reads, 'run', side_effect=GitHubDeferred('fixture', code='minute-exhausted')):
        with pytest.raises(GitHubDeferred):
            guard_pr_merge(REPO, 7, SHA)


@pytest.mark.parametrize('error', [OSError('fixture'), subprocess.TimeoutExpired(['gh'], 15)])
def test_transport_failure_defers_without_a_release_or_merge_failure(error):
    with patch.object(github_reads, 'run', side_effect=error):
        with pytest.raises(PermissionError, match='metadata unavailable'):
            guard_pr_merge(REPO, 7, SHA)


def test_hold_added_after_cached_discovery_blocks_live_readiness(tmp_path):
    instance = worker(tmp_path)
    snapshot = SimpleNamespace(open_prs=[pr(title='Ready', isDraft=True)])
    with patch.object(c.ClaimClient, 'from_env'), patch.object(c, 'Lease'), \
         patch.object(c, '_get_pr_details', return_value=pr(isDraft=True)), \
         patch.object(github_reads, 'run') as request:
        results = instance.deterministic_review_pass(snapshot, live=True)
    assert not results[0].executed
    assert 'hold' in results[0].execution_result.lower()
    request.assert_not_called()


@pytest.mark.parametrize('current', [pr(), pr(title='Ready', labels=[dict(name='hold')]),
                                     pr(title='Ready', headRefOid='b'*40)])
def test_changed_hold_or_head_after_main_ci_blocks_the_actual_merge_command(tmp_path, current):
    instance = worker(tmp_path)
    instance.validate_action = Mock(side_effect=lambda proposal, _: c.ValidatedAction(proposal, True, 'early gate'))
    instance._check_main_ci_after_merge = Mock(return_value=(True, 'success'))
    instance._record_merge = Mock()
    action = c.ValidatedAction(c.ProposedAction('merge_pr', repo=REPO, pr_number=7, head_sha=SHA), True, 'early gate')
    with patch.object(c.ClaimClient, 'from_env') as claims, patch.object(c, 'Lease'), \
         patch.object(github_reads, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(current))) as request, \
         patch.object(c.subprocess, 'run') as merge:
        result = instance.execute_action(action, live=True)
    assert not result.executed
    assert 'deferred' in result.execution_result
    assert claims.return_value.acquire.call_args.args[:3] == (REPO, 7, SHA)
    instance._check_main_ci_after_merge.assert_called_once_with(REPO)
    assert request.call_count == 1
    merge.assert_not_called()
    instance._record_merge.assert_not_called()
    instance.metrics.record_merge.assert_not_called()


def test_unheld_current_head_still_merges_with_existing_claim_and_head_fence(tmp_path):
    instance = worker(tmp_path)
    instance.validate_action = Mock(side_effect=lambda proposal, _: c.ValidatedAction(proposal, True, 'early gate'))
    instance._check_main_ci_after_merge = Mock(return_value=(True, 'success'))
    instance._record_merge = Mock()
    action = c.ValidatedAction(c.ProposedAction('merge_pr', repo=REPO, pr_number=7, head_sha=SHA), True, 'early gate')
    with patch.object(c.ClaimClient, 'from_env'), patch.object(c, 'Lease'), \
         patch.object(github_reads, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(pr(title='Ready')))), \
         patch.object(c.subprocess, 'run') as merge, \
         patch('cluster.control_plane.main_ci.merged_pr', return_value={}), \
         patch('cluster.control_plane.main_ci.observe', return_value='pending'):
        result = instance.execute_action(action, live=True)
    assert result.executed
    assert merge.call_count == 1
    assert merge.call_args.args[0][-2:] == ['--match-head-commit', SHA]
