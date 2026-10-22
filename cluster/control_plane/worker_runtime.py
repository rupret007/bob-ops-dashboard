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
    _require(isinstance(head_sha, str) and _HEAD.fullmatch(head_sha)
             and platform.system() == 'Linux' and platform.machine() == 'x86_64')
    home = Path.home()
    _directory(home)
    root = home / PROFILE
    # Every parent below HOME must be owned and resistant to other-user writes.
    path = home
    for component in Path(PROFILE).parts:
        path = path / component
        _directory(path, private=path == root)
    for relative in ('venv', 'venv/bin', 'native', 'receipts',
                     'config', 'cache', 'data', 'state', 'xdg-runtime'):
        _directory(root / relative)
    empty_netrc, _ = _digest(root / 'config/empty-netrc', 1, private=True, allow_empty=True)
    _require(empty_netrc == b'')
    raw, receipt_hash = _digest(root / 'qualification.json', 128 * 1024, private=True)
    value = _json(raw)
    _require(type(value.get('schema')) is int and value['schema'] == 1
             and value.get('repo') == WEBJAM and value.get('machine') == 'nucbox'
             and value.get('platform') == 'linux-x86_64')
    _require(value.get('test_recipe') == TEST_RECIPE
             and value.get('headless_audio_config_sha256') == ALSA_SHA256)
    audio, audio_hash = _digest(root / 'config/alsa.conf', 4096, private=True)
    _require(audio == ALSA_CONFIG and audio_hash == ALSA_SHA256)
    python = root / 'venv/bin/python'
    native = root / 'native'
    _require(value.get('python') == str(python) and value.get('native_prefix') == str(native)
             and isinstance(value.get('python_version'), str)
             and re.fullmatch(r'3\.11\.\d+', value['python_version']))
    resolved = python.resolve(strict=True)
    _require(str(resolved) == value.get('python_realpath'))
    _, binary_hash = _digest(resolved, 64 * 1024 * 1024)
    _require(binary_hash == value.get('python_sha256') and os.access(python, os.X_OK))
    cfg, _ = _digest(root / 'venv/pyvenv.cfg', 16384)
    _require(re.search(rb'(?mi)^include-system-site-packages\s*=\s*false\s*$', cfg))
    manifests = {}
    for name, field in (('wheels', 'wheel_manifest_sha256'), ('native', 'native_manifest_sha256')):
        data, digest = _digest(root / ('receipts/' + name + '.json'), 4 * 1024 * 1024)
        _require(isinstance(value.get(field), str) and _SHA.fullmatch(value[field])
                 and digest == value[field])
        manifest = manifests[name] = _json(data)
        _require(type(manifest.get('schema')) is int and manifest['schema'] == 1)
        artifacts = manifest.get('artifacts')
        _require(type(artifacts) is list and 0 < len(artifacts) <= 1024)
        names = set()
        for artifact in artifacts:
            _require(type(artifact) is dict and isinstance(artifact.get('filename'), str)
                     and re.fullmatch(r'[A-Za-z0-9_.+~\-]+', artifact['filename'])
                     and artifact['filename'] not in names
                     and isinstance(artifact.get('sha256'), str) and _SHA.fullmatch(artifact['sha256'])
                     and type(artifact.get('bytes')) is int and artifact['bytes'] > 0)
            names.add(artifact['filename'])
            if name == 'native':
                _require(all(isinstance(artifact.get(k), str) and artifact[k]
                             for k in ('package', 'version')))
        if name == 'native':
            _require(manifest.get('signature_verified') is True
                     and isinstance(manifest.get('index_sha256'), str)
                     and _SHA.fullmatch(manifest['index_sha256']))
            audio_proof = manifest.get('headless_audio')
            _require(type(audio_proof) is dict and audio_proof.get('backend') == 'alsa-null'
                     and audio_proof.get('configuration') == 'config/alsa.conf'
                     and audio_proof.get('configuration_sha256') == ALSA_SHA256
                     and all(audio_proof.get(k) is False
                             for k in ('includes', 'hardware_audio', 'audio_server')))
            fonts = manifest.get('font_closure')
            _require(type(fonts) is dict and fonts.get('directory') == FONT_DIRECTORY
                     and any(a.get('package') == 'fonts-liberation'
                             and a['sha256'] == fonts.get('package_sha256') for a in artifacts))
            files = fonts.get('files')
            _require(type(files) is list and len(files) == 12)
            directory = native
            for component in Path(FONT_DIRECTORY).parts:
                directory = directory / component
                _directory(directory)
            filenames = []
            for font in files:
                _require(type(font) is dict and isinstance(font.get('filename'), str)
                         and re.fullmatch(r'[A-Za-z0-9_.-]+\.ttf', font['filename'])
                         and type(font.get('bytes')) is int and font['bytes'] > 0)
                data, digest = _digest(directory / font['filename'], 8 * 1024 * 1024)
                _require(digest == font.get('sha256') and len(data) == font['bytes'])
                filenames.append(font['filename'])
            _require(filenames == sorted(set(filenames))
                     and set(filenames) == {p.name for p in directory.iterdir()}
                     and hashlib.sha256(json.dumps(files, sort_keys=True,
                         separators=(',', ':')).encode()).hexdigest() == fonts.get('sha256'))
            _font_link(root / 'data/fonts', directory)
    proof = value.get('imports')
    _require(type(proof) is dict and proof.get('modules') == list(IMPORTS)
             and type(proof.get('returncode')) is int and proof['returncode'] == 0)
    heads = value.get('qualified_heads')
    _require(type(heads) is list and 0 < len(heads) <= 32)
    seen = set()
    selected = None
    for head in heads:
        _require(type(head) is dict and isinstance(head.get('head_sha'), str)
                 and _HEAD.fullmatch(head['head_sha']) and head['head_sha'] not in seen
                 and isinstance(head.get('tree_sha'), str) and _HEAD.fullmatch(head['tree_sha'])
                 and all(isinstance(head.get(k), str) and _SHA.fullmatch(head[k])
                         for k in ('requirements_sha256', 'ci_sha256', 'test_files_sha256'))
                 and type(head.get('test_file_count')) is int and 0 < head['test_file_count'] <= 1024)
        seen.add(head['head_sha'])
        collection = head.get('collection')
        _require(type(collection) is dict and type(collection.get('returncode')) is int
                 and collection['returncode'] == 0 and type(collection.get('count')) is int
                 and collection['count'] > 0)
        if head['head_sha'] == head_sha:
            selected = head
    _require(selected is not None)
    _require(manifests['wheels'].get('requirements_sha256') == selected['requirements_sha256'])
    if tree is not None:
        for relative, field in (('requirements.txt', 'requirements_sha256'),
                                ('.github/workflows/ci.yml', 'ci_sha256')):
            # Checkout contents are data; symlinked dependency recipes are refused.
            path = Path(tree) / relative
            _require(all(not p.is_symlink() for p in (path, *path.parents)
                         if p == Path(tree) or Path(tree) in p.parents))
            _, digest = _digest(path, 1024 * 1024)
            _require(digest == selected[field])
    runtime = QualifiedRuntime(str(python), str(native), receipt_hash, selected)
    result = subprocess.run([str(python), '-I', '-c', _PROBE],
        env=runtime.environment(os.environ), capture_output=True, text=True, timeout=15)
    _require(result.returncode == 0 and len(result.stdout) <= 1024)
    observed = _json(result.stdout)
    _require(observed == {'python': value['python_version'], 'pyside6': '6.11.1'})
    return runtime
