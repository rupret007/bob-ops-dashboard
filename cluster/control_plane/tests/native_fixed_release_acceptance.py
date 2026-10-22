"""Actual StoryPool imports from two exact releases in an owned native scope.

Explicit selection only. No service entry, admission, network review, installer
or production rollback is executed. The shared-layout control demonstrates the
late-import hazard using a disposable copy, never the installed source tree.
"""
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess

import pytest

from cluster.control_plane import release_artifacts as artifacts

BEFORE = '3905ca1ad5f3f581a8c8d1d64bda0b52db679ea7'
AFTER = '12e4d8c3c01f81bbc528fb77ab973769b376a7ea'
LATE = 'cluster/control_plane/local_karen/source_context.py'
PROBE = '''import hashlib,importlib,json,os,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve();sys.path.insert(0,str(root))
early=importlib.import_module('cluster.control_plane.guards')
print(json.dumps(dict(phase='ready',pid=os.getpid(),early=str(Path(early.__file__).resolve()))),flush=True)
sys.stdin.readline()
late=importlib.import_module('cluster.control_plane.local_karen.source_context')
path=Path(late.__file__).resolve()
print(json.dumps(dict(phase='late',late=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())),flush=True)
'''


def start(root, runtime):
    env = {k:os.environ[k] for k in ['HOME','PATH','LANG','LC_CTYPE'] if k in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE='1',STORYPOOL_DATA_DIR=str(runtime/'data'),
               STORYPOOL_STATE_DIR=str(runtime/'state'),STORYPOOL_JOBS_DIR=str(runtime/'jobs'))
    return subprocess.Popen(['/usr/bin/python3','-I','-B','-u','-c',PROBE,str(root)],
        cwd=root,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)


def ready(child):
    with selectors.DefaultSelector() as poll:
        poll.register(child.stdout,selectors.EVENT_READ)
        assert poll.select(timeout=5),'Actual source import did not become ready'
        value=json.loads(child.stdout.readline())
    assert value['phase']=='ready' and value['pid']==child.pid
    return value


def late(child):
    stdout,stderr=child.communicate('\n',timeout=5)
    assert child.returncode==0 and not stderr,(child.returncode,stderr)
    value=json.loads(stdout)
    assert value['phase']=='late'
    return value


@pytest.fixture(scope='module')
def actual(tmp_path_factory):
    candidates=[Path.home()/'storypool-install',
        Path.home()/'.local/share/storypool/overseer/infra128-129-receiving-private']
    repo=None
    for candidate in candidates:
        if not candidate.is_dir():continue
        try:
            # This must run before even the fixture's commit-type probes:
            # historical Git may ignore GIT_NO_LAZY_FETCH on a promisor repo.
            for head in [BEFORE,AFTER]:
                artifacts._objects(candidate,head)
            assert all(artifacts._git(candidate,['cat-file','-t',head]).strip()==b'commit'
                       for head in [BEFORE,AFTER])
        except (artifacts.ArtifactError,AssertionError):continue
        repo=candidate;break
    assert repo is not None,'Exact already-fetched Git objects are unavailable; do not fetch or bypass'
    root=tmp_path_factory.mktemp('actual-fixed-artifacts').resolve()
    store=root/'releases'
    first=artifacts.build(repo,BEFORE,store,allowed=lambda:True)
    first_root=artifacts.verify(store,first,allowed=lambda:True)
    before=artifacts._git(repo,['show',BEFORE+':'+LATE])
    after=artifacts._git(repo,['show',AFTER+':'+LATE])
    assert before!=after
    return repo,root,store,first,first_root,before,after


@pytest.mark.parametrize('layout',['fixed','shared-control'])
def test_actual_late_import_during_release_preparation(actual,tmp_path,layout):
    repo,root,store,first,first_root,before,after=actual
    selected=first_root
    if layout=='shared-control':
        selected=tmp_path/'disposable-shared-code'
        shutil.copytree(first_root,selected)
    child=start(selected,tmp_path)
    try:
        beginning=ready(child)
        assert Path(beginning['early']).is_relative_to(selected)
        second=artifacts.build(repo,AFTER,store,allowed=lambda:True)
        second_root=artifacts.verify(store,second,allowed=lambda:True)
        if layout=='shared-control':
            path=selected/LATE;path.chmod(0o644);path.write_bytes(after);path.chmod(0o444)
        observed=late(child)
        expected=before if layout=='fixed' else after
        assert observed['sha256']==hashlib.sha256(expected).hexdigest()
        assert Path(observed['late']).is_relative_to(selected)
        # New processes bind to the newly selected verified root. The original
        # root remains usable for later native/transaction rollback integration.
        current=start(second_root,tmp_path)
        try:
            assert Path(ready(current)['early']).is_relative_to(second_root)
            assert late(current)['sha256']==hashlib.sha256(after).hexdigest()
        finally:
            if current.poll() is None:current.kill()
            current.wait(timeout=5)
        previous=artifacts.verify(store,first,allowed=lambda:True)
        restored=start(previous,tmp_path)
        try:
            assert Path(ready(restored)['early']).is_relative_to(previous)
            assert late(restored)['sha256']==hashlib.sha256(before).hexdigest()
        finally:
            if restored.poll() is None:restored.kill()
            restored.wait(timeout=5)
        assert (first_root/LATE).read_bytes()==before
        (tmp_path/'actual-release-result.json').write_text(json.dumps(dict(layout=layout,
            before=first.__dict__,after=second.__dict__,oldProcess=observed,
            newProcessUsesAfter=True,previousSourceReusable=True,
            admissionExercised=False,nativeServicesInstalled=False,productionRollbackQualified=False)))
    finally:
        if child.poll() is None:child.kill()
        child.wait(timeout=5)
