"""Fail-closed comparison, ordered rollout and rollback of partial manager changes."""
from unittest.mock import Mock, patch

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
