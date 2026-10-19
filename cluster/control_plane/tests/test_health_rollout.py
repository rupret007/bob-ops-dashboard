"""Fail-closed comparison, ordered rollout and rollback of partial manager changes."""
from unittest.mock import Mock, patch
import subprocess

import pytest

from cluster.control_plane import health_rollout as rollout
from cluster.control_plane.health_adapters import PERIODS


def candidate(machine='nucbox'):
    return {'machine': machine, 'loaded_head': 'a' * 40, 'timestamp': 1000,
            'groups': {name: {'checks': [{'name': name, 'passed': True}],
                              'finished_at': 1000, 'period': period, 'details': {}}
                       for name, period in PERIODS.items()},
            'rollout': {'phase': 'observe'}}


def test_comparison_cannot_drop_check_or_change_failed_outcome():
    old = [{'name': 'one', 'passed': True}, {'name': 'two', 'passed': False}]
    assert not rollout.same_checks(old, old[:1])
    assert not rollout.same_checks(old, [old[0], dict(old[1], passed=True)])
    assert rollout.same_checks(old, [dict(c, message='same detail class, changed age') for c in old])


def test_mini_requires_fresh_matching_nucbox_canary():
    value = candidate('mini')
    assert not rollout.peer_canary_ok(value, {}, 1001)
    peer = candidate()
    peer['rollout'] = dict(phase='active', canary_passed_at=1000, head='a' * 40)
    assert rollout.peer_canary_ok(value, peer, 1001)
    assert not rollout.peer_canary_ok(value, peer, 2000)
    peer['rollout']['head'] = 'b' * 40
    assert not rollout.peer_canary_ok(value, peer, 1001)


def test_partial_cutover_restores_original_enabled_state(tmp_path):
    value = candidate()
    manager = Mock()
    manager.snapshot.return_value = {'watchdog': True, 'guardian': False}
    manager.disable.side_effect = RuntimeError('manager rejected request')
    with pytest.raises(RuntimeError):
        rollout.activate(tmp_path, value, manager, 1001)
    assert value['rollout']['phase'] == 'observe'
    manager.restore.assert_called_once_with(manager.snapshot.return_value)


def test_cutover_sets_authority_only_after_disable_and_verify(tmp_path):
    value = candidate()
    manager = Mock()
    manager.snapshot.return_value = {'watchdog': True}
    manager.verify_disabled.return_value = True
    rollout.activate(tmp_path, value, manager, 1001)
    assert value['rollout']['phase'] == 'canary'
    assert value['rollout']['previous_units'] == {'watchdog': True}
    assert value['rollout']['activated_at'] == 1001


def test_missing_or_failed_group_blocks_comparison():
    value = candidate()
    assert rollout.complete_cycle(value, 1001)
    del value['groups']['doctor']
    assert not rollout.complete_cycle(value, 1001)
    value = candidate()
    value['groups']['doctor']['error'] = 'TimeoutExpired'
    assert not rollout.complete_cycle(value, 1001)


def disabled_listing(value):
    return 'disabled services = {\n' + '\n'.join(
        '\t"com.storypool.{}" => {}'.format(name, value) for name in rollout.RETIRED) + '\n}\n'


@pytest.mark.parametrize('value,enabled', [('enabled', True), ('disabled', False),
                                         ('false', True), ('true', False)])
def test_launchd_current_and_legacy_enable_states(value, enabled):
    assert rollout.launchd_enable_states(disabled_listing(value)) == dict.fromkeys(rollout.RETIRED, enabled)


@pytest.mark.parametrize('output', ['', '{}', 'disabled services = {}',
    disabled_listing('unknown'), disabled_listing('true').replace('watchdog"', 'watchdog-extra"'),
    disabled_listing('true').replace('true', 'true-extra', 1),
    disabled_listing('true').replace('\n}', '\n"com.storypool.watchdog" => false\n}')])
def test_unknown_missing_or_conflicting_enable_states_fail_closed(output):
    with pytest.raises(RuntimeError):
        rollout.launchd_enable_states(output)


@pytest.mark.parametrize('value,loaded,expected', [('disabled', False, True), ('true', False, True),
                                                ('enabled', False, False), ('disabled', True, False)])
def test_verify_disabled_requires_disabled_and_unregistered(tmp_path, value, loaded, expected):
    manager = rollout.Manager('mini', tmp_path)
    manager.run = Mock(side_effect=lambda argv: subprocess.CompletedProcess(argv,
        0 if argv[1] == 'print-disabled' or loaded else 113, disabled_listing(value), ''))
    assert manager.verify_disabled() is expected


def test_enable_listing_command_failure_does_not_become_disabled(tmp_path):
    manager = rollout.Manager('mini', tmp_path)
    manager.run = Mock(return_value=subprocess.CompletedProcess([], 1, disabled_listing('disabled'), 'private'))
    with pytest.raises(RuntimeError):
        manager.verify_disabled()


def test_registration_error_is_unknown_not_absent(tmp_path):
    manager = rollout.Manager('mini', tmp_path)
    manager.run = Mock(side_effect=lambda argv: subprocess.CompletedProcess(argv,
        0 if argv[1] == 'print-disabled' else 1, disabled_listing('disabled'), ''))
    with pytest.raises(RuntimeError):
        manager.verify_disabled()


@pytest.mark.parametrize('prints,ok', [([0, 113], True), ([0] * 5, False), ([1] * 5, False)])
def test_disable_waits_for_proven_unregistration_without_more_mutations(tmp_path, monkeypatch, prints, ok):
    manager = rollout.Manager('mini', tmp_path)
    manager.snapshot = Mock(return_value={'watchdog': {'installed': True}})
    codes = iter([0, 0] + prints)
    manager.run = Mock(side_effect=lambda argv: subprocess.CompletedProcess(argv, next(codes), '', ''))
    monkeypatch.setattr(rollout.guardian.time, 'sleep', lambda _: None)
    monkeypatch.setattr(rollout.guardian, 'event', lambda *a, **kw: None)
    if ok:
        manager.disable()
    else:
        with pytest.raises(RuntimeError):
            manager.disable()
    assert [c.args[0][1] for c in manager.run.call_args_list] == ['disable', 'bootout'] + ['print'] * len(prints)
