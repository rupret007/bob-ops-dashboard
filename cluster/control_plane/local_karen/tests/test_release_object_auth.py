"""Real altered loose objects must not inherit an approved object identity."""
import hashlib
import zlib
import subprocess

import pytest

from cluster.control_plane import release_artifacts as artifacts
from .test_release_artifacts import commit, git, source


@pytest.mark.parametrize('target', ['commit', 'root', 'descendant'])
def test_changed_object_body_cannot_publish_under_its_previous_identity(source, target):
    repo, head, store = source
    before = {'commit': head, 'root': git(repo, 'rev-parse', head+'^{tree}'),
              'descendant': git(repo, 'rev-parse', head+':example')}
    after_head = commit(repo, 'after')
    after = {'commit': after_head, 'root': git(repo, 'rev-parse', after_head+'^{tree}'),
             'descendant': git(repo, 'rev-parse', after_head+':example')}
    old_oid, new_oid = before[target], after[target]
    assert old_oid != new_oid
    objects = repo/'.git/objects'
    old_path, new_path = (objects/oid[:2]/oid[2:] for oid in (old_oid, new_oid))
    replacement = new_path.read_bytes()
    assert hashlib.sha1(zlib.decompress(replacement)).hexdigest() == new_oid
    old_path.chmod(0o644)
    old_path.write_bytes(replacement)
    old_path.chmod(0o444)
    # This is real Git reading a corrupt local object, not a mocked inventory.
    kind = 'commit' if target == 'commit' else 'tree'
    body = subprocess.check_output(['git', '-C', str(repo), 'cat-file', kind, old_oid])
    assert hashlib.sha1(kind.encode()+b' '+str(len(body)).encode()+b'\0'+body).hexdigest() != old_oid
    with pytest.raises(artifacts.ArtifactError):
        artifacts.build(repo, head, store, allowed=lambda: True)
    assert not (store/head).exists() and not list(store.glob('.stage-*'))


def test_authenticated_tree_preserves_git_directory_sorting_and_nested_names(source):
    repo, _, store = source
    for name in ['a.py', 'a/x.py', 'a.b.py', 'a0.py', 'a/nested/é.py', 'a-/z.py']:
        path = repo/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('VALUE = '+repr(name)+'\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'Nested Git tree ordering')
    head = git(repo, 'rev-parse', 'HEAD')
    identity = artifacts.build(repo, head, store, allowed=lambda: True)
    root = artifacts.verify(store, identity, allowed=lambda: True)
    for name in ['a.py', 'a/x.py', 'a.b.py', 'a0.py', 'a/nested/é.py', 'a-/z.py']:
        assert (root/name).read_bytes() == (repo/name).read_bytes()
