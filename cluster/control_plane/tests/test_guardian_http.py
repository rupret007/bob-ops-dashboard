"""Exercise dashboard outages even when the service manager says loaded."""
import json
import sys
from unittest.mock import Mock

import pytest

from cluster.control_plane import guardian
from cluster.control_plane.tests.test_guardian import agent, response, lifecycle

PORT_AVAILABLE = guardian.dashboard_port_available


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(tmp_path / 'state'))
    monkeypatch.setattr(guardian, 'event', Mock())
    monkeypatch.setattr(guardian.time, 'sleep', lambda _: None)
    monkeypatch.setattr(guardian, 'dashboard_port_available', lambda: True)


def test_probe_uses_loopback_root_without_proxy_or_redirect(monkeypatch):
    reply = Mock()
    reply.status = 200
    connection = Mock()
    connection.getresponse.return_value = reply
    factory = Mock(return_value=connection)
    monkeypatch.setattr(guardian.http.client, 'HTTPConnection', factory)
    assert guardian.dashboard_responding()
    factory.assert_called_once_with('127.0.0.1', 8790, timeout=2)
    connection.request.assert_called_once_with('GET', '/')
    connection.close.assert_called_once()


@pytest.mark.parametrize('status', [301, 400, 502, 503])
def test_http_error_is_not_healthy(monkeypatch, status):
    connection = Mock()
    connection.getresponse.return_value.status = status
    monkeypatch.setattr(guardian.http.client, 'HTTPConnection', lambda *a, **k: connection)
    assert not guardian.dashboard_responding()


def test_probe_timeout_is_failure_and_closes_socket(monkeypatch):
    connection = Mock()
    connection.request.side_effect = TimeoutError()
    monkeypatch.setattr(guardian.http.client, 'HTTPConnection', lambda *a, **k: connection)
    assert not guardian.dashboard_responding()
    connection.close.assert_called_once()


def test_healthy_probe_does_not_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(guardian, 'dashboard_responding', lambda: True)
    run = Mock()
    monkeypatch.setattr(guardian, 'command', run)
    assert guardian.guard_dashboard('mini', tmp_path) == {'ok': True, 'restarted': False}
    run.assert_not_called()


@pytest.mark.parametrize('machine', ['mini', 'nucbox'])
def test_loaded_but_dead_dashboard_restarts_and_verifies(tmp_path, monkeypatch, machine):
    path = agent(tmp_path, 'dashboard')
    replies = iter([False, True])
    monkeypatch.setattr(guardian, 'dashboard_responding', lambda: next(replies))
    reload = Mock(return_value=True)
    run = Mock(return_value=response())
    monkeypatch.setattr(guardian, 'reload_agent', reload)
    monkeypatch.setattr(guardian, 'command', run)
    assert guardian.guard_dashboard(machine, tmp_path) == {'ok': True, 'restarted': True}
    if machine == 'mini':
        reload.assert_called_once_with(path)
    else:
        run.assert_called_once_with(['systemctl', '--user', 'restart', 'storypool-dashboard.service'])
    assert not (tmp_path / 'state/NEEDS_JEFF.md').exists()
    assert any(c.args[1] == 'dashboard_restart_verified' for c in guardian.event.call_args_list)


def test_failed_restart_alerts_without_flooding(tmp_path, monkeypatch):
    monkeypatch.setattr(guardian, 'dashboard_responding', lambda: False)
    monkeypatch.setattr(guardian, 'reload_agent', lambda _: False)
    for _ in range(2):
        assert guardian.guard_dashboard('mini', tmp_path) == {'ok': False, 'restarted': True}
    text = (tmp_path / 'state/NEEDS_JEFF.md').read_text()
    assert text.count('dashboard restart failed') == 1
    assert '127.0.0.1:8790/' in text


def test_registered_dashboard_reload_requires_http(tmp_path, monkeypatch):
    path = agent(tmp_path, 'dashboard')
    calls = []
    monkeypatch.setattr(guardian, 'command', lifecycle(calls))
    monkeypatch.setattr(guardian, 'dashboard_responding', lambda: False)
    assert not guardian.reload_agent(path)
    assert any(c[1] == 'bootstrap' for c in calls)
    assert not any(c[1] == 'kickstart' for c in calls)


def test_every_guardian_cycle_probes_and_persists_http_status(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['guardian', '--machine', 'nucbox'])
    monkeypatch.setattr(guardian, 'recover', lambda _: [])
    probe = Mock(return_value={'ok': False, 'restarted': True})
    monkeypatch.setattr(guardian, 'guard_dashboard', probe)
    assert guardian.main() == 1
    probe.assert_called_once_with('nucbox')
    status = json.loads((tmp_path / 'state/guardian-status.json').read_text())
    assert status['dashboard'] == {'ok': False, 'restarted': True}
    assert status['reloads'] == 0


def test_dashboard_displays_http_proof():
    from pathlib import Path
    html = (Path(guardian.__file__).parent / 'dashboard/engine.html').read_text()
    assert 'Dashboard HTTP verified' in html
    assert 'Dashboard HTTP failed' in html
    assert 'Dashboard HTTP not checked' in html


@pytest.mark.parametrize('machine', ['mini', 'nucbox'])
def test_failed_http_proof_overrides_green_watchdog(tmp_path, machine):
    from datetime import datetime, timezone
    from cluster.control_plane.dashboard.tachometer import EngineCollector
    now = datetime.now(timezone.utc).isoformat()
    (tmp_path / 'watchdog.log').write_text(json.dumps({'timestamp': now, 'checks': [{'passed': True}]}))
    peer = tmp_path / 'machines/mini'
    peer.mkdir(parents=True)
    (peer / 'watchdog.log').write_text(json.dumps({'timestamp': now, 'checks': [{'passed': True}]}))
    proof = {'timestamp': now, 'dashboard': {'ok': False, 'restarted': True}}
    (tmp_path / 'guardian-status.json' if machine == 'nucbox' else peer / 'engine.json').write_text(
        json.dumps(proof if machine == 'nucbox' else {'synced_at': now, 'guardian': proof}))
    result = EngineCollector(tmp_path, tmp_path / 'karen', 'nucbox').collect()
    assert result['machines'][machine]['state'] == 'red'
    assert result['engine'] == 'red'
    assert 'HTTP restart failed' in result['machines'][machine]['detail']


def test_failed_reinstall_is_failed_and_same_head_retries(tmp_path, monkeypatch):
    from cluster.control_plane import self_update as updater
    monkeypatch.setattr(updater, '_log_update_outcome', lambda *a: None)
    installer = Mock(side_effect=[False, True])
    monkeypatch.setattr(updater, 'reinstall', installer)
    result = updater.finish_install(tmp_path, 'mini', {})
    assert result['outcome'] == 'FAILED'
    assert (tmp_path / '.storypool-reinstall-pending').exists()
    monkeypatch.setattr(updater, 'get_current_head', lambda _: 'a' * 40)
    monkeypatch.setattr(updater, 'get_remote_head', lambda _: 'a' * 40)
    monkeypatch.setattr(updater, 'get_current_branch', lambda _: updater.BRANCH)
    monkeypatch.setattr(updater, 'is_working_tree_dirty', lambda _: False)
    monkeypatch.setattr(updater, 'fetch_branch', lambda _: True)
    monkeypatch.setattr(updater, 'run_tests', lambda _: True)
    result = updater.self_update('mini', tmp_path)
    assert result['outcome'] == 'UPDATED'
    assert result['reinstalled']
    assert not (tmp_path / '.storypool-reinstall-pending').exists()
    assert installer.call_count == 2


def test_pending_reinstall_still_requires_passing_gate(tmp_path, monkeypatch):
    from cluster.control_plane import self_update as updater
    (tmp_path / '.storypool-reinstall-pending').touch()
    monkeypatch.setattr(updater, '_log_update_outcome', lambda *a: None)
    for name in ('get_current_head', 'get_remote_head'):
        monkeypatch.setattr(updater, name, lambda _: 'a' * 40)
    monkeypatch.setattr(updater, 'get_current_branch', lambda _: updater.BRANCH)
    monkeypatch.setattr(updater, 'is_working_tree_dirty', lambda _: False)
    monkeypatch.setattr(updater, 'fetch_branch', lambda _: True)
    monkeypatch.setattr(updater, 'run_tests', lambda _: False)
    installer = Mock()
    monkeypatch.setattr(updater, 'reinstall', installer)
    assert updater.self_update('mini', tmp_path)['outcome'] == 'FAILED'
    installer.assert_not_called()
    assert (tmp_path / '.storypool-reinstall-pending').exists()


def test_dashboard_reload_waits_for_old_listener_before_bootstrap(tmp_path, monkeypatch):
    path = agent(tmp_path, 'dashboard')
    calls = []
    run = lifecycle([])
    monkeypatch.setattr(guardian, 'command', lambda argv: calls.append(argv[1]) or run(argv))
    monkeypatch.setattr(guardian, 'wait_dashboard_stopped',
                        lambda: calls.append('port-free') or True, raising=False)
    monkeypatch.setattr(guardian, 'dashboard_responding', lambda: True)
    assert guardian.reload_agent(path)
    assert calls.index('bootout') < calls.index('port-free') < calls.index('bootstrap')
    # Bootstrap starts RunAtLoad; a second forced kickstart recreates the port race.
    assert 'kickstart' not in calls


def test_dashboard_reload_refuses_busy_port(tmp_path, monkeypatch):
    path = agent(tmp_path, 'dashboard')
    calls = []
    run = lifecycle([])
    monkeypatch.setattr(guardian, 'command', lambda argv: calls.append(argv[1]) or run(argv))
    monkeypatch.setattr(guardian, 'wait_dashboard_stopped', lambda: False, raising=False)
    monkeypatch.setattr(guardian, 'dashboard_responding', lambda: True)
    assert not guardian.reload_agent(path)
    assert 'bootstrap' not in calls


def test_dashboard_shutdown_wait_is_bounded_and_checks_port(monkeypatch):
    checks = iter([False, False, True])
    probe = Mock(side_effect=lambda: next(checks))
    monkeypatch.setattr(guardian, 'dashboard_port_available', probe, raising=False)
    assert guardian.wait_dashboard_stopped()
    assert probe.call_count == 3
    probe = Mock(return_value=False)
    monkeypatch.setattr(guardian, 'dashboard_port_available', probe)
    assert not guardian.wait_dashboard_stopped()
    assert probe.call_count <= 61


@pytest.mark.parametrize('busy', [False, True])
def test_dashboard_port_probe_closes_socket_and_rejects_busy_listener(monkeypatch, busy):
    connection = Mock()
    if busy:
        connection.bind.side_effect = OSError('address in use')
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=connection)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(guardian.socket, 'socket', factory)
    assert PORT_AVAILABLE() is not busy
    connection.bind.assert_called_once_with(('127.0.0.1', 8790))
    factory.return_value.__exit__.assert_called_once()


def test_failed_installer_logs_sanitized_stdout_and_stderr(tmp_path, monkeypatch):
    import subprocess
    from cluster.control_plane import self_update as updater
    script = tmp_path / 'cluster/control_plane/install/install-all-mini.sh'
    script.parent.mkdir(parents=True)
    script.touch()
    log = tmp_path / 'self-update.log'
    monkeypatch.setattr(updater, 'UPDATE_LOG_FILE', log)
    monkeypatch.setattr(updater.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(
        a[0], 1, 'Dashboard reload_failed\napi_key=fixture-private-value\n',
        'OSError: [Errno 48] Address already in use\nAuthorization: Bearer fixture-bearer-value\n'))
    assert not updater.reinstall(tmp_path, 'mini')
    text = log.read_text()
    assert 'Dashboard reload_failed' in text
    assert 'Address already in use' in text
    assert 'fixture-private-value' not in text
    assert 'fixture-bearer-value' not in text


def test_installer_timeout_preserves_sanitized_partial_output(tmp_path, monkeypatch):
    import subprocess
    from cluster.control_plane import self_update as updater
    script = tmp_path / 'cluster/control_plane/install/install-all-mini.sh'
    script.parent.mkdir(parents=True)
    script.touch()
    log = tmp_path / 'self-update.log'
    monkeypatch.setattr(updater, 'UPDATE_LOG_FILE', log)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 300,
                                        output=b'Karen attempted\nsecret=fixture-private-value',
                                        stderr=b'Address already in use')
    monkeypatch.setattr(updater.subprocess, 'run', timeout)
    assert not updater.reinstall(tmp_path, 'mini')
    text = log.read_text()
    assert 'TimeoutExpired' in text
    assert 'Karen attempted' in text
    assert 'Address already in use' in text
    assert 'fixture-private-value' not in text


def test_mini_installer_continues_after_reload_failures_and_exits_failed(tmp_path):
    import os
    import subprocess
    from pathlib import Path
    install = Path(guardian.__file__).parent / 'install/install-all-mini.sh'
    shell = '''
source "$1"
uname() { echo Darwin; }
check_dependencies() { :; }
setup_config_dir() { :; }
for name in install_guardian install_shadow install_dispatcher install_conductor install_watchdog install_ops_doctor install_selfupdate; do
    eval "$name() { :; }"
done
python3() { return 1; }
install_dashboard() { storypool_reload_agent /fixture/dashboard.plist; }
install_karen_cli() { echo KAREN_ATTEMPTED; storypool_reload_agent /fixture/karen.plist; }
run_self_check() { :; }
main
'''
    result = subprocess.run(['bash', '-c', shell, 'test', str(install)],
                            capture_output=True, text=True, timeout=10,
                            env={**os.environ, 'HOME': str(tmp_path)})
    assert result.returncode != 0
    assert 'KAREN_ATTEMPTED' in result.stdout
    assert '/fixture/dashboard.plist' in result.stdout + result.stderr
    assert '/fixture/karen.plist' in result.stdout + result.stderr


def test_standalone_reload_failure_still_exits_nonzero(tmp_path):
    import subprocess
    from pathlib import Path
    helper = Path(guardian.__file__).parent / 'install/launchd-reload.sh'
    result = subprocess.run(['bash', '-c', '''
set -euo pipefail
REPO_ROOT="$2"
source "$1"
python3() { return 1; }
storypool_reload_agent /fixture/dashboard.plist
echo SHOULD_NOT_RUN
''', 'test', str(helper), str(tmp_path)], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'SHOULD_NOT_RUN' not in result.stdout
