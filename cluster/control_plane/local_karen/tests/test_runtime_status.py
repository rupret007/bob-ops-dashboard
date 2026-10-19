"""A disk update alone is not evidence that the poller loaded that code."""
import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from cluster.control_plane.local_karen import runtime_status as runtime
from cluster.control_plane.local_karen.config import KarenConfig


def test_loaded_source_must_match_installed_commit():
    content = b'print("version")\n'
    outputs = [b'a' * 40 + b'\n', b'cluster/control_plane/local_karen/poller.py\n', content]
    with patch.object(runtime.subprocess, 'run', side_effect=[subprocess.CompletedProcess([], 0, stdout=v) for v in outputs]):
        result = runtime.deployment_status('/tmp', {'poller.py': hashlib.sha256(content).hexdigest()})
    assert result['installed_head'] == result['loaded_head'] == 'a' * 40
    assert result['source_matches'] is True


def test_old_process_does_not_claim_new_disk_head():
    outputs = [b'b' * 40 + b'\n', b'cluster/control_plane/local_karen/poller.py\n', b'new version']
    with patch.object(runtime.subprocess, 'run', side_effect=[subprocess.CompletedProcess([], 0, stdout=v) for v in outputs]):
        result = runtime.deployment_status('/tmp', {'poller.py': hashlib.sha256(b'old version').hexdigest()})
    assert result['installed_head'] == 'b' * 40
    assert result['loaded_head'] is None
    assert result['source_matches'] is False


def test_missing_git_never_proves_deployment():
    with patch.object(runtime.subprocess, 'run', side_effect=OSError):
        value = runtime.deployment_status('/tmp')
    assert value['loaded_head'] is None and value['source_matches'] is False


def test_receipt_is_atomic_and_public_fields_are_allowlisted(tmp_path):
    config = KarenConfig(state_dir=str(tmp_path))
    with patch.object(runtime, 'deployment_status', return_value={'installed_head': 'c' * 40, 'loaded_head': 'c' * 40, 'source_matches': True, 'loaded_at': runtime.LOADED_AT, 'pid': 42}):
        value = runtime.record_runtime(config)
    assert runtime.public_status(config) == value
    path = tmp_path / 'poller-runtime.json'
    path.write_text(json.dumps(dict(value, token='must not appear', loaded_head='invalid')))
    public = runtime.public_status(config)
    assert 'token' not in public and public['loaded_head'] is None
    assert not (tmp_path / 'poller-runtime.tmp').exists()
