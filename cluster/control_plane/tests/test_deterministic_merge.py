"""Regression coverage for Karen's production disk contract and live merge rails."""
import json
import plistlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane import conductor as c

SHA = 'a' * 40
REPO = 'owner/repo'


def artifact(tmp_path, suffix='LATEST', passed='True', timestamp='2026-10-18T19:30:00+00:00', **changes):
    data = dict(owner='owner', repo='repo', pr_number=7, head_sha=SHA,
                passed=passed, timestamp=timestamp, status='COMPLETE',
                review_results=[{'reviewer': 'codex', 'verdict': 'PASS'},
                                {'reviewer': 'gemini', 'verdict': 'PASS'}])
    data.update(changes)
    (tmp_path / f'owner_repo_7_{SHA[:12]}_{suffix}.json').write_text(json.dumps(data))
    return data


def runner(tmp_path):
    return c.Conductor(log_dir=tmp_path/'logs', verdict_dir=tmp_path,
                       budget_tracker=Mock(), metrics_tracker=Mock())


def pr(**changes):
    return dict(dict(repo=REPO, number=7, headRefOid=SHA, state='OPEN', isDraft=False,
                     mergeStateStatus='CLEAN', reviews=[],
                     statusCheckRollup=[{'name': 'tests', 'conclusion': 'SUCCESS'}]), **changes)


def validate(tmp_path, details=None, repo=REPO):
    worker = runner(tmp_path)
    with patch.object(c, '_get_pr_details', return_value=details or pr()):
        return worker.validate_action(c.ProposedAction('merge_pr', repo=repo,
                                      pr_number=7, head_sha=SHA), None)


def test_flat_latest_string_true_is_approval_without_github_review(tmp_path):
    artifact(tmp_path)
    verdict = c._read_flat_verdict(tmp_path, REPO, 7, SHA)
    assert verdict['verdict'] == 'KARENPASS' and verdict['sha'] == SHA
    assert validate(tmp_path).validated


def test_stale_fail_does_not_override_newer_latest_pass(tmp_path):
    artifact(tmp_path, 'KARENFAIL', passed=False, timestamp='2026-10-18T18:00:00+00:00')
    artifact(tmp_path, 'KARENPASS')
    artifact(tmp_path)
    assert validate(tmp_path).validated
    assert len(c._list_verdicts(tmp_path)) == 1


@pytest.mark.parametrize('stamp', ['2026-10-18T19:31:00+00:00', '2026-10-18T19:30:00+00:00'])
def test_newer_or_equal_conflicting_fail_blocks_pass(tmp_path, stamp):
    artifact(tmp_path)
    artifact(tmp_path, 'KARENFAIL', passed=False, timestamp=stamp)
    assert not validate(tmp_path).validated


def test_mismatch_full_sha_with_same_filename_prefix_rejected(tmp_path):
    artifact(tmp_path, head_sha=SHA[:12] + 'b'*28)
    assert c._read_flat_verdict(tmp_path, REPO, 7, SHA) is None
    assert not validate(tmp_path).validated


@pytest.mark.parametrize('changes', [dict(owner='other'), dict(pr_number=8),
    dict(timestamp='invalid'), dict(status='ERROR'), dict(passed='False'),
    dict(review_results=[{'reviewer':'codex','verdict':'PASS'}]),
    dict(review_results=[{'reviewer':'codex','verdict':'PASS'}]*2),
    dict(review_results=[{'reviewer':'codex','verdict':'PASS'}, {'reviewer':'gemini','verdict':'FAIL'}])])
def test_incomplete_untrusted_or_disagreeing_review_cannot_approve(tmp_path, changes):
    artifact(tmp_path, **changes)
    assert not validate(tmp_path).validated


@pytest.mark.parametrize('changes,reason', [
    (dict(isDraft=True), 'draft'),
    (dict(headRefOid='b'*40), 'SHA mismatch'),
    (dict(mergeStateStatus='DIRTY'), 'CLEAN'),
    (dict(statusCheckRollup=[{'conclusion':'FAILURE'}]), 'CI not passing'),
    (dict(statusCheckRollup=[{'status':'IN_PROGRESS'}]), 'CI not passing'),
    (dict(statusCheckRollup=[]), 'CI not passing'),
    (dict(state='CLOSED'), 'no longer open')])
def test_live_github_gates(tmp_path, changes, reason):
    artifact(tmp_path)
    details = pr(); details.update(changes)
    result = validate(tmp_path, details)
    assert not result.validated and reason in result.validation_reason


def test_never_touch_cannot_enter_merge_gate(tmp_path):
    artifact(tmp_path)
    assert not validate(tmp_path, repo='rupret007/rad-dad-show-night').validated


def test_missing_latest_or_corrupt_latest_cannot_use_pass_sibling(tmp_path):
    artifact(tmp_path, 'KARENPASS')
    assert not validate(tmp_path).validated
    (tmp_path / f'owner_repo_7_{SHA[:12]}_LATEST.json').write_text('{')
    assert not validate(tmp_path).validated


def test_instance_directory_cannot_fall_back_to_another_nodes_pass(tmp_path):
    other = tmp_path / 'other'; other.mkdir(); artifact(other)
    with patch.object(c, 'VERDICT_DIR', other):
        assert not validate(tmp_path).validated


def test_deterministic_pass_runs_before_and_without_llm(tmp_path):
    artifact(tmp_path)
    worker = runner(tmp_path)
    snapshot = c.StateSnapshot(c._now_iso(), [REPO], [pr()], {}, [], {}, {}, {}, [], [])
    worker.build_state_snapshot = Mock(return_value=snapshot)
    worker.get_llm_proposals = Mock(side_effect=lambda *args, **kwargs: None)
    worker.write_digest = Mock()
    worker._can_use_codex = Mock(return_value=(False, "unavailable"))
    worker._can_use_gemini = Mock(return_value=(False, "unavailable"))
    worker.execute_action = Mock(side_effect=lambda result, **kwargs: result)
    with patch.object(c, '_get_pr_details', return_value=pr()), \
         patch('cluster.control_plane.node_control.is_draining', return_value=False):
        summary = worker.run(live=True)
    assert not summary['llm_available']
    worker.execute_action.assert_called_once()
    assert worker.execute_action.call_args.kwargs['live']


def test_rate_limit_still_blocks_deterministic_pass(tmp_path):
    artifact(tmp_path)
    worker = runner(tmp_path)
    worker._check_merge_rate_limit = Mock(return_value=(False, 'Rate limit reached'))
    worker.execute_action = Mock()
    results = worker.deterministic_merge_pass(SimpleNamespace(open_prs=[pr()]), live=True)
    assert not results[0].validated
    worker.execute_action.assert_not_called()


def test_mini_plist_has_one_real_live_argument_and_installer_kickstart():
    install = Path(c.__file__).parent / 'install'
    data = plistlib.loads((install/'com.storypool.conductor.plist').read_bytes())
    assert data['ProgramArguments'].count('--live') == 1
    block = (install/'install-all-mini.sh').read_text().split('install_conductor()')[1].split('install_watchdog()')[0]
    assert 'launchctl kickstart -k "gui/$(id -u)/com.storypool.conductor"' in block


def test_newer_pass_cannot_bypass_latest_fail_marker(tmp_path):
    artifact(tmp_path, passed=False)
    artifact(tmp_path, 'KARENPASS', timestamp='2026-10-18T19:31:00+00:00')
    assert not validate(tmp_path).validated


def test_legacy_artifact_cannot_authorize_merge(tmp_path):
    data = artifact(tmp_path)
    (tmp_path / f'owner_repo_7_{SHA[:12]}_LATEST.json').unlink()
    legacy = tmp_path / 'owner_repo'; legacy.mkdir()
    (legacy / f'pr-7-{SHA[:12]}.json').write_text(json.dumps(
        dict(data, sha=SHA, verdict='KARENPASS', _latest=True)))
    assert not validate(tmp_path).validated


def test_snapshot_discovers_owner_repos_and_excludes_protected_before_fetch(tmp_path):
    worker = runner(tmp_path)
    worker.budget.summary.return_value = {}
    repos = [{'nameWithOwner': 'rupret007/new-project', 'isArchived': False},
             {'nameWithOwner': 'rupret007/rad-dad-show-night', 'isArchived': False},
             {'nameWithOwner': 'rupret007/archived', 'isArchived': True}]
    with patch.object(c, '_run_gh', return_value=repos), \
         patch.object(c, '_get_open_prs', return_value=[]) as fetch, \
         patch.object(c, 'MachineGuards'):
        snapshot = worker.build_state_snapshot()
    assert 'rupret007/new-project' in snapshot.repos
    fetched = {call.args[0] for call in fetch.call_args_list}
    assert 'rupret007/new-project' in fetched
    assert 'rupret007/rad-dad-show-night' not in fetched
    assert 'rupret007/archived' not in fetched
