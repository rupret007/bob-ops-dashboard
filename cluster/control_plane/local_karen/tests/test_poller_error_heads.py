"""Failed review attempts retain their subject without becoming review authority."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cluster.control_plane import github_reads
from cluster.control_plane.local_karen import poller
from cluster.control_plane.local_karen.config import KarenConfig


HEAD = 'a' * 40
OTHER = 'b' * 40


@pytest.fixture
def attempt(tmp_path, monkeypatch):
    config = KarenConfig(repos=['owner/repo'], state_dir=str(tmp_path / 'state'),
                         verdict_dir=str(tmp_path / 'verdicts'))
    pr = dict(number=7, headRefOid=HEAD, isDraft=False)
    monkeypatch.setattr(poller, 'BACKLOG_AVAILABLE', False)
    monkeypatch.setattr(poller.KarenConfig, 'migrate_config', lambda: None)
    monkeypatch.setattr(poller, 'record_runtime', lambda _: None)
    monkeypatch.setattr(poller, 'list_prs_with_label', lambda *args: [pr])
    monkeypatch.setattr(poller, 'enrich', lambda *args, **kwargs: None)
    monkeypatch.setattr(github_reads, 'refresh_quota', lambda: None)
    monkeypatch.setattr('cluster.control_plane.node_control.is_draining', lambda _: False)
    monkeypatch.setattr('cluster.control_plane.guards.MachineGuards.check_all',
                        lambda _: SimpleNamespace(passed=True))
    gate = Mock(return_value=SimpleNamespace(error='Synthetic incomplete review',
                    sha_verified=False, verdict_path=None, context_diagnostics=[]))
    monkeypatch.setattr(poller, 'run_gate', gate)
    side_effects = [Mock(), Mock(), Mock(), Mock()]
    for name, call in zip(['remove_label', 'add_label', '_ensure_result_label',
                           '_dispatch_fix_if_eligible'], side_effects):
        monkeypatch.setattr(poller, name, call)
    push = Mock()
    monkeypatch.setattr('cluster.control_plane.local_karen.verdict_push.push', push)

    def run():
        return poller._run_poll_once(config, auto_discover=False, auto_label=False,
                                     auto_update=False)

    return config, pr, gate, run, side_effects + [push]


@pytest.mark.parametrize('failure', ['raised', 'returned', 'unverified', 'missing_artifact'])
def test_error_keeps_attempted_head_and_stage_without_approval(attempt, failure):
    config, pr, gate, run, side_effects = attempt
    if failure == 'raised':
        gate.side_effect = RuntimeError('Synthetic transport refused')
    elif failure == 'unverified':
        gate.return_value = SimpleNamespace(error=None, sha_verified=False,
                                           verdict_path='synthetic-artifact')
    elif failure == 'missing_artifact':
        gate.return_value = SimpleNamespace(error=None, sha_verified=True, verdict_path=None)
    row, = run()
    assert row['sha'] == HEAD and row['status'] == 'ERROR'
    assert row['stage'] == ('gate' if failure == 'raised' else 'artifact_verification')
    assert 'error' in row and 'passed' not in row and 'verdict' not in row
    assert gate.call_args.kwargs['expected_sha'] == HEAD
    assert poller.load_poll_state(config).prs_checked == [row]
    assert not config.get_verdict_dir().exists()
    for call in side_effects[:-1]:
        call.assert_not_called()
    # The cycle still retries delivery of prior observations, with no new artifact.
    side_effects[-1].assert_called_once_with(config)


def test_changed_candidate_or_returned_head_cannot_reassign_a_failed_attempt(attempt):
    config, pr, gate, run, _ = attempt

    def changed(**kwargs):
        assert kwargs['expected_sha'] == HEAD
        pr['headRefOid'] = OTHER
        return SimpleNamespace(error='Synthetic head changed', sha_verified=False,
                               verdict_path=None, verdict=SimpleNamespace(head_sha=OTHER))

    gate.side_effect = changed
    row, = run()
    assert row['sha'] == HEAD and row['sha'] != pr['headRefOid']
    assert poller.load_poll_state(config).prs_checked == [row]


@pytest.mark.parametrize('value', [None, 7, True, 'a' * 39, 'a' * 41,
                                  'synthetic-untrusted-head'])
def test_unknown_or_malformed_head_is_not_recorded_as_a_pin(attempt, value):
    _, pr, _, run, _ = attempt
    pr['headRefOid'] = value
    row, = run()
    assert row['sha'] is None and row['status'] == 'ERROR'
    assert 'synthetic-untrusted-head' not in json.dumps(row)


def test_new_failed_attempts_preserve_unattributed_history(attempt):
    config, pr, _, run, _ = attempt
    legacy = dict(repo='owner/old', pr=2, error='Older unattributed failure')
    poller.save_poll_state(poller.PollState('earlier', [legacy]), config)
    first, = run()
    pr['headRefOid'] = OTHER
    second, = run()
    assert [first['sha'], second['sha']] == [HEAD, OTHER]
    assert poller.load_poll_state(config).prs_checked == [legacy, first, second]
    assert 'sha' not in legacy


def test_post_review_failure_does_not_replace_the_verified_outcome(attempt):
    _, _, gate, run, side_effects = attempt
    gate.return_value = SimpleNamespace(error=None, sha_verified=True,
        verdict_path='synthetic-artifact',
        verdict=SimpleNamespace(head_sha=HEAD, passed=True, reason='Synthetic verified result'))
    side_effects[0].side_effect = RuntimeError('Synthetic label write refused')
    passed, error = run()
    assert passed['sha'] == HEAD and passed['passed'] is True
    assert error['sha'] == HEAD and error['stage'] == 'post_review'
    assert error['status'] == 'ERROR' and 'passed' not in error


def test_cli_identifies_new_error_heads_and_keeps_legacy_output(monkeypatch, capsys):
    rows = [dict(repo='owner/repo', pr=7, sha=HEAD, status='ERROR', error='Incomplete'),
            dict(repo='owner/old', pr=2, error='Unattributed')]
    monkeypatch.setattr('sys.argv', ['karen-poller', '--once'])
    monkeypatch.setattr(poller.KarenConfig, 'load', lambda: object())
    monkeypatch.setattr(poller, 'run_poll_once', lambda *args, **kwargs: rows)
    assert poller.main() == 0
    output = capsys.readouterr().out
    assert 'ERROR owner/repo#7@' + HEAD + ': Incomplete' in output
    assert 'ERROR owner/old#2: Unattributed' in output
