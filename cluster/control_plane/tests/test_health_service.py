"""Fake-clock scheduling and durable per-group recovery damping."""
from concurrent.futures import Future
from unittest.mock import Mock
import json

import pytest

from cluster.control_plane import health_service as service
from cluster.control_plane.health_adapters import PERIODS


class Executor:
    def __init__(self):
        self.calls = []

    def submit(self, function, *args):
        future = Future()
        self.calls.append((args, future))
        return future


def test_one_loop_preserves_cadence_and_slow_check_cannot_block_fast(tmp_path):
    fast, slow = Executor(), Executor()
    scheduler = service.Scheduler(tmp_path, 'nucbox', 'a' * 40, fast, slow)
    scheduler.tick(0, 100)
    assert {args[0] for args, _ in fast.calls} == {'guardian', 'flow'}
    assert {args[0] for args, _ in slow.calls} == set(PERIODS) - {'guardian', 'flow'}
    for args, future in fast.calls:
        future.set_result({'checks': [{'name': args[0], 'passed': True}], 'details': {}})
    scheduler.tick(5, 105)
    scheduler.tick(120, 220)
    assert len(fast.calls) == 4
    assert len(slow.calls) == len(PERIODS) - 2  # Still running; never duplicate.
    assert scheduler.status['timestamp'] == 220
    assert service.read_status(tmp_path)['groups']['guardian']['checks'][0]['passed']


def test_timeout_is_unknown_not_green_and_does_not_resolve(tmp_path):
    fast, slow = Executor(), Executor()
    scheduler = service.Scheduler(tmp_path, 'mini', 'a' * 40, fast, slow)
    scheduler.tick(0, 100)
    fast.calls[0][1].set_exception(TimeoutError('unsafe captured text'))
    scheduler.tick(5, 105)
    group = scheduler.status['groups'][fast.calls[0][0][0]]
    assert group['error'] == 'TimeoutError'
    assert 'unsafe' not in str(group)
    assert not group['checks'][0]['passed']


def test_repair_cooldown_survives_daemon_restart(tmp_path):
    fast, slow = Executor(), Executor()
    scheduler = service.Scheduler(tmp_path, 'nucbox', 'a' * 40, fast, slow)
    scheduler.status['rollout']['phase'] = 'active'
    scheduler.tick(0, 1000)
    args, future = next(row for row in fast.calls if row[0][0] == 'guardian')
    assert args[2]
    future.set_result({'checks': [{'name': 'dashboard', 'passed': False}],
                       'details': {}, 'repair_attempted': True})
    scheduler.tick(5, 1005)
    restarted = service.Scheduler(tmp_path, 'nucbox', 'a' * 40, Executor(), Executor())
    restarted.tick(0, 1100)
    args = next(row[0] for row in restarted.fast.calls if row[0][0] == 'guardian')
    assert not args[2]


def test_public_snapshot_cannot_echo_peer_snapshots(tmp_path):
    nested = {'schema': 1, 'machine': 'nucbox', 'groups': {
        'peer': {'checks': [{'name': 'peer_freshness', 'passed': True}],
                 'details': {'health': {'private': 'recursive'}}},
        'guardian': {'checks': [{'name': 'dashboard', 'passed': True}], 'details': {}}}}
    snapshot = service.public_snapshot(nested)
    assert 'recursive' not in str(snapshot)
    assert snapshot['groups']['guardian'] == nested['groups']['guardian']


def test_new_revision_reproves_canary_without_resurrecting_legacy(tmp_path):
    service.atomic_json(tmp_path / 'health-status.json', {
        'machine': 'nucbox', 'loaded_head': 'a' * 40, 'groups': {},
        'rollout': {'phase': 'active', 'head': 'a' * 40, 'previous_units': {'saved': True}}})
    scheduler = service.Scheduler(tmp_path, 'nucbox', 'b' * 40, Executor(), Executor())
    assert scheduler.status['rollout']['phase'] == 'canary'
    assert scheduler.status['rollout']['head'] == 'b' * 40
    assert scheduler.status['rollout']['previous_units'] == {'saved': True}


@pytest.mark.parametrize('change', ['ready', 'missing', 'loaded-head', 'receipt-head', 'canary', 'stale'])
def test_active_mini_new_head_cannot_promote_before_matching_nuc_canary(tmp_path, change):
    from cluster.control_plane.tests.test_update_coordination import observed, TARGET, OLD
    scheduler = service.Scheduler(tmp_path, 'mini', TARGET, Executor(), Executor())
    scheduler.status['rollout'] = dict(phase='canary', activated_at=1000, head=TARGET)
    scheduler.status['groups'] = {name: dict(started_at=1000, finished_at=1000,
        checks=[dict(name=name, passed=True)], details={}) for name in PERIODS}
    details = observed()
    if change == 'loaded-head': details['health']['loaded_head'] = OLD
    if change == 'receipt-head': details['health']['rollout']['head'] = OLD
    if change == 'canary': details['health']['rollout']['phase'] = 'canary'
    if change == 'stale': details['received_at'] = 1
    if change != 'missing': scheduler.status['groups']['peer']['details'] = details
    scheduler.rollout_tick(0, 1001)
    assert scheduler.status['rollout']['phase'] == ('active' if change == 'ready' else 'canary')
    assert not scheduler.slow.calls


def test_failed_canary_retries_only_after_code_changes_and_needs_new_checks(tmp_path):
    saved = {'machine': 'nucbox', 'loaded_head': 'a' * 40,
             'groups': {'guardian': {'finished_at': 100, 'checks': [{'passed': True}]}},
             'rollout': {'phase': 'rolled-back', 'rolled_back_at': 90, 'head': 'a' * 40}}
    service.atomic_json(tmp_path / 'health-status.json', saved)
    same = service.Scheduler(tmp_path, 'nucbox', 'a' * 40, Executor(), Executor())
    assert same.status['rollout']['phase'] == 'rolled-back'
    changed = service.Scheduler(tmp_path, 'nucbox', 'b' * 40, Executor(), Executor())
    assert changed.status['rollout']['phase'] == 'observe'
    assert changed.status['rollout']['previous_rollback'] == saved['rollout']
    assert changed.status['groups'] == {}


def failed_transition(tmp_path):
    saved = {'machine': 'mini', 'loaded_head': 'a' * 40,
             'groups': {'guardian': {'finished_at': 100, 'checks': [{'passed': True}]}},
             'rollout': {'phase': 'transition-failed', 'error': 'RuntimeError',
                         'comparison': {'passed': True, 'head': 'a' * 40}}}
    service.atomic_json(tmp_path / 'health-status.json', saved)
    return saved


def restored_manager(monkeypatch):
    from cluster.control_plane import health_rollout as rollout
    manager = Mock()
    manager.snapshot.return_value = {name: dict(installed=True, enabled=True, active=True)
                                     for name in rollout.RETIRED}
    monkeypatch.setattr(rollout, 'Manager', lambda machine: manager)
    return manager


def test_new_revision_recovers_only_to_observe_with_private_failure_archive(tmp_path, monkeypatch):
    saved = failed_transition(tmp_path)
    manager = restored_manager(monkeypatch)
    scheduler = service.Scheduler(tmp_path, 'mini', 'b' * 40, Executor(), Executor())
    assert scheduler.status['rollout']['phase'] == 'observe'
    assert 'comparison' not in scheduler.status['rollout']
    assert scheduler.status['groups'] == {}
    archive = json.loads((tmp_path / ('health-transition-failed-' + 'a' * 40 + '.json')).read_text())
    assert archive['rollout'] == saved['rollout']
    assert archive['verified_units'] == manager.snapshot.return_value
    assert (tmp_path / ('health-transition-failed-' + 'a' * 40 + '.json')).stat().st_mode & 0o777 == 0o600
    assert manager.method_calls == [('snapshot', (), {})]
    scheduler.rollout_tick(0, 1000)
    assert scheduler.status['rollout']['phase'] == 'observe'
    assert not scheduler.slow.calls  # Fresh complete groups must precede comparison.
    assert not (tmp_path / '.health-authority').exists()
    scheduler.status['groups'] = {name: dict(checks=[dict(name=name, passed=True)],
        finished_at=1000, details={}) for name in PERIODS}
    scheduler.rollout_tick(1, 1000)
    assert scheduler.transition[0] == 'compare'
    scheduler.transition[1].set_result(dict(passed=True, head='b' * 40))
    scheduler.rollout_tick(2, 1001)
    assert scheduler.transition is None  # A full comparison still needs the Nuc canary.
    assert scheduler.status['rollout']['phase'] == 'observe'
    assert len(scheduler.slow.calls) == 1


@pytest.mark.parametrize('blocker', ['same-head', 'authority', 'missing', 'installed',
                                    'enabled', 'active', 'unknown', 'nonboolean'])
def test_failed_transition_stays_blocked_without_complete_positive_restoration(tmp_path, monkeypatch, blocker):
    saved = failed_transition(tmp_path)
    manager = restored_manager(monkeypatch)
    head = 'b' * 40
    if blocker == 'same-head':
        head = 'a' * 40
    elif blocker == 'authority':
        (tmp_path / '.health-authority').touch()
    elif blocker == 'missing':
        del manager.snapshot.return_value['shadow']
    elif blocker == 'unknown':
        manager.snapshot.side_effect = RuntimeError('private detail')
    elif blocker == 'nonboolean':
        manager.snapshot.return_value['watchdog']['enabled'] = 1
    else:
        manager.snapshot.return_value['watchdog'][blocker] = False
    scheduler = service.Scheduler(tmp_path, 'mini', head, Executor(), Executor())
    assert scheduler.status['rollout'] == saved['rollout']
    assert not list(tmp_path.glob('health-transition-failed-*.json'))
    assert not any(call[0] in ('disable', 'restore') for call in manager.method_calls)
