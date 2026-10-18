"""Regression coverage for bounded checks and findings versus execution errors."""
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from cluster.control_plane import ops_doctor as doctor, smoke, watchdog, watchdog_check


def test_watchdog_template_preserves_timeout_override():
    template = Path(watchdog.__file__).parent / 'install/storypool-watchdog.service'
    assert 'TimeoutStartSec=600' in template.read_text()


def test_real_check_subprocess_returns_health_result():
    result = watchdog_check.run_check(watchdog.check_gh_in_path)
    assert result.name == 'gh_available'
    assert not result.execution_error


def test_deadline_terminates_entire_process_group(tmp_path):
    child_file = tmp_path / 'child.pid'
    script = ('import subprocess, sys, time; from pathlib import Path; '
              'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]); '
              'Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)')
    process = subprocess.Popen([sys.executable, '-c', script, str(child_file)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    deadline = time.monotonic() + 3
    while not child_file.exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    child_pid = int(child_file.read_text())
    with patch.object(watchdog_check.subprocess, 'Popen', return_value=process), \
            patch.object(watchdog_check.os, 'killpg', wraps=os.killpg) as kill:
        with pytest.raises(subprocess.TimeoutExpired):
            watchdog_check.bounded_output(['test'], 0.05)
    kill.assert_called_once_with(process.pid, signal.SIGKILL)
    assert process.returncode == -signal.SIGKILL
    state = subprocess.run(['ps', '-o', 'stat=', '-p', str(child_pid)],
                           capture_output=True, text=True).stdout.strip()
    assert not state or state.startswith('Z'), 'Check descendant survived the deadline'


@pytest.mark.parametrize('error', [subprocess.TimeoutExpired('check', 20),
                                 subprocess.CalledProcessError(1, ['check']),
                                 ValueError('unsafe output')])
def test_failed_check_execution_never_becomes_healthy(error):
    with patch.object(watchdog_check, 'bounded_output', side_effect=error):
        result = watchdog_check.run_check(watchdog.check_head_lag)
    assert not result.passed and result.execution_error
    assert not result.can_repair and not result.repaired
    assert 'unsafe output' not in result.message


def test_checks_overlap_and_check_only_cannot_repair(tmp_path):
    barrier = threading.Barrier(4)
    active = 0
    maximum = 0
    lock = threading.Lock()

    def check(function, *args):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        if function in (watchdog.check_service_stuck, watchdog.check_tracebacks):
            barrier.wait(timeout=3)
        with lock:
            active -= 1
        return watchdog.HealthCheck('conductor_stuck', False, 'stuck',
                                    repair_action='restart_service')

    with patch('cluster.control_plane.node_control.is_draining', return_value=False), \
            patch.object(watchdog_check, 'run_check', side_effect=check), \
            patch.object(watchdog, '_write_watchdog_log'), \
            patch.object(watchdog, 'repair_restart_service') as restart, \
            patch.object(watchdog, 'repair_create_labels') as labels, \
            patch.object(watchdog, 'repair_self_update') as update, \
            patch.object(watchdog, '_escalate_to_jeff') as escalate:
        result = watchdog.run_watchdog('nucbox', check_only=True)
    assert maximum == 4
    assert len(result.checks) == 13
    assert all(not check.passed for check in result.checks)
    for action in (restart, labels, update, escalate):
        action.assert_not_called()


@pytest.mark.parametrize('outcome,expected', [(0, 0), (1, 1), (2, 1), (-9, 1),
                                           (subprocess.TimeoutExpired('check', 600), 1),
                                           (FileNotFoundError('python'), 1)])
def test_doctor_findings_only_exit_contract(tmp_path, monkeypatch, outcome, expected):
    monkeypatch.setenv('STORYPOOL_DOCTOR_DIR', str(tmp_path))
    response = (dict(side_effect=outcome) if isinstance(outcome, Exception) else
                dict(return_value=subprocess.CompletedProcess([], outcome)))
    with patch('cluster.control_plane.node_control.is_draining', return_value=False), \
            patch.object(doctor.subprocess, 'run', **response) as command, \
            patch.object(doctor, 'repair') as repair, \
            patch.object(doctor, 'escalate') as escalate:
        assert doctor.main(['--findings-only']) == expected
    repair.assert_not_called()
    escalate.assert_not_called()
    assert '--check-only' in command.call_args_list[1].args[0]
    assert all('--findings-only' in call.args[0] for call in command.call_args_list)
    assert 'CHECK ' in next(tmp_path.glob('*.log')).read_text()


def test_repair_failure_still_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setenv('STORYPOOL_DOCTOR_DIR', str(tmp_path))
    with patch('cluster.control_plane.node_control.is_draining', return_value=False), \
            patch.object(doctor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)), \
            patch.object(doctor, 'repair', side_effect=RuntimeError('repair failed')), \
            patch.object(doctor, 'escalate') as escalate:
        assert doctor.main([]) == 1
    escalate.assert_called_once()


def test_both_doctor_installers_default_to_findings_only():
    import plistlib
    install = Path(doctor.__file__).parent / 'install'
    assert '--findings-only' in (install / 'storypool-ops-doctor.service').read_text()
    plist = plistlib.loads((install / 'com.storypool.ops-doctor.plist').read_bytes())
    assert '--findings-only' in plist['ProgramArguments']


@pytest.mark.parametrize('error,expected', [(smoke.PipelineFinding('waiting'), 1),
                                          (OSError('unsafe details'), 2)])
def test_smoke_distinguishes_findings_from_execution_errors(error, expected, capsys):
    with patch.object(sys, 'argv', ['smoke']), \
            patch.object(smoke, 'get_ledger') as ledger, \
            patch.object(smoke.KarenConfig, 'load'), \
            patch.object(smoke, 'verify', side_effect=error):
        ledger.return_value.get_recent_jobs.return_value = [type('Job', (), {'created_at': 'now'})()]
        assert smoke.main() == expected
    assert 'unsafe details' not in capsys.readouterr().out


@pytest.mark.parametrize('error,expected', [(smoke.PipelineFinding('waiting'), 0),
                                          (OSError('failed'), 2)])
def test_smoke_findings_only_keeps_real_errors_nonzero(error, expected):
    with patch.object(sys, 'argv', ['smoke', '--findings-only']), \
            patch.object(smoke, 'get_ledger') as ledger, \
            patch.object(smoke.KarenConfig, 'load'), \
            patch.object(smoke, 'verify', side_effect=error):
        ledger.return_value.get_recent_jobs.return_value = [type('Job', (), {'created_at': 'now'})()]
        assert smoke.main() == expected


def test_smoke_setup_error_is_not_a_finding():
    with patch.object(sys, 'argv', ['smoke', '--findings-only']), \
            patch.object(smoke, 'get_ledger', side_effect=OSError('unreadable')):
        assert smoke.main() == 2


@pytest.mark.parametrize('execution_error,expected', [(False, 0), (True, 2)])
def test_watchdog_findings_only_keeps_real_errors_nonzero(execution_error, expected):
    result = watchdog.WatchdogResult('now', 'nucbox', checks=[
        watchdog.HealthCheck('test', False, 'finding', execution_error=execution_error)])
    with patch.object(sys, 'argv', ['watchdog', '--findings-only']), \
            patch.object(watchdog, 'run_watchdog', return_value=result) as run, \
            patch.object(watchdog, 'sync_peer_health'), \
            patch('cluster.control_plane.main_ci.reconcile_recent') as reconcile:
        assert watchdog.main() == expected
    run.assert_called_once_with(machine=None, check_only=True)
    reconcile.assert_not_called()


@pytest.mark.parametrize('function', [watchdog.check_labels_exist, watchdog.check_install_stale])
def test_caught_cli_failure_remains_an_execution_error(function, tmp_path, monkeypatch):
    monkeypatch.setenv('STORYPOOL_INSTALL_DIR', str(tmp_path))
    with patch.object(watchdog.subprocess, 'run', side_effect=OSError('unsafe details')):
        result = function('owner/repo') if function == watchdog.check_labels_exist else function()
    assert not result.passed and result.execution_error
    assert 'unsafe details' not in result.message
