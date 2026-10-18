import json
import plistlib
import subprocess
from pathlib import Path

import pytest

from cluster.control_plane import guardian

INSTALL = Path(__file__).resolve().parents[1] / 'install'


def agent(root, name='watchdog'):
    path = root / 'Library/LaunchAgents' / ('com.storypool.' + name + '.plist')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps({'Label': path.stem, 'ProgramArguments': ['/bin/true']}))
    return path


@pytest.fixture(autouse=True)
def no_live_logging(monkeypatch):
    monkeypatch.setattr(guardian, 'event', lambda *a, **kw: None)


def response(code=0, output=''):
    return subprocess.CompletedProcess([], code, output, '')


def test_reload_validates_then_boots_out_bootstraps_and_verifies(tmp_path, monkeypatch):
    path = agent(tmp_path)
    calls = []
    monkeypatch.setattr(guardian, 'command', lambda argv: calls.append(argv) or response())
    assert guardian.reload_agent(path)
    assert [c[1] for c in calls] == ['enable', 'bootout', 'bootstrap', 'print']
    assert calls[2][-1] == str(path)
    assert calls[1][-1].endswith('/com.storypool.watchdog')


def test_invalid_plist_never_unloads_existing_agent(tmp_path, monkeypatch):
    path = agent(tmp_path)
    path.write_text('broken')
    calls = []
    monkeypatch.setattr(guardian, 'command', lambda argv: calls.append(argv))
    with pytest.raises(plistlib.InvalidFileException):
        guardian.reload_agent(path)
    assert calls == []


@pytest.mark.parametrize('failed', ['enable', 'bootstrap', 'print'])
def test_failed_reload_cannot_report_success(tmp_path, monkeypatch, failed):
    path = agent(tmp_path)
    monkeypatch.setattr(guardian, 'command', lambda argv: response(int(argv[1] == failed)))
    assert not guardian.reload_agent(path)


def test_failed_bootout_with_loaded_agent_does_not_bootstrap(tmp_path, monkeypatch):
    path = agent(tmp_path)
    calls = []
    def run(argv):
        calls.append(argv[1])
        return response(int(argv[1] == 'bootout'))
    monkeypatch.setattr(guardian, 'command', run)
    assert not guardian.reload_agent(path)
    assert 'bootstrap' not in calls


def test_running_updater_defers_its_own_reload(tmp_path, monkeypatch):
    path = agent(tmp_path, 'selfupdate')
    calls = []
    monkeypatch.setattr(guardian, 'command', lambda argv: calls.append(argv) or response(output='pid = 42'))
    assert guardian.reload_agent(path, defer_running=True)
    assert path.with_suffix('.reload-pending').exists()
    assert [c[1] for c in calls] == ['print']


def test_guardian_applies_pending_update_only_after_updater_exits(tmp_path, monkeypatch):
    path = agent(tmp_path, 'selfupdate')
    path.with_suffix('.reload-pending').touch()
    monkeypatch.setattr(guardian, 'command', lambda argv: response(output='pid = 42'))
    assert guardian.recover('mini', tmp_path) == []
    calls = []
    monkeypatch.setattr(guardian, 'command', lambda argv: calls.append(argv) or response())
    assert guardian.recover('mini', tmp_path) == [{'unit': path.stem, 'ok': True}]
    assert not path.with_suffix('.reload-pending').exists()
    assert any(c[1] == 'bootstrap' for c in calls)


def test_mini_recovers_unloaded_watchdog_without_enabling_shadow_karen(tmp_path, monkeypatch):
    watchdog = agent(tmp_path)
    agent(tmp_path, 'karen-poller')
    calls = []
    monkeypatch.setattr(guardian, 'command', lambda argv: response(1))
    monkeypatch.setattr(guardian, 'reload_agent', lambda path: calls.append(path) or True)
    assert guardian.recover('mini', tmp_path) == [{'unit': watchdog.stem, 'ok': True}]
    assert calls == [watchdog]


def test_live_mini_recovers_karen_too(tmp_path, monkeypatch):
    agent(tmp_path, 'karen-poller')
    marker = tmp_path / '.config/storypool/live'
    marker.parent.mkdir(parents=True)
    marker.touch()
    monkeypatch.setattr(guardian, 'command', lambda argv: response(1))
    monkeypatch.setattr(guardian, 'reload_agent', lambda path: True)
    assert guardian.recover('mini', tmp_path) == [{'unit': 'com.storypool.karen-poller', 'ok': True}]


def test_linux_restores_only_installed_storypool_timers(tmp_path, monkeypatch):
    units = tmp_path / '.config/systemd/user'
    units.mkdir(parents=True)
    for name in ('storypool-watchdog.timer', 'webbob-driver.timer', 'storypool-dispatch.service'):
        (units / name).touch()
    calls = []
    def run(argv):
        calls.append(argv)
        return response(0 if 'enable' in argv else 1)
    monkeypatch.setattr(guardian, 'command', run)
    assert guardian.recover('nucbox', tmp_path) == [{'unit': 'storypool-watchdog.timer', 'ok': True}]
    assert calls[-1] == ['systemctl', '--user', 'enable', '--now', 'storypool-watchdog.timer']
    assert all('webbob' not in ' '.join(c) for c in calls)


def test_command_failure_is_bounded_and_contains_no_cli_output(monkeypatch):
    def fail(*args, **kwargs):
        assert kwargs['timeout'] == 15
        raise subprocess.TimeoutExpired(args[0], 15, output='sensitive')
    monkeypatch.setattr(guardian.subprocess, 'run', fail)
    result = guardian.command(['launchctl', 'print', 'target'])
    assert result.returncode == 1
    assert result.stdout == result.stderr == ''


def test_installers_and_selfupdate_wire_guardian_and_checked_reload():
    mini = (INSTALL / 'install-all-mini.sh').read_text()
    assert '    install_guardian\n' in mini
    assert 'storypool_reload_agent "$dst" --defer-running' in mini
    assert 's|__HOME__|$HOME|g' in mini
    assert 's|__REPO_PATH__|$REPO_ROOT|g' in mini
    assert '    install_guardian\n' in (INSTALL / 'install-all-nucbox.sh').read_text()
    for name in ('install-all-mini.sh', 'install-conductor-mini.sh', 'install-dispatch-mini.sh',
                 'install-shadow-mini.sh', 'enable-karen-poller-mini.sh', 'go-live-mini.sh'):
        text = (INSTALL / name).read_text()
        assert 'source "$SCRIPT_DIR/launchd-reload.sh"' in text
        assert 'storypool_reload_agent' in text
        assert not any(line.lstrip().startswith('launchctl load ') for line in text.splitlines())
    assert 'install-all-mini.sh' in (INSTALL.parent / 'self_update.py').read_text()


def test_installed_guardian_wrapper_and_plist_resolve_paths(tmp_path):
    root = str(tmp_path / 'home space')
    plist = plistlib.loads((INSTALL / 'com.storypool.guardian.plist').read_bytes().replace(b'__HOME__', root.encode()))
    assert plist['ProgramArguments'] == ['/bin/bash', root + '/.local/bin/storypool-guardian']
    assert plist['StartInterval'] == 120
    script = (INSTALL / 'storypool-guardian.sh').read_text().replace('__REPO_PATH__', str(tmp_path))
    assert 'cluster.control_plane.guardian --machine mini' in script
    assert '__REPO_PATH__' not in script


def test_shell_reload_helper_runs_installed_module_with_quoted_paths(tmp_path):
    import os
    import sys
    path = agent(tmp_path / 'home with spaces')
    binary = tmp_path / 'bin'
    binary.mkdir()
    (binary / 'python3').symlink_to(sys.executable)
    calls = tmp_path / 'calls'
    launchctl = binary / 'launchctl'
    launchctl.write_text('#!/bin/bash\nprintf "%s\\n" "$1" >> "$STORYPOOL_TEST_CALLS"\nexit 0\n')
    launchctl.chmod(0o755)
    env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
           'REPO_ROOT': str(INSTALL.parents[2]), 'STORYPOOL_TEST_CALLS': str(calls),
           'STORYPOOL_DATA_DIR': str(tmp_path / 'state'), 'STORYPOOL_LOG_DIR': str(tmp_path / 'logs')}
    result = subprocess.run(['bash', '-c', 'source "$1"; storypool_reload_agent "$2"',
                             'test', str(INSTALL / 'launchd-reload.sh'), str(path)],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert calls.read_text().splitlines() == ['enable', 'bootout', 'bootstrap', 'print']
