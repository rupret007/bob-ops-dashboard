"""Canary admission needs positive health, not just a completed probe."""
import copy
import subprocess
from unittest.mock import Mock

import pytest

from cluster.control_plane import health_rollout as rollout, health_service as service
from cluster.control_plane.health_adapters import PERIODS, TIMEOUTS
from cluster.control_plane.tests.test_health_service import Executor


def healthy_status():
    return dict(machine='nucbox', loaded_head='a' * 40, timestamp=1000,
                rollout=dict(phase='canary', activated_at=990, head='a' * 40),
                groups={name: dict(started_at=995, finished_at=1000,
                                   execution_completed=True, period=period,
                                   checks=[dict(name=name, passed=True)])
                        for name, period in PERIODS.items()})


@pytest.mark.parametrize('name', list(PERIODS))
@pytest.mark.parametrize('finding', [dict(passed=False), dict(passed=False, repaired=True),
    dict(unknown=True), dict(message='WARNING: deferred'), dict(message='Evidence unverified'),
    dict(execution_error=True), dict(passed=1), dict(unknown='false')])
def test_any_required_nonpositive_finding_blocks_cycle_and_peer_admission(name, finding):
    value = healthy_status()
    value['rollout'].update(phase='active', last_confirmed_at=1000)
    value['groups'][name]['checks'][0].update(finding)
    assert not rollout.complete_cycle(value, 1001, 990)
    assert not rollout.peer_canary_ok(dict(loaded_head='a' * 40), value, 1001)


@pytest.mark.parametrize('change', [dict(execution_completed=False), dict(unknown=True),
    dict(error='TimeoutError'), dict(started_at=989), dict(started_at=True),
    dict(started_at=float('nan')), dict(started_at=float('inf')), dict(started_at=1001),
    dict(finished_at=994), dict(finished_at=1002), dict(finished_at=float('nan')),
    dict(checks=[]), dict(checks=[None]), dict(checks='not checks')])
def test_malformed_unattested_or_wrong_cycle_cannot_prove_health(change):
    value = healthy_status()
    value['groups']['guardian'].update(change)
    assert not rollout.complete_cycle(value, 1001, 990)


@pytest.mark.parametrize('missing', ['started_at', 'finished_at', 'execution_completed', 'checks'])
def test_cycle_requires_explicit_parent_completion_evidence(missing):
    value = healthy_status()
    del value['groups']['guardian'][missing]
    assert not rollout.complete_cycle(value, 1001, 990)


@pytest.mark.parametrize('name', list(PERIODS))
def test_reported_expected_groups_cannot_drop_required_coverage(name):
    value = healthy_status()
    value['expected_groups'] = list(set(PERIODS) - {name})
    del value['groups'][name]
    assert not rollout.complete_cycle(value, 1001, 990)


def test_full_positive_cycle_and_exact_freshness_boundary():
    value = healthy_status()
    assert rollout.complete_cycle(value, 1000, 995)
    assert rollout.complete_cycle(value, 1210, 995)
    assert not rollout.complete_cycle(value, 1210.001, 995)


@pytest.mark.parametrize('finding', [dict(passed=False), dict(passed=False, repaired=True),
    dict(unknown=True), dict(message='Could not check health'), dict(execution_error=True)])
def test_completed_nonpositive_canary_uses_normal_rollback_without_rewriting_findings(tmp_path, finding):
    scheduler = service.Scheduler(tmp_path, 'nucbox', 'a' * 40, Executor(), Executor())
    scheduler.status.update(healthy_status())
    scheduler.status['groups']['guardian']['checks'][0].update(finding)
    original = copy.deepcopy(scheduler.status['groups'])
    scheduler.rollout_tick(0, 1001)
    assert scheduler.status['rollout']['phase'] == 'switching'
    assert scheduler.transition[0] == 'rollback'
    assert len(scheduler.slow.calls) == 1
    assert scheduler.status['groups'] == original


def test_unstarted_canary_groups_wait_and_full_positive_cycle_promotes(tmp_path):
    scheduler = service.Scheduler(tmp_path, 'nucbox', 'a' * 40, Executor(), Executor())
    scheduler.status.update(healthy_status())
    group = scheduler.status['groups'].pop('doctor')
    scheduler.rollout_tick(0, 1001)
    assert scheduler.status['rollout']['phase'] == 'canary'
    assert not scheduler.slow.calls
    scheduler.status['groups']['doctor'] = group
    scheduler.rollout_tick(1, 1002)
    assert scheduler.status['rollout']['phase'] == 'active'
    assert scheduler.status['rollout']['last_confirmed_at'] == 1002


@pytest.mark.parametrize('started', [None, '995', True, float('nan'), float('inf')])
def test_present_completed_canary_with_invalid_start_rolls_back(tmp_path, started):
    scheduler = service.Scheduler(tmp_path, 'nucbox', 'a' * 40, Executor(), Executor())
    scheduler.status.update(healthy_status())
    scheduler.status['groups']['guardian']['started_at'] = started
    scheduler.rollout_tick(0, 1001)
    assert scheduler.transition[0] == 'rollback'


@pytest.mark.parametrize('group', [None, [], {}, dict(finished_at=1000, execution_completed=True)])
def test_present_corrupt_canary_group_is_not_mistaken_for_missing_initial_group(tmp_path, group):
    scheduler = service.Scheduler(tmp_path, 'nucbox', 'a' * 40, Executor(), Executor())
    scheduler.status.update(healthy_status())
    scheduler.status['groups']['guardian'] = group
    scheduler.rollout_tick(0, 1001)
    assert scheduler.transition[0] == 'rollback'


@pytest.mark.parametrize('since', [None, '990', True, float('nan'), float('inf'), -1, 0, 1002])
def test_invalid_or_future_activation_cannot_hold_unprovable_canary_ownership(tmp_path, since):
    scheduler = service.Scheduler(tmp_path, 'nucbox', 'a' * 40, Executor(), Executor())
    scheduler.status.update(healthy_status())
    scheduler.status['rollout']['activated_at'] = since
    scheduler.rollout_tick(0, 1001)
    assert scheduler.transition[0] == 'rollback'


def test_valid_pre_activation_failure_waits_for_a_new_probe_without_rolling_back(tmp_path):
    scheduler = service.Scheduler(tmp_path, 'nucbox', 'a' * 40, Executor(), Executor())
    scheduler.status.update(healthy_status())
    scheduler.status['groups']['guardian'].update(started_at=980, finished_at=989,
        checks=[dict(name='dashboard', passed=False)])
    scheduler.rollout_tick(0, 1001)
    assert scheduler.status['rollout']['phase'] == 'canary'
    assert scheduler.transition is None


@pytest.mark.parametrize('pending', ['absent', 'pre-activation'])
def test_unqualified_canary_has_a_bounded_deadline_instead_of_owning_recovery_forever(tmp_path, pending):
    scheduler = service.Scheduler(tmp_path, 'nucbox', 'a' * 40, Executor(), Executor())
    scheduler.status.update(healthy_status())
    if pending == 'absent':
        scheduler.status['groups'] = {}
    else:
        for group in scheduler.status['groups'].values():
            group.update(started_at=980, finished_at=989)
    deadline = 990 + 2 * sum(TIMEOUTS.values()) + 90
    scheduler.rollout_tick(0, deadline)
    assert scheduler.status['rollout']['phase'] == 'canary'
    assert scheduler.transition is None
    scheduler.rollout_tick(1, deadline + .001)
    assert scheduler.status['rollout']['phase'] == 'switching'
    assert scheduler.transition[0] == 'rollback'


def test_positive_canary_can_promote_before_existing_probe_budget_deadline(tmp_path):
    scheduler = service.Scheduler(tmp_path, 'nucbox', 'a' * 40, Executor(), Executor())
    scheduler.status.update(healthy_status())
    now = 990 + 2 * sum(TIMEOUTS.values()) + 89
    for group in scheduler.status['groups'].values():
        group.update(started_at=now - 5, finished_at=now)
    scheduler.rollout_tick(0, now)
    assert scheduler.status['rollout']['phase'] == 'active'
    assert scheduler.status['rollout']['last_confirmed_at'] == now
    assert scheduler.transition is None


def test_active_failure_preserves_repairs_but_does_not_refresh_admission(tmp_path):
    scheduler = service.Scheduler(tmp_path, 'nucbox', 'a' * 40, Executor(), Executor())
    scheduler.status.update(healthy_status())
    scheduler.status['rollout'].update(phase='active', last_confirmed_at=1000)
    scheduler.status['groups']['guardian']['checks'][0]['passed'] = False
    scheduler.rollout_tick(0, 1001)
    assert scheduler.status['rollout']['phase'] == 'active'
    assert scheduler.status['rollout']['last_confirmed_at'] == 1000
    assert not scheduler.slow.calls


@pytest.mark.parametrize('mismatch', ['missing', 'active', 'enabled', 'installed', 'nonboolean'])
def test_rollback_requires_verified_saved_native_state_before_releasing_authority(tmp_path, mismatch):
    value = healthy_status()
    previous = {name: dict(installed=True, enabled=True, active=True) for name in rollout.RETIRED}
    value['rollout']['previous_units'] = copy.deepcopy(previous)
    manager = rollout.Manager('nucbox', tmp_path)
    manager.restore = Mock()
    observed = copy.deepcopy(previous)
    if mismatch == 'missing':
        observed.pop('watchdog')
    elif mismatch == 'nonboolean':
        observed['watchdog']['active'] = 1
    else:
        observed['watchdog'][mismatch] = False
    manager.snapshot = Mock(return_value=observed)
    (tmp_path / '.health-authority').touch()
    (tmp_path / '.health-rollback-request').touch()
    with pytest.raises(RuntimeError):
        rollout.rollback(tmp_path, value, manager)
    assert value['rollout']['phase'] == 'transition-failed'
    assert service.read_status(tmp_path)['rollout']['phase'] == 'transition-failed'
    assert (tmp_path / '.health-authority').exists()
    assert (tmp_path / '.health-rollback-request').exists()
    manager.restore.assert_called_once_with(previous)


def test_verified_rollback_restores_exact_saved_states_then_releases_authority(tmp_path):
    value = healthy_status()
    previous = {name: dict(installed=True, enabled=name != 'shadow', active=name != 'shadow')
                for name in rollout.RETIRED}
    value['rollout']['previous_units'] = copy.deepcopy(previous)
    manager = rollout.Manager('nucbox', tmp_path)
    manager.restore = Mock()
    manager.snapshot = Mock(return_value=copy.deepcopy(previous))
    (tmp_path / '.health-authority').touch()
    (tmp_path / '.health-rollback-request').touch()
    result = rollout.rollback(tmp_path, value, manager)
    assert result['phase'] == 'rolled-back'
    manager.restore.assert_called_once_with(previous)
    manager.snapshot.assert_called_once_with()
    assert not (tmp_path / '.health-authority').exists()
    assert not (tmp_path / '.health-rollback-request').exists()


@pytest.mark.parametrize('compensation', ['mismatch', 'unknown', 'restore-failure', 'success'])
def test_failed_activation_requires_verified_compensation_before_releasing_authority(
        tmp_path, compensation):
    value = healthy_status()
    old = dict(phase='observe', head='a' * 40)
    value['rollout'] = copy.deepcopy(old)
    previous = {name: dict(installed=True, enabled=True, active=True) for name in rollout.RETIRED}
    observed = copy.deepcopy(previous)
    if compensation == 'mismatch':
        observed['watchdog']['active'] = False
    manager = rollout.Manager('nucbox', tmp_path)
    manager.snapshot = Mock(side_effect=[previous, OSError('private observation')
                                        if compensation == 'unknown' else observed])
    manager.disable = Mock(side_effect=RuntimeError('private initial failure'))
    manager.restore = Mock(side_effect=RuntimeError('private restore failure')
                           if compensation == 'restore-failure' else None)
    with pytest.raises((RuntimeError, OSError)):
        rollout.activate(tmp_path, value, manager, 1001)
    manager.restore.assert_called_once_with(previous)
    if compensation == 'success':
        assert value['rollout'] == old
        assert manager.snapshot.call_count == 2
        assert not (tmp_path / '.health-authority').exists()
    else:
        assert value['rollout']['phase'] == 'transition-failed'
        assert (tmp_path / '.health-authority').exists()
        persisted = service.read_status(tmp_path)
        assert persisted['rollout']['phase'] == 'transition-failed'
        assert persisted['rollout']['previous_units'] == previous
        assert 'private' not in str(persisted)


@pytest.mark.parametrize('code,output', [(1, ''), (1, 'private bus error'),
    (0, ''), (0, 'unknown'), (1, 'enabled'), (0, 'disabled')])
def test_linux_unknown_enable_state_cannot_verify_saved_disabled_state(tmp_path, code, output):
    manager = rollout.Manager('nucbox', tmp_path)
    manager.run = Mock(side_effect=lambda argv: subprocess.CompletedProcess(
        argv, code if 'is-enabled' in argv else 3,
        output if 'is-enabled' in argv else 'inactive', ''))
    with pytest.raises(RuntimeError, match='inspect'):
        manager.snapshot()


@pytest.mark.parametrize('code,output', [(1, ''), (1, 'private bus error'),
    (0, ''), (0, 'unknown'), (3, 'active'), (0, 'inactive')])
def test_linux_unknown_activity_cannot_verify_saved_inactive_state(tmp_path, code, output):
    manager = rollout.Manager('nucbox', tmp_path)
    manager.run = Mock(side_effect=lambda argv: subprocess.CompletedProcess(
        argv, code if 'is-active' in argv else 1,
        output if 'is-active' in argv else 'disabled', ''))
    with pytest.raises(RuntimeError, match='inspect'):
        manager.snapshot()


@pytest.mark.parametrize('enabled,active', [(True, True), (False, False), (False, True)])
def test_linux_explicit_native_states_are_observed_without_mutation(tmp_path, enabled, active):
    units = tmp_path / '.config/systemd/user'
    units.mkdir(parents=True)
    for name in rollout.RETIRED:
        (units / ('storypool-' + name + '.timer')).touch()
    def result(argv):
        if 'is-enabled' in argv:
            return subprocess.CompletedProcess(argv, 0 if enabled else 1,
                                               'enabled' if enabled else 'disabled', '')
        return subprocess.CompletedProcess(argv, 0 if active else 3,
                                           'active' if active else 'inactive', '')
    manager = rollout.Manager('nucbox', tmp_path)
    manager.run = Mock(side_effect=result)
    assert manager.snapshot() == {name: dict(installed=True, enabled=enabled, active=active)
                                  for name in rollout.RETIRED}
    assert all(call.args[0][2] in ('is-enabled', 'is-active')
               for call in manager.run.call_args_list)
