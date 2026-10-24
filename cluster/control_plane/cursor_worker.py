"""Cursor edits a private checkout; only the existing supervisor runs commands."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile


ENVIRONMENT = ('HOME', 'PATH', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TERM',
               'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS', 'CURSOR_API_KEY',
               'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY')
VERSION = '2026.10.16-e3cb207'


def environment():
    return {key: value for key, value in os.environ.items() if key in ENVIRONMENT}


def _executable():
    executable = shutil.which('cursor-agent')
    if not executable:
        raise RuntimeError('Cursor worker unavailable')
    current = Path(executable).resolve()
    if (current.name == 'cursor-agent' and current.parent.name != VERSION
            and current.parent.parent.name == 'versions'):
        pinned = current.parent.parent / VERSION / 'cursor-agent'
        if (not pinned.is_symlink() and pinned.is_file()
                and pinned.resolve() == pinned and os.access(pinned, os.X_OK)):
            info = pinned.stat()
            if info.st_uid == os.getuid() and not info.st_mode & 0o022:
                return str(pinned)
    return executable


def available():
    try:
        executable = _executable()
        result = subprocess.run([executable, '--version'], env=environment(),
                                capture_output=True, text=True, timeout=20)
        if result.returncode or result.stdout.strip() != VERSION:
            return False
        result = subprocess.run([executable, 'status', '--format', 'json'],
                                env=environment(), capture_output=True, text=True, timeout=20)
        value = json.loads(result.stdout)
        return result.returncode == 0 and isinstance(value, dict) and value.get('isAuthenticated') is True
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError):
        return False


def command():
    executable = Path(_executable()).resolve()
    if executable.parent.name != VERSION: raise RuntimeError('Cursor runtime requires qualification')
    return [str(executable), '--print', '--trust', '--sandbox', 'enabled', '--output-format', 'json']


@contextmanager
def policy(tree):
    folder = Path(tree) / '.cursor'
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise RuntimeError('Cursor workspace policy path is unsafe')
    existed = folder.exists()
    definitions = {
        'cli.json': {'permissions': {'allow': [f'Read({Path(tree).resolve()}/**)', f'Write({Path(tree).resolve()}/**)'], 'deny': [
            'Shell(*)', 'Mcp(*:*)', 'WebFetch(*)', 'Read(/home/**)', 'Read(/Users/**)',
            'Write(/home/**)', 'Write(/Users/**)', 'Write(.git)', 'Write(.git/**)',
            'Write(.cursor)', 'Write(.cursor/**)', 'Read(**/.env*)', 'Read(**/*.pem)']}},
        'sandbox.json': {'type': 'workspace_readwrite', 'readBoundary': 'workspace',
                         'networkPolicy': {'default': 'deny'}, 'disableTmpWrite': True},
    }
    previous = {}
    for name in definitions:
        path = folder / name
        if path.is_symlink():
            raise RuntimeError('Cursor workspace policy file is unsafe')
        if path.exists():
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                raise RuntimeError('Cursor workspace policy file is unsafe')
            previous[name] = (path.read_bytes(), stat.S_IMODE(info.st_mode))
            if name == 'cli.json':
                old = json.loads(previous[name][0])
                if not isinstance(old, dict) or not isinstance(old.get('permissions', {}), dict):
                    raise RuntimeError('Cursor workspace permission schema is unsafe')
                deny = old.get('permissions', {}).get('deny', [])
                if not isinstance(deny, list) or not all(isinstance(v, str) for v in deny):
                    raise RuntimeError('Cursor workspace permission schema is unsafe')
                definitions[name]['permissions']['deny'] += deny
    with tempfile.TemporaryDirectory(prefix='storypool-cursor-policy-', dir='/tmp') as settings:
        configuration = dict(version=1, editor={'vimMode': False}, approvalMode='allowlist',
                             permissions=definitions['cli.json']['permissions'],
                             sandbox={'mode': 'enabled', 'readBoundary': 'workspace'})
        (Path(settings) / 'cli-config.json').write_text(json.dumps(configuration))
        folder.mkdir(exist_ok=True)
        installed = {}
        try:
            for name, value in definitions.items():
                data = json.dumps(value, sort_keys=True).encode()
                (folder / name).write_bytes(data)
                installed[name] = data
            yield dict(environment(), CURSOR_CONFIG_DIR=settings)
        finally:
            if folder.is_symlink():
                raise RuntimeError('Cursor changed its supervisor-owned policy folder')
            for name, data in installed.items():
                path = folder / name
                if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
                    raise RuntimeError('Cursor changed its supervisor-owned policy')
            for name in installed:
                path = folder / name
                if name in previous:
                    data, mode = previous[name]
                    path.write_bytes(data)
                    path.chmod(mode)
                else:
                    path.unlink()
            if not existed and not any(folder.iterdir()):
                folder.rmdir()



def validate_reply(raw):
    try:
        value = json.loads(raw)
    except ValueError:
        raise RuntimeError('Cursor worker response is invalid') from None
    if (not isinstance(value, dict) or value.get('type') != 'result'
            or value.get('subtype') != 'success' or value.get('is_error') is not False):
        raise RuntimeError('Cursor worker did not complete successfully')
