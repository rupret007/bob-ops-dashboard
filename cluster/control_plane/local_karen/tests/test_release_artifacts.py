"""Real Git objects/files/process imports; no service or admission is modeled."""
from dataclasses import replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import selectors
import shlex
import subprocess
import sys

import pytest

from cluster.control_plane import release_artifacts as artifacts


def git(repo, *args):
    env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
    result = subprocess.run(['git', '-c', 'core.hooksPath='+os.devnull,
        '-c', 'commit.gpgsign=false', '-c', 'user.name=Private artifact test',
        '-c', 'user.email=artifact-test', '-C', str(repo), *args],
        env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def commit(repo, value):
    for name in ['early.py', 'late.py']:
        (repo/'example'/name).write_text('VALUE = '+repr(value)+'\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'Private source '+value)
    return git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def source(tmp_path):
    repo = (tmp_path/'repo').resolve(); repo.mkdir(mode=0o700)
    git(repo, 'init', '-q')
    (repo/'example').mkdir()
    (repo/'example/__init__.py').write_text('')
    (repo/'run.sh').write_text('#!/bin/sh\nexit 0\n'); (repo/'run.sh').chmod(0o755)
    (repo/'.gitattributes').write_text('example/late.py export-ignore\nmarker.txt export-subst\n')
    (repo/'marker.txt').write_text('$Format:%H$\n')
    head = commit(repo, 'before')
    return repo, head, (tmp_path/'releases').resolve()


def ready(child):
    with selectors.DefaultSelector() as observer:
        observer.register(child.stdout, selectors.EVENT_READ)
        assert observer.select(timeout=5), 'Owned child did not become ready'
        assert child.stdout.readline().strip() == 'READY'


def finish(child):
    try:
        if child.poll() is None: child.kill()
        child.wait(timeout=5)
    finally:
        for stream in [child.stdin, child.stdout, child.stderr]:
            if stream: stream.close()


def test_complete_git_source_is_fixed_idempotent_and_independent_of_worktree(source):
    repo, head, store = source
    (repo/'example/late.py').write_text('VALUE = "uncommitted"\n')
    identity = artifacts.build(repo, head, store, allowed=lambda: True)
    root = artifacts.verify(store, identity, allowed=lambda: True)
    assert root == store/head/'source'
    assert (root/'example/late.py').read_text() == 'VALUE = \'before\'\n'
    assert (root/'marker.txt').read_text() == '$Format:%H$\n'
    assert (root/'run.sh').stat().st_mode & 0o777 == 0o555
    assert (root/'example/late.py').stat().st_mode & 0o777 == 0o444
    assert root.stat().st_mode & 0o777 == 0o555
    before = {str(p.relative_to(store)):(p.stat().st_ino, p.stat().st_mtime_ns)
              for p in store.rglob('*')}
    assert artifacts.build(repo, head, store, allowed=lambda: True) == identity
    assert before == {str(p.relative_to(store)):(p.stat().st_ino, p.stat().st_mtime_ns)
                      for p in store.rglob('*')}
    assert not list(store.glob('.stage-*'))
    assert not (root/'.git').exists()


def test_running_imports_keep_their_release_and_previous_source_remains_usable(source, tmp_path):
    repo, before, store = source
    first = artifacts.build(repo, before, store, allowed=lambda: True)
    root = artifacts.verify(store, first, allowed=lambda: True)
    state = tmp_path/'shared'; state.mkdir()
    protected = {}
    for name in ['configuration', 'credential', 'community']:
        path = state/name; path.write_bytes(('synthetic '+name).encode()); protected[path] = path.read_bytes()
    code = '''import sys
sys.path.insert(0,sys.argv[1])
from example.early import VALUE
print('READY',flush=True)
sys.stdin.readline()
from example.late import VALUE as LATE
print(VALUE+':'+LATE,flush=True)
'''
    child = subprocess.Popen([sys.executable, '-I', '-B', '-u', '-c', code, str(root)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready(child)
        second_head = commit(repo, 'after')
        second = artifacts.build(repo, second_head, store, allowed=lambda: True)
        second_root = artifacts.verify(store, second, allowed=lambda: True)
        out, err = child.communicate('\n', timeout=5)
        assert child.returncode == 0 and out.strip() == 'before:before' and not err
        new = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(second_root)],
            input='\n', capture_output=True, text=True, timeout=5)
        assert new.returncode == 0 and new.stdout.splitlines() == ['READY','after:after']
        # This verifies previous artifact reuse, not actual native rollback or
        # a synthetic health verdict that could authorize production promotion.
        previous = artifacts.verify(store, first, allowed=lambda: True)
        restored = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(previous)],
            input='\n', capture_output=True, text=True, timeout=5)
        assert restored.returncode == 0 and restored.stdout.splitlines() == ['READY','before:before']
        assert all(path.read_bytes() == raw for path, raw in protected.items())
        assert set(p.name for p in store.iterdir()) == {before, second_head, '.publish.lock'}
    finally: finish(child)


@pytest.mark.parametrize('fault', ['content','mode','manifest','extra','empty-directory','directory-link','hardlink'])
def test_changed_or_unattributed_artifacts_refuse(source, fault):
    repo, head, store = source
    identity = artifacts.build(repo, head, store, allowed=lambda: True)
    root = artifacts.verify(store, identity, allowed=lambda: True)
    path = root/'example/late.py'
    if fault == 'content':
        path.chmod(0o644); path.write_text('VALUE = "operator"\n'); path.chmod(0o444)
    elif fault == 'mode': path.chmod(0o600)
    elif fault == 'manifest':
        manifest = store/head/'manifest.json'
        manifest.chmod(0o644); manifest.write_text('{}'); manifest.chmod(0o444)
    else:
        root.chmod(0o755)
        if fault == 'extra': (root/'unexpected.py').write_text('')
        elif fault == 'empty-directory': (root/'unexpected').mkdir(mode=0o555)
        elif fault == 'directory-link':
            (root/'example').chmod(0o755)  # macOS requires write permission to rename this directory.
            (root/'example').rename(root/'original-example')
            (root/'original-example').chmod(0o555)
            (root/'example').symlink_to(root/'original-example', target_is_directory=True)
        elif fault == 'hardlink': os.link(path, root/'alias.py')
        root.chmod(0o555)
    with pytest.raises(artifacts.ArtifactError): artifacts.verify(store, identity, allowed=lambda: True)
    with pytest.raises(artifacts.ArtifactError): artifacts.build(repo, head, store, allowed=lambda: True)


@pytest.mark.parametrize('field,size', [('head',40),('tree',40),('manifest_sha256',64)])
def test_manifest_does_not_authorize_its_own_identity(source, field, size):
    repo, head, store = source
    identity = artifacts.build(repo, head, store, allowed=lambda: True)
    with pytest.raises(artifacts.ArtifactError):
        artifacts.verify(store, replace(identity, **{field:'0'*size}), allowed=lambda: True)


def test_held_preparation_does_not_publish_or_read_objects(source, monkeypatch):
    repo, head, store = source
    def forbidden(*args, **kwargs): raise AssertionError('Held preparation read Git')
    monkeypatch.setattr(artifacts, '_git', forbidden)
    with pytest.raises(artifacts.ArtifactError, match='held'):
        artifacts.build(repo, head, store, allowed=lambda: False)
    assert not store.exists()


def test_publication_failure_preserves_previous_release_and_retry(source, monkeypatch):
    repo, head, store = source
    previous = artifacts.build(repo, head, store, allowed=lambda: True)
    next_head = commit(repo, 'after')
    rename = artifacts.os.rename
    def interrupted(*args): raise OSError('synthetic publication interruption')
    monkeypatch.setattr(artifacts.os, 'rename', interrupted)
    with pytest.raises(OSError): artifacts.build(repo, next_head, store, allowed=lambda: True)
    assert not (store/next_head).exists()
    assert artifacts.verify(store, previous, allowed=lambda: True).is_dir()
    stages = set(store.glob('.stage-*')); assert len(stages) == 1
    monkeypatch.setattr(artifacts.os, 'rename', rename)
    current = artifacts.build(repo, next_head, store, allowed=lambda: True)
    assert artifacts.verify(store, current, allowed=lambda: True).is_dir()
    assert artifacts.verify(store, previous, allowed=lambda: True).is_dir()
    assert set(store.glob('.stage-*')) == stages  # Interrupted evidence is retained.


def test_real_competing_publisher_cannot_enter(source):
    repo, head, store = source
    store.mkdir(mode=0o700); path = store/'.publish.lock'; path.touch(mode=0o600)
    code = "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('READY',flush=True); sys.stdin.readline()"
    child = subprocess.Popen([sys.executable, '-I', '-B', '-u', '-c', code, str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready(child)
        with pytest.raises(artifacts.ArtifactError, match='publisher'):
            artifacts.build(repo, head, store, allowed=lambda: True)
        assert set(p.name for p in store.iterdir()) == {'.publish.lock'}
    finally: finish(child)
    assert artifacts.verify(store, artifacts.build(repo, head, store, allowed=lambda: True), allowed=lambda: True)


def test_symlink_git_objects_are_not_materialized(source):
    repo, _, store = source
    (repo/'linked').symlink_to('example/late.py')
    git(repo, 'add', '.'); git(repo, 'commit', '-qm', 'Unsupported link')
    with pytest.raises(artifacts.ArtifactError, match='unsupported'):
        artifacts.build(repo, git(repo, 'rev-parse','HEAD'), store, allowed=lambda: True)
    assert not list(store.glob('.stage-*'))


def test_partial_clone_never_invokes_a_promisor_fetch(source, tmp_path, monkeypatch):
    repo, head, store = source
    upstream = tmp_path/'upstream.git'
    git(repo, 'clone', '--bare', '--no-local', str(repo), str(upstream))
    git(upstream, 'config', 'uploadpack.allowFilter', 'true')
    partial = tmp_path/'partial'
    git(repo, 'clone', '--filter=blob:none', '--no-checkout', upstream.as_uri(), str(partial))
    assert git(partial, 'config', '--get', 'remote.origin.promisor') == 'true'
    # A local helper observes a lazy fetch without contacting any network or
    # credentials. Only fixture preparation clones; the builder must not fetch.
    marker = tmp_path/'fetch-attempted'
    directory = tmp_path/'owned-bin'; directory.mkdir()
    helper = directory/'git-remote-artifact-probe'
    helper.write_text('#!/bin/sh\nprintf attempted > '+shlex.quote(str(marker))+'\nexit 1\n')
    helper.chmod(0o755)
    git(partial, 'config', 'remote.origin.url', 'artifact-probe::probe')
    git(partial, 'config', 'protocol.artifact-probe.allow', 'always')
    monkeypatch.setenv('PATH', str(directory)+os.pathsep+os.environ['PATH'])
    with pytest.raises(artifacts.ArtifactError):
        artifacts.build(partial.resolve(), head, store, allowed=lambda: True)
    assert not marker.exists(), 'Source preparation invoked a promisor fetch'
    assert not (store/head).exists() and not list(store.glob('.stage-*'))


def owned_git_program(tmp_path, monkeypatch, program):
    directory = tmp_path/'bounded-git-bin'; directory.mkdir()
    executable = directory/'git'
    executable.write_text('#!'+sys.executable+'\n'+program)
    executable.chmod(0o755)
    monkeypatch.setenv('PATH', str(directory)+os.pathsep+os.environ['PATH'])


@pytest.mark.parametrize('channel', [1, 2])
def test_git_output_limit_stops_the_actual_producer_before_buffering_its_tail(
        source, tmp_path, monkeypatch, channel):
    repo, _, _ = source
    marker = tmp_path/'unbounded-tail-produced'
    pidfile = tmp_path/'bounded-child-pid'
    owned_git_program(tmp_path, monkeypatch,
        'import os\nfrom pathlib import Path\n'
        'Path('+repr(str(pidfile))+').write_text(str(os.getpid()))\n'
        'for _ in range(96): os.write('+str(channel)+", b'x'*65536)\n"
        'Path('+repr(str(marker))+').write_text("tail")\n')
    with pytest.raises(artifacts.ArtifactError, match='output exceeds limit'):
        artifacts._git(repo, ['ls-tree', '-r', 'synthetic-bounded-output'])
    assert not marker.exists(), 'Git output was buffered through its unbounded tail'
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_git_output_at_the_original_inventory_limit_is_complete(source, tmp_path, monkeypatch):
    repo, _, _ = source
    owned_git_program(tmp_path, monkeypatch,
        "import os\nfor _ in range(64): os.write(1, b'x'*65536)\n")
    assert artifacts._git(repo, ['ls-tree', '-r', 'synthetic-boundary']) == b'x'*(4*1024*1024)


def test_git_batch_pipe_reads_output_while_writing_its_complete_input(source, tmp_path, monkeypatch):
    repo, _, _ = source
    payload = (b'0'*40+b'\n')*5000
    owned_git_program(tmp_path, monkeypatch,
        "import hashlib,os,sys\nos.write(1,b'p'*131072)\n"
        "raw=sys.stdin.buffer.read()\nos.write(1,hashlib.sha256(raw).hexdigest().encode())\n")
    assert artifacts._git(repo, ['cat-file', '--batch'], data=payload) == (
        b'p'*131072+hashlib.sha256(payload).hexdigest().encode())


def test_native_acceptance_checks_promisor_configuration_before_its_object_probe(
        source, tmp_path, tmp_path_factory, monkeypatch):
    import importlib.util
    import shutil
    repo, _, _ = source
    upstream = tmp_path/'fixture-upstream.git'
    git(repo, 'clone', '--bare', '--no-local', str(repo), str(upstream))
    git(upstream, 'config', 'uploadpack.allowFilter', 'true')
    home = tmp_path/'owned-home'; home.mkdir()
    partial = home/'storypool-install'
    git(repo, 'clone', '--filter=blob:none', '--no-checkout', upstream.as_uri(), str(partial))
    marker = tmp_path/'fixture-fetch-attempted'
    directory = tmp_path/'fixture-owned-bin'; directory.mkdir()
    helper = directory/'git-remote-artifact-probe'
    helper.write_text('#!/bin/sh\nprintf attempted > '+shlex.quote(str(marker))+'\nexit 1\n')
    helper.chmod(0o755)
    git(partial, 'config', 'remote.origin.url', 'artifact-probe::probe')
    git(partial, 'config', 'protocol.artifact-probe.allow', 'always')
    real_git = shutil.which('git'); assert real_git
    # Reproduce historical Git's lack of GIT_NO_LAZY_FETCH support while
    # retaining real Git object lookup and the owned no-network fetch probe.
    executable = directory/'git'
    executable.write_text('#!'+sys.executable+'\nimport os,sys\n'
        'env=dict(os.environ);env.pop("GIT_NO_LAZY_FETCH",None)\n'
        'os.execve('+repr(real_git)+', ['+repr(real_git)+',*sys.argv[1:]],env)\n')
    executable.chmod(0o755)
    monkeypatch.setenv('PATH', str(directory)+os.pathsep+os.environ['PATH'])
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    path = Path(artifacts.__file__).parent/'tests/native_fixed_release_acceptance.py'
    spec = importlib.util.spec_from_file_location('owned_native_artifact_fixture', path)
    native = importlib.util.module_from_spec(spec); spec.loader.exec_module(native)
    with pytest.raises(AssertionError, match='Exact already-fetched Git objects are unavailable'):
        native.actual.__wrapped__(tmp_path_factory)
    assert not marker.exists(), 'The acceptance fixture fetched before its partial-clone guard'
