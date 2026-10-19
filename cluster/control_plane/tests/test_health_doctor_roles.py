"""Mini observes the Nuc's real pipeline; it never manufactures local fix jobs."""
import copy
import json
import subprocess
from unittest.mock import Mock

import pytest

from cluster.control_plane import health_adapters as adapters, health_peer
from cluster.control_plane import health_rollout as rollout, health_service as service
from cluster.control_plane import self_update
from cluster.control_plane.tests.test_health_service import Executor


HEAD = 'a' * 40


def observation(served=1000, received=1000):
    peer = dict(schema=1, machine='nucbox', loaded_head=HEAD, timestamp=served - 5,
                observation=dict(nonce='b' * 32, served_at=served),
                rollout=dict(phase='canary', head=HEAD, activated_at=served - 20),
                groups=dict(doctor=dict(started_at=served - 10, finished_at=served - 5,
                    execution_completed=True, checks=[dict(name='pipeline_smoke', passed=True)],
                    details=dict(exit_code=0, diagnostics='private remote evidence'))))
    return health_peer.direct_observation(peer, 'nucbox', 'b' * 32, received, .1)


@pytest.fixture
def mini(monkeypatch):
    details = observation()
    collector = Mock(return_value=dict(checks=[dict(name='peer_freshness', passed=True)],
                                       details=details))
    monkeypatch.setattr(adapters, 'collect_peer', collector)
    monkeypatch.setattr(adapters.time, 'time', lambda: 1001)
    local = Mock(side_effect=AssertionError('Mini must not enqueue or run a local smoke'))
    monkeypatch.setattr(adapters.subprocess, 'run', local)
    return details, collector, local


def collect_mini():
    return adapters.collect('doctor', 'mini', False, expected_head=HEAD)


@pytest.mark.parametrize('phase', ['canary', 'active'])
def test_mini_reuses_positive_nuc_doctor_without_requiring_the_whole_peer_cycle(mini, monkeypatch, phase):
    details, collector, local = mini
    details['health']['rollout']['phase'] = phase
    original = copy.deepcopy(details)
    positive = Mock(wraps=rollout.positive_group)
    monkeypatch.setattr(rollout, 'positive_group', positive)
    monkeypatch.setattr(rollout, 'complete_cycle', Mock(side_effect=AssertionError('recursive qualification')))
    result = collect_mini()
    assert result['checks'][0]['passed'] is True
    assert result['checks'][0]['unknown'] is False
    collector.assert_called_once_with('mini')
    local.assert_not_called()
    positive.assert_called_once_with(details['health']['groups']['doctor'], 'doctor',
                                     pytest.approx(1001.1), 980)
    assert result['details']['source_machine'] == 'nucbox'
    assert result['details']['source_head'] == HEAD
    assert result['details']['source_finished_at'] == 995
    assert 'private remote evidence' not in json.dumps(result)
    assert 'health' not in result['details']
    assert details == original


@pytest.mark.parametrize('code', [0, 1, 2])
def test_nuc_keeps_the_actual_local_smoke_and_original_failure_semantics(monkeypatch, code):
    run = Mock(return_value=subprocess.CompletedProcess([], code, 'original smoke evidence', ''))
    peer = Mock(side_effect=AssertionError('Nuc doctor must not depend on Mini'))
    monkeypatch.setattr(adapters.subprocess, 'run', run)
    monkeypatch.setattr(adapters, 'collect_peer', peer)
    result = adapters.collect('doctor', 'nucbox', False, expected_head=HEAD)
    assert result['checks'][0]['passed'] is (code == 0)
    assert result['checks'][0]['unknown'] is (code == 1)
    assert result['checks'][0]['execution_error'] is (code not in (0, 1))
    assert result['details']['exit_code'] == code
    assert result['details']['diagnostics'] == 'original smoke evidence'
    assert run.call_args.args[0][-2:] == ['-m', 'cluster.control_plane.smoke']
    assert '--findings-only' not in run.call_args.args[0]
    peer.assert_not_called()


@pytest.mark.parametrize('head', [None, '', 'a' * 12, 'A' * 40, True, 1, []])
def test_mini_missing_or_invalid_scheduler_head_cannot_collect_or_pass(mini, head):
    result = adapters.collect('doctor', 'mini', False, expected_head=head)
    assert result['checks'][0]['passed'] is False
    assert result['checks'][0]['unknown'] is True
    mini[1].assert_not_called()
    mini[2].assert_not_called()


@pytest.mark.parametrize('field,value', [('machine', 'mini'), ('schema', 2), ('schema', True),
    ('loaded_head', 'c' * 40), ('loaded_head', None), ('timestamp', None), ('timestamp', True),
    ('timestamp', float('nan')), ('timestamp', float('inf')), ('timestamp', 849),
    ('timestamp', 1001)])
def test_wrong_role_source_or_malformed_source_status_cannot_pass(mini, field, value):
    mini[0]['health'][field] = value
    result = collect_mini()
    assert result['checks'][0]['passed'] is False
    assert result['checks'][0]['unknown'] is True


@pytest.mark.parametrize('field,value', [('phase', 'observe'), ('phase', 'rolled-back'),
    ('phase', 'switching'), ('phase', 'transition-failed'), ('head', 'c' * 40), ('head', None),
    ('activated_at', None), ('activated_at', True), ('activated_at', float('nan')),
    ('activated_at', float('inf')), ('activated_at', -1), ('activated_at', 0),
    ('activated_at', 1001)])
def test_wrong_or_invalid_rollout_provenance_cannot_pass(mini, field, value):
    mini[0]['health']['rollout'][field] = value
    assert collect_mini()['checks'][0]['passed'] is False


@pytest.mark.parametrize('change', [dict(execution_completed=False), dict(execution_completed='true'),
    dict(unknown=True), dict(error='TimeoutError'), dict(checks=[]), dict(checks=[None]),
    dict(checks=[dict(name='pipeline_smoke', passed=False)]),
    dict(checks=[dict(name='pipeline_smoke', passed=False, repaired=True)]),
    dict(checks=[dict(name='pipeline_smoke', passed=True, unknown=True)]),
    dict(checks=[dict(name='pipeline_smoke', passed=1)]),
    dict(checks=[dict(name='pipeline_smoke', passed=True, execution_error=True)]),
    dict(checks=[dict(name='pipeline_smoke', passed=True, message='Evidence unverified')]),
    dict(started_at=979), dict(started_at=None), dict(started_at=True),
    dict(finished_at=1001), dict(finished_at=None)])
def test_nonpositive_unattested_or_pre_activation_doctor_cannot_pass(mini, change):
    mini[0]['health']['groups']['doctor'].update(change)
    assert collect_mini()['checks'][0]['passed'] is False


@pytest.mark.parametrize('checks', [[dict(name='unrelated', passed=True)],
    [dict(name='pipeline_smoke', passed=True), dict(name='unrelated', passed=True)]])
def test_unrelated_or_changed_check_coverage_cannot_replace_actual_pipeline_smoke(mini, checks):
    mini[0]['health']['groups']['doctor']['checks'] = checks
    assert collect_mini()['checks'][0]['passed'] is False


@pytest.mark.parametrize('detail', [None, [], {}, dict(exit_code=True), dict(exit_code=False),
    dict(exit_code='0'), dict(exit_code=1), dict(exit_code=2), dict(exit_code=float('nan'))])
def test_missing_malformed_or_contradictory_smoke_exit_cannot_pass(mini, detail):
    mini[0]['health']['groups']['doctor']['details'] = detail
    assert collect_mini()['checks'][0]['passed'] is False


@pytest.mark.parametrize('field,value', [('health', None), ('health', []),
    ('clock_observation', None), ('clock_observation', {}), ('received_at', None),
    ('received_at', True), ('received_at', 1002), ('received_at', 500)])
def test_missing_direct_observation_or_invalid_clock_envelope_cannot_pass(mini, field, value):
    mini[0][field] = value
    assert collect_mini()['checks'][0]['passed'] is False


@pytest.mark.parametrize('field,value', [('rollout', None), ('rollout', []), ('groups', None),
    ('groups', []), ('groups', {}), ('groups', dict(doctor=None))])
def test_malformed_nested_evidence_cannot_crash_or_pass(mini, field, value):
    mini[0]['health'][field] = value
    assert collect_mini()['checks'][0]['passed'] is False


@pytest.mark.parametrize('field,value', [('served_at', True), ('served_at', float('nan')),
    ('served_at', float('inf')), ('round_trip_seconds', True), ('round_trip_seconds', -1),
    ('round_trip_seconds', 5.001), ('round_trip_seconds', float('nan'))])
def test_invalid_peer_clock_numbers_cannot_pass(mini, field, value):
    mini[0]['clock_observation'][field] = value
    assert collect_mini()['checks'][0]['passed'] is False


def test_clock_envelope_must_name_the_nuc_it_actually_observed(mini):
    mini[0]['machine'] = 'mini'
    assert collect_mini()['checks'][0]['passed'] is False


@pytest.mark.parametrize('offset', [-3600, 3600])
def test_mini_uses_the_direct_peer_clock_instead_of_assuming_synchronized_hosts(mini, offset):
    mini[1].return_value['details'] = observation(served=10000 + offset, received=1000)
    assert collect_mini()['checks'][0]['passed'] is True


def test_fresh_source_snapshot_cannot_freshen_an_old_doctor_result(mini):
    details = observation(served=10000, received=1000)
    details['health']['rollout']['activated_at'] = 1
    details['health']['groups']['doctor'].update(started_at=6000, finished_at=6001)
    mini[1].return_value['details'] = details
    assert collect_mini()['checks'][0]['passed'] is False


def test_transport_failure_stays_unknown_without_leaking_private_exception_text(mini):
    mini[1].side_effect = OSError('private endpoint detail')
    result = collect_mini()
    assert result['checks'][0]['passed'] is False
    assert result['checks'][0]['unknown'] is True
    assert 'private endpoint detail' not in json.dumps(result)


def test_positive_peer_doctor_does_not_bypass_full_nuc_deployment_admission(mini, monkeypatch):
    mini[0]['health']['rollout']['phase'] = 'active'
    assert collect_mini()['checks'][0]['passed'] is True
    assert not self_update.target_canary_ready('mini', HEAD)


def test_scheduler_binds_every_adapter_to_its_loaded_head_without_reading_old_disk_state(tmp_path):
    service.atomic_json(tmp_path / 'health-status.json', dict(machine='mini', loaded_head='c' * 40))
    scheduler = service.Scheduler(tmp_path, 'mini', HEAD, Executor(), Executor())
    scheduler.tick(0, 1000)
    assert all(args[3] == HEAD for pool in (scheduler.fast, scheduler.slow) for args, _ in pool.calls)


def test_bounded_adapter_runner_passes_scheduler_head_to_child(monkeypatch):
    output = Mock(return_value=json.dumps(dict(checks=[dict(name='pipeline_smoke', passed=True)])))
    monkeypatch.setattr(service, 'bounded_output', output)
    assert service.execute('doctor', 'mini', False, HEAD)['execution_completed'] is True
    argv = output.call_args.args[0]
    assert argv[-2:] == ['--expected-head', HEAD]


def test_adapter_cli_preserves_the_explicit_loaded_head(monkeypatch, capsys):
    monkeypatch.setenv('STORYPOOL_HEALTH_WORKER', '0')
    collect = Mock(return_value=dict(checks=[dict(name='pipeline_smoke', passed=False, unknown=True)]))
    monkeypatch.setattr(adapters, 'collect', collect)
    monkeypatch.setattr(adapters.sys, 'argv', ['health_adapters', 'doctor', '--machine', 'mini',
                                            '--expected-head', HEAD])
    adapters.main()
    collect.assert_called_once_with('doctor', 'mini', False, expected_head=HEAD)
    assert json.loads(capsys.readouterr().out)['checks'][0]['passed'] is False


def test_independent_comparison_binds_doctor_to_the_same_loaded_head(tmp_path, monkeypatch):
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(rollout.watchdog, 'run_watchdog', Mock(return_value=Mock(to_dict=lambda: dict(checks=[]))))
    collect = Mock(return_value=dict(checks=[], details={}))
    monkeypatch.setattr(rollout, 'collect', collect)
    value = dict(machine='mini', loaded_head=HEAD,
                 groups=dict(watchdog=dict(checks=[]), doctor=dict(checks=[]), shadow=dict(details={})))
    rollout.compare_cycle(value)
    assert collect.call_args_list[0].args == ('doctor', 'mini', False)
    assert collect.call_args_list[0].kwargs == dict(expected_head=HEAD)
