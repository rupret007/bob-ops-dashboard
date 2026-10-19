"""Only complete bounded probe execution resolves its scheduler-owned incident."""
from concurrent.futures import Future
import json
import subprocess

import pytest

from cluster.control_plane import health_service as service
from cluster.control_plane import health_state as state


class Executor:
    def __init__(self):
        self.calls = []

    def submit(self, function, *args):
        future = Future()
        self.calls.append((args, future))
        return future


def pending(scheduler, group):
    pool = scheduler.fast if group in ('guardian', 'flow') else scheduler.slow
    return next(future for args, future in reversed(pool.calls) if args[0] == group)


def with_execution_incident(tmp_path, group='peer', machine='mini'):
    scheduler = service.Scheduler(tmp_path, machine, 'a' * 40, Executor(), Executor())
    scheduler.status['rollout']['phase'] = 'active'
    scheduler.tick(0, 100)
    pending(scheduler, group).set_exception(RuntimeError('private exception detail'))
    scheduler.tick(5, 105)
    key = ':'.join((machine, group, group + '_execution'))
    issue = scheduler.status['incidents'][key]
    assert issue['state'] == 'firing'
    assert group + '_execution' in (tmp_path / 'NEEDS_JEFF.md').read_text()
    scheduler.tick(120, 220)
    return scheduler, issue, pending(scheduler, group)


def run_probe(monkeypatch, payload, group='peer'):
    monkeypatch.setattr(service, 'bounded_output', lambda *_: json.dumps(payload))
    return service.execute(group, 'mini', False)


@pytest.mark.parametrize('group', ['peer', 'main_ci'])
@pytest.mark.parametrize('machine', ['mini', 'nucbox'])
def test_completed_probe_archives_only_execution_incident_preserving_semantics(
        tmp_path, monkeypatch, group, machine):
    (tmp_path / 'NEEDS_JEFF.md').write_text('# Jeff\n\nKeep this human action.\n')
    scheduler, issue, future = with_execution_incident(tmp_path, group, machine)
    checks = [dict(name='unknown_finding', passed=False, unknown=True,
                   message='Evidence unverified'),
              dict(name='actual_health_failure', passed=False, message='Unhealthy')]
    state.observe(scheduler.status, machine, group,
                  [dict(c, unknown=False, message='Previously unhealthy') for c in checks], 200)
    semantic = {c['name']: scheduler.status['incidents'][
        ':'.join((machine, group, c['name']))] for c in checks}
    future.set_result(run_probe(monkeypatch, dict(checks=checks), group))
    scheduler.tick(125, 225)

    assert issue['state'] == 'resolved' and issue['resolved_at'] == 225
    assert issue['first_seen'] == 105 and not issue['notify']
    assert scheduler.status['groups'][group]['checks'] == checks
    assert state.unknown(scheduler.status['groups'][group]['checks'][0])
    assert all(i['state'] == 'firing' for i in semantic.values())
    assert state.verdict(scheduler.status, machine, 225)['state'] == 'red'
    alerts = (tmp_path / 'NEEDS_JEFF.md').read_text()
    assert 'Keep this human action.' in alerts
    assert group + '_execution' not in alerts
    assert 'unknown_finding' in alerts and 'actual_health_failure' in alerts
    archive = (tmp_path / 'NEEDS_JEFF.resolved.md').read_text()
    assert archive.count('health-resolved:' + ':'.join((machine, group, group + '_execution'))) == 1
    assert 'unknown_finding' not in archive and 'actual_health_failure' not in archive
    state.render_alerts(tmp_path, scheduler.status)
    assert (tmp_path / 'NEEDS_JEFF.resolved.md').read_text() == archive


@pytest.mark.parametrize('payload', [
    dict(checks=[dict(name='peer_freshness', passed=True)], error='RuntimeError'),
    dict(checks=[dict(name='peer_freshness', passed=True)], unknown=True),
    dict(checks=[dict(name='peer_freshness', passed=False, execution_error=True)]),
    dict(checks=[dict(name='peer_freshness', passed=True, execution_error=True)]),
])
def test_unknown_or_failed_execution_cannot_resolve(tmp_path, monkeypatch, payload):
    scheduler, issue, future = with_execution_incident(tmp_path)
    future.set_result(run_probe(monkeypatch, payload))
    scheduler.tick(125, 225)
    assert issue['state'] == 'firing' and issue['notify']
    assert 'peer_execution' in (tmp_path / 'NEEDS_JEFF.md').read_text()
    assert not (tmp_path / 'NEEDS_JEFF.resolved.md').exists()


@pytest.mark.parametrize('proof', [None, False])
def test_unattested_success_cannot_clear_execution_incident(tmp_path, proof):
    scheduler, issue, future = with_execution_incident(tmp_path)
    reply = dict(checks=[dict(name='peer_freshness', passed=True)])
    if proof is not None:
        reply['execution_completed'] = proof
    future.set_result(reply)
    scheduler.tick(125, 225)
    assert issue['state'] == 'firing'
    assert not (tmp_path / 'NEEDS_JEFF.resolved.md').exists()


def test_pending_execution_cannot_clear_old_incident(tmp_path):
    scheduler, issue, _ = with_execution_incident(tmp_path)
    scheduler.tick(125, 225)
    assert issue['state'] == 'firing' and 'peer' in scheduler.pending
    assert not (tmp_path / 'NEEDS_JEFF.resolved.md').exists()


@pytest.mark.parametrize('payload', [
    {}, dict(checks=[]), dict(checks=None), dict(checks='not a list'),
    dict(checks=[None]), dict(checks=[dict(name='peer_freshness', passed=1)]),
    dict(checks=[dict(name='', passed=True)]),
    dict(checks=[dict(name='peer_freshness', passed=True, message=None)]),
    dict(checks=[dict(name='peer_freshness', passed=True, execution_error=0)]),
    dict(checks=[dict(name='peer_freshness', passed=True, unknown='false')]),
    dict(checks=[dict(name='peer_freshness', passed=True, repaired='true')]),
    dict(checks=[dict(name='peer_freshness', passed=True)] * 2),
    dict(checks=[dict(name='peer_execution', passed=True)], execution_completed=True),
    dict(checks=[dict(name='peer_freshness', passed=True)], details=None),
    dict(checks=[dict(name='peer_freshness', passed=True)], error=False),
    dict(checks=[dict(name='peer_freshness', passed=True)], execution_completed='true'),
    dict(checks=[dict(name='peer_freshness', passed=True)], unknown='false'),
])
def test_missing_or_malformed_completion_cannot_resolve_or_crash_scheduler(tmp_path, payload):
    scheduler, issue, future = with_execution_incident(tmp_path)
    future.set_result(payload)
    scheduler.tick(125, 225)
    assert scheduler.status['groups']['peer']['error'] == 'ValueError'
    assert issue['state'] == 'firing'
    assert not (tmp_path / 'NEEDS_JEFF.resolved.md').exists()


@pytest.mark.parametrize('error', [
    subprocess.TimeoutExpired(['private argv'], 15),
    subprocess.CalledProcessError(2, ['private argv']),
    ValueError('private malformed JSON'),
])
def test_bounded_child_failure_is_not_execution_recovery(tmp_path, monkeypatch, error):
    scheduler, issue, future = with_execution_incident(tmp_path)
    monkeypatch.setattr(service, 'bounded_output', lambda *_: (_ for _ in ()).throw(error))
    with pytest.raises(type(error)) as caught:
        service.execute('peer', 'mini', False)
    future.set_exception(caught.value)
    scheduler.tick(125, 225)
    assert issue['state'] == 'firing'
    assert 'private' not in str(scheduler.status['groups']['peer'])


@pytest.mark.parametrize('elapsed', [15.001, -1, float('nan'), float('inf')])
def test_completion_deadline_is_parent_measured_and_cannot_be_forged(
        tmp_path, monkeypatch, elapsed):
    scheduler, issue, future = with_execution_incident(tmp_path)
    clock = iter((10, 10 + elapsed))
    with monkeypatch.context() as m:
        m.setattr(service.time, 'monotonic', lambda: next(clock))
        with pytest.raises(TimeoutError) as caught:
            run_probe(m, dict(checks=[dict(name='peer_freshness', passed=True)],
                              execution_completed=True))
    future.set_exception(caught.value)
    scheduler.tick(125, 225)
    assert issue['state'] == 'firing'
    assert scheduler.status['groups']['peer']['error'] == 'TimeoutError'


def test_validated_child_exit_at_deadline_supplies_positive_execution_proof(monkeypatch):
    clock = iter((10, 25))
    monkeypatch.setattr(service.time, 'monotonic', lambda: next(clock))
    result = run_probe(monkeypatch, dict(checks=[dict(name='peer_freshness', passed=True)]))
    assert service.execution_succeeded(result)


@pytest.mark.parametrize('payload', [{}, dict(checks=[]),
                                  dict(checks=[dict(name='peer_freshness', passed='true')])])
def test_runner_validates_adapter_reply_before_issuing_completion_proof(monkeypatch, payload):
    with pytest.raises(ValueError):
        run_probe(monkeypatch, payload)
