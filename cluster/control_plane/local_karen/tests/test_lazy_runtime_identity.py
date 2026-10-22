"""Real Git revisions qualify the lazy poller module's loaded-code identity."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from cluster.control_plane.local_karen import runtime_status as runtime

PREFIX = "cluster/control_plane/local_karen/"
HELPER = "poller_runtime.py"


def git(root, *args):
    return subprocess.check_output(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "-C", str(root), *args], stderr=subprocess.STDOUT).decode().strip()


@pytest.fixture
def pinned(tmp_path):
    source = Path(runtime.__file__).parent
    target = tmp_path / PREFIX
    target.mkdir(parents=True)
    for name in ["poller.py", "runtime_status.py", HELPER]:
        (target / name).write_bytes((source / name).read_bytes())
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Pinned poller runtime")
    return tmp_path, target, runtime.source_hashes(target)


def test_snapshot_covers_helper_before_its_lazy_import():
    root = Path(runtime.__file__).parents[3]
    code = """
import hashlib, json, sys
from pathlib import Path
from cluster.control_plane.local_karen import poller, runtime_status as identity
name = 'cluster.control_plane.local_karen.poller_runtime'
assert name not in sys.modules
from cluster.control_plane.local_karen import poller_runtime
expected = hashlib.sha256(Path(poller_runtime.__file__).read_bytes()).hexdigest()
assert identity.LOADED_HASHES['poller_runtime.py'] == expected
status = identity.deployment_status(Path(identity.__file__).parents[3])
assert status['source_matches'] and status['installed_head'] == status['loaded_head']
print(json.dumps({'covered_before_import': True, 'loaded_identity_verified': True}))
"""
    value = subprocess.check_output([sys.executable, "-c", code], cwd=root)
    assert json.loads(value) == dict(covered_before_import=True, loaded_identity_verified=True)


def test_pinned_helper_is_accepted_and_an_omitted_helper_is_rejected(pinned):
    root, target, hashes = pinned
    assert hashes[HELPER] == hashlib.sha256((target / HELPER).read_bytes()).hexdigest()
    status = runtime.deployment_status(root, hashes)
    assert status['source_matches'] and status['loaded_head'] == git(root, "rev-parse", "HEAD")
    incomplete = {name: value for name, value in hashes.items() if name != HELPER}
    rejected = runtime.deployment_status(root, incomplete)
    assert not rejected['source_matches'] and rejected['loaded_head'] is None


@pytest.mark.parametrize("change", ["modify", "remove"])
def test_old_snapshot_cannot_claim_a_changed_helper_commit(pinned, change):
    root, target, hashes = pinned
    before = git(root, "rev-parse", "HEAD")
    if change == "modify":
        with (target / HELPER).open("a") as stream:
            stream.write("\nIDENTITY_FIXTURE_REVISION = 2\n")
    else:
        (target / HELPER).unlink()
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Changed runtime identity fixture")
    current = git(root, "rev-parse", "HEAD")
    assert current != before
    status = runtime.deployment_status(root, hashes)
    assert status['installed_head'] == current
    assert not status['source_matches'] and status['loaded_head'] is None
