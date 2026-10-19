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

    def environment(self, inherited, state_root=None):
        env = dict(inherited)
        for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONUSERBASE',
                    'LD_PRELOAD', 'LD_AUDIT', 'PYTEST_ADDOPTS', 'PYTEST_PLUGINS',
                    'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH',
                    'QML2_IMPORT_PATH', 'QML_IMPORT_PATH'):
            env.pop(key, None)
        native = Path(self.native_prefix)
        state = Path(state_root) if state_root is not None else native.parent
        env.update(QT_QPA_PLATFORM='offscreen', VIRTUAL_ENV=str(Path(self.python).parent.parent),
                   PATH=str(Path(self.python).parent) + os.pathsep + env.get('PATH', '/usr/bin:/bin'),
                   PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
                   PIP_CONFIG_FILE=os.devnull, NETRC=str(state / 'config/empty-netrc'),
                   XDG_CONFIG_HOME=str(state / 'config'), XDG_CACHE_HOME=str(state / 'cache'),
                   XDG_DATA_HOME=str(state / 'data'), XDG_STATE_HOME=str(state / 'state'),
                   XDG_RUNTIME_DIR=str(state / 'xdg-runtime'),
                   PULSE_SERVER='unix:' + str(state / 'xdg-runtime/no-pulse-server'),
                   LD_LIBRARY_PATH=os.pathsep.join(str(native / relative) for relative in
                       ('usr/lib/x86_64-linux-gnu', 'lib/x86_64-linux-gnu',
                        'usr/lib/x86_64-linux-gnu/pulseaudio')))
        return env


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
                     and re.fullmatch(r'[A-Za-z0-9_.+-]+', artifact['filename'])
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
                         for k in ('requirements_sha256', 'ci_sha256')))
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
