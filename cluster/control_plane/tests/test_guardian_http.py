"""Exercise dashboard outages even when the service manager says loaded."""
import json
import sys
from unittest.mock import Mock

import pytest

from cluster.control_plane import guardian
from cluster.control_plane.tests.test_guardian import agent, response


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(tmp_path / 'state'))
    monkeypatch.setattr(guardian, 'event', Mock())
    monkeypatch.setattr(guardian.time, 'sleep', lambda _: None)


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
    monkeypatch.setattr(guardian, 'command', lambda argv: calls.append(argv) or response())
    monkeypatch.setattr(guardian, 'dashboard_responding', lambda: False)
    assert not guardian.reload_agent(path)
    assert any(c[1] == 'bootstrap' for c in calls)
    assert any(c[1] == 'kickstart' for c in calls)


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
