"""Normal Mini generation must retain the qualified controller Python closure."""
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest


INSTALL = Path(__file__).resolve().parents[1] / 'install'


def render_command(function):
    """Extract only the unambiguous real render, without installer side effects."""
    source = (INSTALL / 'install-all-mini.sh').read_text()
    marker = function + '() {'
    assert source.count(marker) == 1
    body = source.split(marker, 1)[1].split('\n}', 1)[0]
    start, end = '    sed -e ', '        "$plist_src" > "$plist_dst"'
    assert body.count(start) == body.count(end) == 1
    command = start + body.split(start, 1)[1].split(end, 1)[0] + end
    lines = command.splitlines()
    assert lines[0].endswith('\\')
    assert all(line.strip().startswith('-e ') and line.endswith('\\')
               for line in lines[1:-1])
    return command


@pytest.mark.parametrize('controller,function,module,tail', [
    ('conductor', 'install_conductor', 'conductor', ['--live']),
    ('dispatch', 'install_dispatcher', 'dispatcher', ['--machine', 'mini']),
])
def test_normal_mini_generation_ignores_unqualified_python_on_tool_path(
        tmp_path, controller, function, module, tail):
    home = Path(os.environ['HOME'])
    brew = tmp_path / 'unqualified-brew'
    brew.mkdir()
    python = brew / 'python3'
    python.write_text('#!/bin/sh\necho UNQUALIFIED_PYTHON_SELECTED >&2\nexit 99\n')
    python.chmod(0o755)
    tool_path = str(brew) + ':/usr/bin:/bin'
    repo = tmp_path / 'checkout with spaces'
    repo.mkdir()
    path = tmp_path / ('com.storypool.' + controller + '.plist')
    # Execute only the real sed render with a private destination. HOME stays
    # unchanged; no installer function, runtime action or controller is run.
    script = '''
set -euo pipefail
plist_src="$1"
plist_dst="$2"
REPO_ROOT="$3"
LAUNCHD_PATH="$4"
''' + render_command(function)
    result = subprocess.run(
        ['bash', '-c', script, 'test',
         str(INSTALL / ('com.storypool.' + controller + '.plist')),
         str(path), str(repo), tool_path],
        env={**os.environ, 'PATH': tool_path},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert 'UNQUALIFIED_PYTHON_SELECTED' not in result.stdout + result.stderr
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
    else:
        assert rendered['StartInterval'] == 900
        assert rendered['AbandonProcessGroup'] is True
        assert '--live' not in argv
    # Existing enabled native Python controllers use the same stable runtime.
    for name in ('health', 'karen-poller', 'dashboard', 'selfupdate'):
        peer = plistlib.loads((INSTALL / ('com.storypool.' + name + '.plist')).read_bytes())
        assert peer['ProgramArguments'][0] == selected
