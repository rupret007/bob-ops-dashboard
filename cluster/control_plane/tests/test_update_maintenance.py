"""Operator maintenance stops normal updates without changing release gates."""
import fcntl
import json
from unittest.mock import Mock

import pytest

from cluster.control_plane import node_control, self_update as updater


OLD, TARGET, CHANGED = 'a' * 40, 'b' * 40, 'c' * 40
MODES = [(machine, operator) for machine in ('mini', 'nucbox')
         for operator in (False, True)]


@pytest.fixture
def update(tmp_path, monkeypatch):
    repo, data, control = (tmp_path / name for name in ('repo', 'data', 'control'))
    for path in (repo, data, control):
        path.mkdir()
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(data))
    monkeypatch.setenv('STORYPOOL_STATE_DIR', str(control))
    monkeypatch.setattr(updater, '_log_update_outcome', Mock())
    state = dict(head=OLD, remote=TARGET)
    monkeypatch.setattr(updater, 'get_current_head', lambda _: state['head'])
    monkeypatch.setattr(updater, 'get_current_branch', lambda _: updater.BRANCH)
    monkeypatch.setattr(updater, 'get_remote_head', lambda _: state['remote'])
    monkeypatch.setattr(updater, 'is_working_tree_dirty', lambda _: False)
    for name in ('fetch_branch', 'run_tests', 'target_canary_ready', 'reinstall',
                 'copy_karen_module', 'migrate_config', 'rollback_to_sha'):
        monkeypatch.setattr(updater, name, Mock(return_value=True))
    monkeypatch.setattr(updater, 'clear_pycache', Mock(return_value=0))
    monkeypatch.setattr(updater, 'can_fast_forward', Mock(return_value=(True, TARGET)))

    def forward(_, sha):
        state['head'] = sha
        return True, OLD

    monkeypatch.setattr(updater, 'fast_forward', Mock(side_effect=forward))
    return repo, data, control, state


def call(update, machine, operator):
    return updater.self_update(machine, update[0], operator=operator,
                               expected_head=TARGET if operator else None)


def hold(update):
    # The public operator writer takes its own short lock and atomically replaces
    # state. The updater must not retain that lock while doing work.
    node_control.set_maintenance(True, 'maintenance regression', update[2])


def assert_not_installed():
    updater.reinstall.assert_not_called()
    updater.rollback_to_sha.assert_not_called()


@pytest.mark.parametrize('machine,operator', MODES)
def test_existing_maintenance_blocks_both_modes_before_admission(update, machine, operator):
    hold(update)
    result = call(update, machine, operator)
    assert result['outcome'] == 'SKIPPED'
    assert 'maintenance' in result['reason']
    updater.fetch_branch.assert_not_called()
    updater.fast_forward.assert_not_called()
    updater.run_tests.assert_not_called()
    updater.copy_karen_module.assert_not_called()
    updater.migrate_config.assert_not_called()
    assert_not_installed()
    assert not (update[1] / '.self-update.lock').exists()
    assert not (update[1] / 'overseer.lock').exists()


@pytest.mark.parametrize('payload', ['{', '[]', '{}', '{"maintenance": "false"}',
                                    '{"maintenance": null}', '{"maintenance": 0}'])
@pytest.mark.parametrize('machine,operator', MODES)
def test_malformed_operator_state_fails_closed(update, machine, operator, payload):
    (update[2] / 'node-control.json').write_text(payload)
    assert call(update, machine, operator)['outcome'] == 'SKIPPED'
    updater.fetch_branch.assert_not_called()
    assert_not_installed()


@pytest.mark.parametrize('machine,operator', MODES)
def test_unreadable_operator_state_fails_closed(update, machine, operator):
    (update[2] / 'node-control.json').mkdir()
    assert call(update, machine, operator)['outcome'] == 'SKIPPED'
    updater.fetch_branch.assert_not_called()
    assert_not_installed()


@pytest.mark.parametrize('value', [OSError('private operator detail'), RuntimeError('private operator detail'),
                                  None, 0, 'false', {}])
def test_failed_or_nonboolean_control_read_does_not_admit(update, monkeypatch, value):
    reader = Mock(side_effect=value) if isinstance(value, Exception) else Mock(return_value=value)
    monkeypatch.setattr(node_control, 'is_draining', reader)
    result = call(update, 'mini', True)
    assert result['outcome'] == 'SKIPPED'
    assert 'private operator detail' not in json.dumps(result)
    assert 'private operator detail' not in repr(updater._log_update_outcome.call_args_list)
    updater.fetch_branch.assert_not_called()
    assert_not_installed()


@pytest.mark.parametrize('explicit_false', [False, True])
@pytest.mark.parametrize('machine,operator', MODES)
def test_missing_or_explicitly_clear_control_preserves_normal_update(update, machine, operator,
                                                                    explicit_false):
    if explicit_false:
        (update[2] / 'node-control.json').write_text('{"maintenance": false}')
    result = call(update, machine, operator)
    assert result['outcome'] == 'UPDATED'
    assert update[3]['head'] == TARGET
    updater.run_tests.assert_called_once_with(update[0])
    updater.reinstall.assert_called_once_with(update[0], machine)
    assert all(c.args[1] == TARGET for c in updater.target_canary_ready.call_args_list)
    assert not (update[0] / '.storypool-reinstall-pending').exists()


@pytest.mark.parametrize('stage', ['branch', 'fast-forward-proof'])
@pytest.mark.parametrize('machine,operator', MODES)
def test_maintenance_arriving_before_fetch_or_fast_forward_stops_mutation(update, monkeypatch,
                                                                         machine, operator, stage):
    if stage == 'branch':
        def branch(_):
            hold(update)
            return updater.BRANCH
        monkeypatch.setattr(updater, 'get_current_branch', branch)
    else:
        def proof(*_):
            hold(update)
            return True, TARGET
        updater.can_fast_forward.side_effect = proof
    result = call(update, machine, operator)
    assert result['outcome'] == 'SKIPPED'
    assert update[3]['head'] == OLD
    updater.fast_forward.assert_not_called()
    if stage == 'branch':
        updater.fetch_branch.assert_not_called()
    updater.copy_karen_module.assert_not_called()
    assert_not_installed()
    assert not (update[0] / '.storypool-reinstall-pending').exists()


@pytest.mark.parametrize('stage', ['fast-forward', 'tests-pass', 'tests-fail', 'tests-change-head'])
@pytest.mark.parametrize('machine,operator', MODES)
def test_maintenance_after_fast_forward_keeps_head_and_pending_without_rollback(update, machine,
                                                                               operator, stage):
    if stage == 'fast-forward':
        def forward(_, sha):
            update[3]['head'] = sha
            hold(update)
            return True, OLD
        updater.fast_forward.side_effect = forward
    else:
        def test_gate(_):
            if stage == 'tests-change-head':
                update[3]['head'] = CHANGED
            hold(update)
            return stage == 'tests-pass'
        updater.run_tests.side_effect = test_gate
    result = call(update, machine, operator)
    assert result['outcome'] == 'SKIPPED'
    assert 'maintenance' in result['reason']
    actual_head = CHANGED if stage == 'tests-change-head' else TARGET
    assert update[3]['head'] == actual_head
    assert result['observed_head'] == actual_head
    assert result['source_advanced'] is True
    assert result['updated'] is False
    assert result['reinstalled'] is False
    assert (update[0] / '.storypool-reinstall-pending').exists()
    updater.copy_karen_module.assert_not_called()
    updater.migrate_config.assert_not_called()
    assert_not_installed()
    if stage == 'fast-forward':
        updater.run_tests.assert_not_called()


@pytest.mark.parametrize('machine,operator', MODES)
def test_hold_during_fetch_does_not_read_remote_or_start_same_head_pending_tests(update, monkeypatch,
                                                                               machine, operator):
    update[3]['head'] = TARGET
    pending = update[0] / '.storypool-reinstall-pending'
    pending.touch()
    remote = Mock(return_value=TARGET)
    monkeypatch.setattr(updater, 'get_remote_head', remote)

    def fetch(_):
        hold(update)
        return True

    updater.fetch_branch.side_effect = fetch
    result = call(update, machine, operator)
    assert result['outcome'] == 'SKIPPED'
    assert 'maintenance' in result['reason']
    assert update[3]['head'] == TARGET
    assert pending.exists()
    remote.assert_not_called()
    updater.run_tests.assert_not_called()
    updater.fast_forward.assert_not_called()
    assert_not_installed()


@pytest.mark.parametrize('stage', ['pycache', 'module', 'config', 'last-canary'])
@pytest.mark.parametrize('machine,operator', MODES)
def test_maintenance_during_preparation_stops_remaining_writes_and_installer(update, machine,
                                                                           operator, stage):
    if stage == 'pycache':
        def clear(_):
            hold(update)
            return 1
        updater.clear_pycache.side_effect = clear
    elif stage == 'module':
        def copy(_):
            hold(update)
            return True
        updater.copy_karen_module.side_effect = copy
    elif stage == 'config':
        def migrate():
            hold(update)
            return True
        updater.migrate_config.side_effect = migrate
    else:
        calls = []
        def canary(*_):
            calls.append(True)
            if len(calls) == 3:
                hold(update)
            return True
        updater.target_canary_ready.side_effect = canary
    assert call(update, machine, operator)['outcome'] == 'SKIPPED'
    assert (update[0] / '.storypool-reinstall-pending').exists()
    assert update[3]['head'] == TARGET
    assert_not_installed()
    if stage == 'pycache':
        assert updater.clear_pycache.call_count == 1
        updater.copy_karen_module.assert_not_called()
    if stage in ('pycache', 'module'):
        updater.migrate_config.assert_not_called()


def test_pending_same_head_retry_waits_for_maintenance_then_retains_test_and_canary_gates(update):
    update[3]['head'] = TARGET
    pending = update[0] / '.storypool-reinstall-pending'
    pending.touch()
    hold(update)
    assert call(update, 'mini', True)['outcome'] == 'SKIPPED'
    assert pending.exists()
    updater.run_tests.assert_not_called()
    node_control.set_maintenance(False, 'maintenance regression', update[2])
    assert call(update, 'mini', True)['outcome'] == 'UPDATED'
    updater.run_tests.assert_called_once_with(update[0])
    assert updater.target_canary_ready.call_count == 2
    updater.fast_forward.assert_not_called()
    assert not pending.exists()


def test_updater_never_retains_operator_control_lock(update):
    def fetch(_):
        with (update[2] / '.node-control.lock').open('a') as operator:
            fcntl.flock(operator, fcntl.LOCK_EX | fcntl.LOCK_NB)
        hold(update)
        return True
    updater.fetch_branch.side_effect = fetch
    assert call(update, 'nucbox', True)['outcome'] == 'SKIPPED'
    updater.fast_forward.assert_not_called()
    assert_not_installed()


@pytest.mark.parametrize('stage', ['head-mismatch', 'tests-failure'])
@pytest.mark.parametrize('rollback_succeeds', [True, False])
def test_failure_reports_observed_source_after_successful_or_failed_rollback(update, stage,
                                                                            rollback_succeeds):
    advanced = CHANGED if stage == 'head-mismatch' else TARGET
    if stage == 'head-mismatch':
        def forward(_, sha):
            update[3]['head'] = CHANGED
            return True, OLD
        updater.fast_forward.side_effect = forward
    else:
        updater.run_tests.return_value = False

    def rollback(_, sha):
        assert sha == OLD
        if rollback_succeeds:
            update[3]['head'] = sha
        return rollback_succeeds

    updater.rollback_to_sha.side_effect = rollback
    result = call(update, 'mini', True)
    expected_head = OLD if rollback_succeeds else advanced
    assert result['outcome'] == 'FAILED'
    assert update[3]['head'] == expected_head
    assert result['observed_head'] == expected_head
    assert result['source_advanced'] is (not rollback_succeeds)
    assert result['updated'] is False
    assert result['reinstalled'] is False
    updater.rollback_to_sha.assert_called_once_with(update[0], OLD)
    updater.reinstall.assert_not_called()
    logged_reason, logged_result = updater._log_update_outcome.call_args.args
    assert logged_result['observed_head'] == expected_head
    assert result['rolled_back'] is rollback_succeeds
    assert logged_result['rolled_back'] is rollback_succeeds
    if rollback_succeeds:
        assert 'rolled back' in result['reason']
        assert 'rollback incomplete' not in logged_reason
    else:
        assert 'rollback incomplete' in result['reason']
        assert 'rollback incomplete' in logged_reason
        assert 'rolled back' not in result['reason']
        assert 'rolled back' not in logged_reason
