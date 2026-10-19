"""Evidence of the Karen source loaded by a poller, separate from disk HEAD."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def source_hashes(directory):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(directory).glob('*.py')) if p.is_file()}


# Capture before a poll cycle can update files under this process.
LOADED_AT = datetime.now(timezone.utc).isoformat()
LOADED_HASHES = source_hashes(Path(__file__).parent)


def deployment_status(install_dir=None, loaded_hashes=None):
    root = Path(install_dir or os.environ.get('STORYPOOL_INSTALL_DIR', '~/storypool-install')).expanduser()
    loaded_hashes = LOADED_HASHES if loaded_hashes is None else loaded_hashes
    result = {'installed_head': None, 'loaded_head': None, 'source_matches': False,
              'loaded_at': LOADED_AT, 'pid': os.getpid()}
    try:
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root,
                              capture_output=True, timeout=5, check=True).stdout.decode().strip()
        if not re.fullmatch('[0-9a-f]{40}', head):
            return result
        result['installed_head'] = head
        prefix = 'cluster/control_plane/local_karen/'
        names = subprocess.run(['git', 'ls-tree', '--name-only', head, prefix], cwd=root,
                               capture_output=True, timeout=5, check=True).stdout.decode().splitlines()
        expected = {}
        for name in names:
            relative = name[len(prefix):]
            if '/' in relative or not relative.endswith('.py'):
                continue
            content = subprocess.run(['git', 'show', head + ':' + name], cwd=root,
                                     capture_output=True, timeout=5, check=True).stdout
            expected[relative] = hashlib.sha256(content).hexdigest()
        if expected and expected == loaded_hashes:
            result.update(loaded_head=head, source_matches=True)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return result


def record_runtime(config):
    value = deployment_status()
    value['observed_at'] = datetime.now(timezone.utc).isoformat()
    directory = config.get_state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'poller-runtime.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value))
    temp.replace(path)
    return value


def public_status(config):
    """Allowlist receipt fields; never expose arbitrary state or filesystem paths."""
    try:
        value = json.loads((config.get_state_dir() / 'poller-runtime.json').read_text())
        if not isinstance(value, dict):
            return None
        out = {k: value.get(k) for k in ('loaded_at', 'observed_at', 'pid', 'source_matches')}
        for field in ('installed_head', 'loaded_head'):
            head = value.get(field)
            out[field] = head if isinstance(head, str) and re.fullmatch('[0-9a-f]{40}', head) else None
        try:
            observed = datetime.fromisoformat(str(out.get('observed_at')))
            age = (datetime.now(timezone.utc) - observed).total_seconds()
            out['fresh'] = 0 <= age <= 900
        except (ValueError, TypeError):
            out['fresh'] = False
        return out
    except (OSError, ValueError):
        return None
