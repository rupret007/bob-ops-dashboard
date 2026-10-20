"""Account admission defers the cycle, never a PR verdict or a retry loop."""
import json
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane import conductor as c
from cluster.control_plane.tests.test_deterministic_merge import artifact, pr, runner


DRAINING_TARGET = 'cluster.control_plane.node_control.is_draining'
REFRESH_TARGET = 'cluster.control_plane.improvement.refresh'


def prepared(tmp_path, *, approved=True):
    if approved:
        artifact(tmp_path)
    worker = runner(tmp_path)
    snapshot = c.StateSnapshot(c._now_iso(), ['owner/repo'],
        [pr(), pr(number=8)], {}, [], {}, {}, {}, [], [])
    worker.build_state_snapshot = Mock(return_value=snapshot)
    worker.get_llm_proposals = Mock(return_value={})
    worker.write_digest = Mock()
    return worker


def exhausted():
    return c.GitHubDeferred('GitHub shared API minute budget exhausted; retry later',
                            code='minute-exhausted', retry_after=27.5)


def assert_deferred(worker, summary):
    assert summary['deferred'] is True
    assert summary['deferred_code'] == 'minute-exhausted'
    assert summary['retry_after'] == 27.5
    assert summary['deferred_reason'] == 'GitHub API admission deferred'
    assert summary['counts_complete'] is False
    assert all(summary[name] is None for name in
               ('proposals_count', 'validated_count', 'rejected_count', 'executed_count'))
    assert json.loads(worker._status_file().read_text())['summary'] == summary
    events = [json.loads(line) for line in worker._log_file().read_text().splitlines()]
    assert events[-1]['action'] == 'cycle_deferred'
    assert not any(event['action'] == 'cycle_end' for event in events)
    worker.write_digest.assert_not_called()


def test_actual_merge_validation_budget_exhaustion_defers_cycle_without_more_work(tmp_path):
    worker = prepared(tmp_path)
    worker.execute_action = Mock()
    with patch(DRAINING_TARGET, return_value=False), \
         patch(REFRESH_TARGET, return_value={}), \
         patch.object(c.github_reads.budget, 'reserve', side_effect=exhausted()) as budget, \
         patch.object(c.subprocess, 'run') as command:
        summary = worker.run(live=True)
    assert_deferred(worker, summary)
    budget.assert_called_once_with('graphql')
    command.assert_not_called()
    worker.execute_action.assert_not_called()
    worker.get_llm_proposals.assert_not_called()
    worker.metrics.record_merge.assert_not_called()


@pytest.mark.parametrize('stage', ['discovery', 'merge-execution', 'optional-validation'])
def test_other_uncaught_admission_boundaries_also_stop_the_cycle(tmp_path, stage):
    worker = prepared(tmp_path)
    if stage == 'discovery':
        worker.build_state_snapshot.side_effect = exhausted()
    elif stage == 'merge-execution':
        worker.execute_action = Mock(side_effect=exhausted())
    else:
        worker.deterministic_review_pass = Mock(return_value=[])
        worker.deterministic_merge_pass = Mock(return_value=[])
        worker.get_llm_proposals.return_value = {'proposed_actions':[
            {'action_type':'label_needs_karen', 'repo':'owner/repo', 'pr_number':7}]}
    with patch(DRAINING_TARGET, return_value=False), \
         patch(REFRESH_TARGET, return_value={}), \
         patch.object(c, '_get_pr_details', side_effect=exhausted() if stage == 'optional-validation' else None,
                      return_value=pr()), \
         patch.object(c.subprocess, 'run') as command:
        summary = worker.run(live=True)
    assert_deferred(worker, summary)
    command.assert_not_called()
    if stage != 'optional-validation':
        worker.get_llm_proposals.assert_not_called()


@pytest.mark.parametrize('at_write', [False, True])
def test_review_admission_releases_lease_and_stops_after_first_budget_refusal(tmp_path, at_write):
    worker = prepared(tmp_path, approved=False)
    worker.deterministic_merge_pass = Mock()
    calls = []
    def reserve(*args, **kwargs):
        calls.append((args, kwargs))
        if not at_write or len(calls) > 1:
            raise exhausted()
    with patch(DRAINING_TARGET, return_value=False), \
         patch(REFRESH_TARGET, return_value={}), \
         patch.object(c.ClaimClient, 'from_env') as factory, patch.object(c, 'Lease') as lease, \
         patch.object(c, 'ensure_label_exists'), \
         patch.object(c.github_reads.budget, 'reserve', side_effect=reserve), \
         patch.object(c.subprocess, 'run', return_value=subprocess.CompletedProcess(
             [], 0, json.dumps(pr()), '')) as command:
        summary = worker.run(live=True)
    assert_deferred(worker, summary)
    assert len(calls) == (2 if at_write else 1)
    assert command.call_count == (1 if at_write else 0)
    assert all(call.args[0][:3] == ['gh', 'pr', 'view'] for call in command.call_args_list)
    factory.return_value.acquire.assert_called_once()
    lease.return_value.__exit__.assert_called_once()
    worker.deterministic_merge_pass.assert_not_called()
    worker.get_llm_proposals.assert_not_called()


def test_earlier_actions_are_not_misreported_as_zero_or_as_rejected(tmp_path):
    worker = prepared(tmp_path)
    completed = c.ValidatedAction(c.ProposedAction('label_needs_karen'), True, 'admitted',
                                  executed=True, execution_result='labeled')
    worker.deterministic_review_pass = Mock(return_value=[completed])
    worker.deterministic_merge_pass = Mock(side_effect=exhausted())
    with patch(DRAINING_TARGET, return_value=False), \
         patch(REFRESH_TARGET, return_value={}):
        summary = worker.run(live=True)
    assert_deferred(worker, summary)
    assert completed.executed and completed.execution_result == 'labeled'
    worker.metrics.record_merge.assert_not_called()


def test_non_budget_bug_is_not_swallowed(tmp_path):
    worker = prepared(tmp_path)
    worker.build_state_snapshot.side_effect = ValueError('fixture programming error')
    with patch(DRAINING_TARGET, return_value=False):
        with pytest.raises(ValueError, match='fixture programming error'):
            worker.run(live=True)


def test_missing_local_pass_still_never_spends_fresh_pr_admission(tmp_path):
    worker = prepared(tmp_path, approved=False)
    worker.execute_action = Mock()
    with patch.object(c.github_reads, 'run') as read:
        results = worker.deterministic_merge_pass(SimpleNamespace(open_prs=[pr()]), live=True)
    assert not results[0].validated and 'No KARENPASS' in results[0].validation_reason
    read.assert_not_called()
    worker.execute_action.assert_not_called()


def test_under_lease_revalidation_defers_without_merge_and_releases_ownership(tmp_path):
    worker = prepared(tmp_path)
    with patch(DRAINING_TARGET, return_value=False), \
         patch(REFRESH_TARGET, return_value={}), \
         patch.object(c.ClaimClient, 'from_env') as factory, patch.object(c, 'Lease') as lease, \
         patch.object(c.github_reads.budget, 'reserve', side_effect=[None, exhausted()]) as budget, \
         patch.object(c.subprocess, 'run', return_value=subprocess.CompletedProcess(
             [], 0, json.dumps(pr()), '')) as command:
        summary = worker.run(live=True)
    assert_deferred(worker, summary)
    assert budget.call_count == 2
    command.assert_called_once()
    assert command.call_args.args[0][:3] == ['gh', 'pr', 'view']
    factory.return_value.acquire.assert_called_once()
    lease.return_value.__exit__.assert_called_once()
    worker.metrics.record_merge.assert_not_called()
    worker.get_llm_proposals.assert_not_called()


def test_deferral_persists_only_controlled_metadata_not_exception_text(tmp_path):
    worker = prepared(tmp_path)
    worker.build_state_snapshot.side_effect = c.GitHubDeferred('private-placeholder-marker')
    with patch(DRAINING_TARGET, return_value=False):
        summary = worker.run(live=True)
    assert summary['deferred_code'] == 'other' and summary['retry_after'] is None
    assert 'private-placeholder-marker' not in worker._status_file().read_text()
    assert 'private-placeholder-marker' not in worker._log_file().read_text()


def test_existing_cli_reports_deferred_cycle_without_crashing(tmp_path, capsys):
    worker = prepared(tmp_path)
    worker.build_state_snapshot.side_effect = exhausted()
    with patch(DRAINING_TARGET, return_value=False), \
         patch.object(c, 'Conductor', return_value=worker), patch.object(sys, 'argv', ['conductor']):
        assert c.main() == 0
    output = capsys.readouterr().out
    assert 'GitHub API admission deferred; cycle incomplete' in output
    assert 'Executed: None' in output


GO_LIVE_SCRIPTS = ['go-live-mini.sh', 'go-live-nucbox.sh']


def go_live_predicate(script, path):
    """Execute only the real inline JSON predicate, never an installer block."""
    text = (Path(c.__file__).parent / 'install' / script).read_text()
    lines = [line.strip() for line in text.splitlines()
             if line.strip().startswith('if [[ $conductor_exit -eq 0 ]] && python3 -c ')]
    assert len(lines) == 1
    command = lines[0].split(' && ', 1)[1]
    assert command.endswith(' 2>/dev/null; then')
    args = shlex.split(command[:-len(' 2>/dev/null; then')])
    assert args[:2] == ['python3', '-c'] and args[3:] == ['$conductor_tmp']
    return subprocess.run([sys.executable, '-I', '-c', args[2], str(path)],
                          capture_output=True, text=True, timeout=10).returncode


@pytest.mark.parametrize('script', GO_LIVE_SCRIPTS)
def test_go_live_predicates_reject_real_deferred_cycle(tmp_path, script):
    worker = prepared(tmp_path)
    worker.build_state_snapshot.side_effect = exhausted()
    with patch(DRAINING_TARGET, return_value=False):
        summary = worker.run(live=True)
    path = tmp_path / 'deferred.json'
    path.write_text(json.dumps(summary))
    assert go_live_predicate(script, path) != 0


@pytest.mark.parametrize('script', GO_LIVE_SCRIPTS)
@pytest.mark.parametrize('payload,accepted', [
    ('{"counts_complete":true,"deferred":false}', True),
    ('{"counts_complete":false,"deferred":true}', False),
    ('{"counts_complete":true,"deferred":true}', False),
    ('{"counts_complete":true}', False), ('{"deferred":false}', False),
    ('{"counts_complete":1,"deferred":0}', False),
    ('{"counts_complete":"true","deferred":"false"}', False),
    ('{}', False), ('[]', False), ('null', False), ('"complete"', False),
    ('{broken json', False)])
def test_go_live_predicates_require_explicit_complete_shape(tmp_path, script, payload, accepted):
    path = tmp_path / 'predicate.json'
    path.write_text(payload)
    assert (go_live_predicate(script, path) == 0) is accepted


@pytest.mark.parametrize('script', GO_LIVE_SCRIPTS)
def test_normal_complete_cycle_is_explicit_and_accepted_by_real_predicate(tmp_path, script):
    worker = prepared(tmp_path, approved=False)
    with patch(DRAINING_TARGET, return_value=False), \
         patch(REFRESH_TARGET, return_value={}):
        summary = worker.run(live=False)
    assert summary['counts_complete'] is True and summary['deferred'] is False
    assert all(type(summary[key]) is int for key in
               ('proposals_count', 'validated_count', 'rejected_count', 'executed_count'))
    path = tmp_path / 'complete.json'
    path.write_text(json.dumps(summary))
    assert go_live_predicate(script, path) == 0
