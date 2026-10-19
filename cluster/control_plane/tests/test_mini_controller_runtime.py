"""Normal Mini generation must retain the qualified controller Python closure."""
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest


INSTALL = Path(__file__).resolve().parents[1] / 'install'


@pytest.mark.parametrize('controller,function,module,tail', [
    ('conductor', 'install_conductor', 'conductor', ['--live']),
    ('dispatch', 'install_dispatcher', 'dispatcher', ['--machine', 'mini']),
])
def test_normal_mini_generation_ignores_unqualified_python_on_tool_path(
        tmp_path, controller, function, module, tail):
    home = tmp_path / 'fixture home'
    home.mkdir()
    brew = tmp_path / 'unqualified-brew'
    brew.mkdir()
    python = brew / 'python3'
    python.write_text('#!/bin/sh\necho UNQUALIFIED_PYTHON_SELECTED >&2\nexit 99\n')
    python.chmod(0o755)
    tool_path = str(brew) + ':/usr/bin:/bin'
    repo = tmp_path / 'checkout with spaces'
    repo.mkdir()
    # Source the real installer, render just this unit, and intercept every
    # runtime action. Neither installer main nor a controller is executed.
    script = '''
source "$1"
REPO_ROOT="$2"
LAUNCHD_PATH="$3"
clear_pycache() { :; }
storypool_reload_agent() { printf 'RELOAD %s\\n' "$1"; }
launchctl() { printf 'LAUNCHCTL %s\\n' "$1"; }
"$4"
'''
    result = subprocess.run(
        ['bash', '-c', script, 'test', str(INSTALL / 'install-all-mini.sh'),
         str(repo), tool_path, function],
        env={**os.environ, 'HOME': str(home), 'PATH': tool_path},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert 'UNQUALIFIED_PYTHON_SELECTED' not in result.stdout + result.stderr
    path = home / ('Library/LaunchAgents/com.storypool.' + controller + '.plist')
    rendered = plistlib.loads(path.read_bytes())
    argv = rendered['ProgramArguments']
    selected = (shutil.which(argv[1], path=tool_path)
                if argv[0] == '/usr/bin/env' else argv[0])
    assert selected == '/usr/bin/python3', 'Tool PATH selected an unqualified Python closure'
    assert argv == ['/usr/bin/python3', '-m', 'cluster.control_plane.' + module, *tail]
    assert rendered['WorkingDirectory'] == str(repo)
    environment = rendered['EnvironmentVariables']
    assert environment['HOME'] == str(home)
    assert environment['PATH'] == tool_path
    assert environment['PYTHONPATH'] == str(repo)
    assert environment['STORYPOOL_MACHINE'] == 'mini'
    assert environment['STORYPOOL_INSTALL_DIR'] == str(repo)
    assert rendered['RunAtLoad'] is False
    assert rendered['Nice'] == 10
    assert rendered['StandardOutPath'].startswith(str(home) + '/')
    assert rendered['StandardErrorPath'].startswith(str(home) + '/')
    if controller == 'conductor':
        assert rendered['StartCalendarInterval'] == {'Minute': 5}
        assert argv.count('--live') == 1
        assert 'LAUNCHCTL kickstart' in result.stdout
    else:
        assert rendered['StartInterval'] == 900
        assert rendered['AbandonProcessGroup'] is True
        assert '--live' not in argv
    # Existing enabled native Python controllers use the same stable runtime.
    for name in ('health', 'karen-poller', 'dashboard', 'selfupdate'):
        peer = plistlib.loads((INSTALL / ('com.storypool.' + name + '.plist')).read_bytes())
        assert peer['ProgramArguments'][0] == selected
