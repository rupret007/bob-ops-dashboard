"""Adapters retain legacy observations and never mutate in comparison mode."""
from unittest.mock import Mock, patch

from cluster.control_plane import guardian, health_adapters as adapter, watchdog


def test_watchdog_retains_every_field_and_never_runs_repairs():
    checks = [watchdog.HealthCheck('dispatch_stuck', False, 'stale log',
                                  repair_action='restart_service')]
    result = watchdog.WatchdogResult('now', 'nucbox', checks=checks)
    with patch.object(watchdog, 'run_watchdog', return_value=result) as run, \
            patch.object(watchdog, 'repair_restart_service') as repair:
        observed = adapter.collect('watchdog', 'nucbox', False)
    run.assert_called_once_with('nucbox', check_only=True)
    repair.assert_not_called()
    assert observed['checks'] == [checks[0].to_dict()]
    assert observed['details'] == result.to_dict()


def test_guardian_comparison_is_read_only_and_live_disables_legacy_alerts():
    with patch.object(guardian, 'recover', return_value=[{'unit': 'missing', 'ok': False}]) as recover, \
            patch.object(guardian, 'guard_dashboard', return_value={'ok': False, 'restarted': False}) as dashboard:
        result = adapter.collect('guardian', 'mini', False)
    assert not all(c['passed'] for c in result['checks'])
    recover.assert_called_once_with('mini', observe_only=True)
    dashboard.assert_called_once_with('mini', observe_only=True, emit_alert=False)


def test_watchdog_failed_restart_return_code_is_not_success():
    with patch.object(watchdog.subprocess, 'run', return_value=Mock(returncode=1)):
        assert not watchdog.repair_restart_service('dispatch', 'nucbox')


def test_live_watchdog_only_repairs_allowlisted_local_failures():
    checks = [watchdog.HealthCheck('dispatch_stuck', False, 'stuck', repair_action='restart_service'),
              watchdog.HealthCheck('unexpected_stuck', False, 'untrusted', repair_action='restart_service')]
    with patch.object(watchdog, 'run_watchdog', return_value=watchdog.WatchdogResult('now', 'nucbox', checks=checks)), \
            patch.object(watchdog, 'repair_restart_service', return_value=False) as repair:
        result = adapter.collect('watchdog', 'nucbox', True)
    repair.assert_called_once_with('dispatch', 'nucbox')
    assert not result['checks'][0]['repaired']


def test_doctor_preserves_diagnostics_and_distinguishes_finding_from_execution_error():
    for code, failed, execution in [(0, False, False), (1, True, False), (2, True, True)]:
        with patch.object(adapter.subprocess, 'run', return_value=Mock(returncode=code, stdout='SMOKE evidence', stderr='')):
            result = adapter.collect('doctor', 'mini', False)
        assert result['checks'][0]['passed'] is not failed
        assert result['checks'][0]['execution_error'] is execution
        assert result['details']['diagnostics'] == 'SMOKE evidence'
