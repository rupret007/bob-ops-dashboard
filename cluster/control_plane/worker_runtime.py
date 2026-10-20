"""One operator-qualified Webjam runtime; repository data cannot choose a recipe."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path


WEBJAM = 'rupret007/webjam'
PROFILE = '.local/share/storypool/overseer/webjam-failure-audit/runtime-py311'
REASON = 'Webjam test runtime is not qualified for this exact head'
IMPORTS = ('PySide6.QtCore', 'PySide6.QtWidgets', 'PySide6.QtMultimedia',
           'sounddevice', 'soundfile')
TEST_RECIPE = 'webjam-linux-per-module-v2'
FONT_DIRECTORY = 'usr/share/fonts/truetype/liberation'
ALSA_CONFIG = (b'# Private headless ALSA configuration: null PCM discards audio; no includes.\n'
               b'pcm.!default {\n type null\n hint { show on description "Private null PCM" }\n}\n')
ALSA_SHA256 = hashlib.sha256(ALSA_CONFIG).hexdigest()
# Load the installed entrypoint before exposing project imports. A checkout's
# pytest.py/package must never be able to report success without running tests.
PYTEST_LAUNCHER = ("import os,sys,pytest\n"
                   "sys.path.append(os.getcwd())\n"
                   "raise SystemExit(pytest.main([sys.argv[1],'-v']))\n")
# This code is supervisor-owned. Repository files supply path data, never code.
MODULE_RUNNER = ("import subprocess,sys\n"
                 "for path in sys.argv[1:]:\n"
                 " result=subprocess.run([sys.executable,'-I','-c'," + repr(PYTEST_LAUNCHER) + ",path])\n"
                 " if result.returncode:\n"
                 "  raise SystemExit(result.returncode if result.returncode>0 else 128-result.returncode)\n")
_SHA = re.compile(r'[0-9a-f]{64}')
_HEAD = re.compile(r'[0-9a-f]{40}')
_PROBE = ("import importlib,importlib.metadata,json,sys;"
          "[importlib.import_module(n) for n in " + repr(IMPORTS) + "];"
          "import pytest;print(json.dumps({'python':sys.version.split()[0],"
          "'pyside6':importlib.metadata.version('PySide6')}))")


class RuntimeUnqualified(RuntimeError):
    def __init__(self):
        super().__init__(REASON)


def _require(condition):
    if not condition:
        raise RuntimeUnqualified()


def _digest(path, limit, *, private=False, allow_empty=False):
    """Bound reads and refuse symlink replacement of evidence files."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                 and not info.st_mode & (0o077 if private else 0o022)
                 and (info.st_size > 0 or allow_empty) and info.st_size <= limit)
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            data = stream.read(limit + 1)
        _require(len(data) <= limit)
        return data, hashlib.sha256(data).hexdigest()
    finally:
        os.close(descriptor)


def _directory(path, *, private=False):
    info = path.lstat()
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
             and not info.st_mode & (0o077 if private else 0o022))


def _font_link(path, target):
    info = path.lstat()
    _require(stat.S_ISLNK(info.st_mode) and info.st_uid == os.getuid()
             and os.readlink(path) == str(target))


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result)
            result[key] = value
        return result
    result = json.loads(data, object_pairs_hook=unique)
    _require(type(result) is dict)
    return result


@dataclass(frozen=True)
class QualifiedRuntime:
    python: str
    native_prefix: str
    receipt_sha256: str
    head: dict

    def test_command(self, files):
        _require(files)
        files = test_files('\0'.join(files) + '\0')
        return [self.python, '-I', '-c', MODULE_RUNNER, *files]

    def validate_test_files(self, raw):
        files = test_files(raw)
        _require(len(files) == self.head['test_file_count']
                 and hashlib.sha256(raw.encode()).hexdigest() == self.head['test_files_sha256'])
        return files

    def prepare_state(self, state):
        state.mkdir(mode=0o700)
        for name in ('config', 'cache', 'data', 'state', 'xdg-runtime'):
            (state / name).mkdir(mode=0o700)
        (state / 'config/empty-netrc').touch(mode=0o600)
        path = state / 'config/alsa.conf'
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(ALSA_CONFIG)
        (state / 'data/fonts').symlink_to(Path(self.native_prefix) / FONT_DIRECTORY)

    def validate_state(self, state):
        _directory(state, private=True)
        _directory(state / 'config', private=True)
        audio, digest = _digest(state / 'config/alsa.conf', 4096, private=True)
        _require(audio == ALSA_CONFIG and digest == ALSA_SHA256)
        netrc, _ = _digest(state / 'config/empty-netrc', 1, private=True, allow_empty=True)
        _require(netrc == b'')
        _directory(state / 'data', private=True)
        _font_link(state / 'data/fonts', Path(self.native_prefix) / FONT_DIRECTORY)

    def environment(self, inherited, state_root=None):
        env = dict(inherited)
        for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONUSERBASE',
                    'LD_PRELOAD', 'LD_AUDIT', 'PYTEST_ADDOPTS', 'PYTEST_PLUGINS',
                    'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH',
                    'QML2_IMPORT_PATH', 'QML_IMPORT_PATH',
                    'FONTCONFIG_FILE', 'FONTCONFIG_PATH', 'FONTCONFIG_SYSROOT'):
            env.pop(key, None)
        native = Path(self.native_prefix)
        state = Path(state_root) if state_root is not None else native.parent
        env.update(QT_QPA_PLATFORM='offscreen', VIRTUAL_ENV=str(Path(self.python).parent.parent),
                   PATH=str(Path(self.python).parent) + os.pathsep + env.get('PATH', '/usr/bin:/bin'),
                   PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
                   PIP_CONFIG_FILE=os.devnull, NETRC=str(state / 'config/empty-netrc'),
                   ALSA_CONFIG_PATH=str(state / 'config/alsa.conf'),
                   XDG_CONFIG_HOME=str(state / 'config'), XDG_CACHE_HOME=str(state / 'cache'),
                   XDG_DATA_HOME=str(state / 'data'), XDG_STATE_HOME=str(state / 'state'),
                   XDG_RUNTIME_DIR=str(state / 'xdg-runtime'),
                   PULSE_SERVER='unix:' + str(state / 'xdg-runtime/no-pulse-server'),
                   LD_LIBRARY_PATH=os.pathsep.join(str(native / relative) for relative in
                       ('usr/lib/x86_64-linux-gnu', 'lib/x86_64-linux-gnu',
                        'usr/lib/x86_64-linux-gnu/pulseaudio')))
        return env


def test_files(raw):
    """Accept bounded NUL-delimited Git path data, preserving Git's exact order."""
    _require(isinstance(raw, str) and raw.endswith('\0') and len(raw.encode()) <= 512 * 1024)
    files = raw[:-1].split('\0')
    _require(0 < len(files) <= 1024 and len(files) == len(set(files)))
    for name in files:
        _require(name.startswith('tests/test_') and name.endswith('.py') and len(name.encode()) <= 512
                 and all(part not in ('', '.', '..') for part in name.split('/')))
    return files


def outside_standard_tests(raw):
    """Snapshot names outside the proven CI scope; new ones need qualification."""
    _require(isinstance(raw, str) and raw.endswith('\0') and len(raw.encode()) <= 2 * 1024 * 1024)
    paths = raw[:-1].split('\0')
    _require(len(paths) <= 16384 and len(paths) == len(set(paths)))
    outside = set()
    for name in paths:
        _require(name.endswith('.py') and len(name.encode()) <= 512
                 and all(part not in ('', '.', '..') for part in name.split('/')))
        base = name.rsplit('/', 1)[-1]
        if ((base.startswith('test_') or base.endswith('_test.py'))
                and not name.startswith('tests/test_')):
            outside.add(name)
    return outside


def qualified_runtime(repo, head_sha, tree=None):
    """Return None for unchanged default behavior; fail closed for Webjam only."""
    if repo != WEBJAM:
        return None
    try:
        return _qualified_runtime(head_sha, tree)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError,
            RuntimeUnqualified):
        raise RuntimeUnqualified() from None


def _qualified_runtime(head_sha, tree):
    from . import worker_runtime, worker_runtime_validation
    return worker_runtime_validation._qualified_runtime(worker_runtime, head_sha, tree)
