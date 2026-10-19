"""Fake-clock scheduling and durable per-group recovery damping."""
from concurrent.futures import Future

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
