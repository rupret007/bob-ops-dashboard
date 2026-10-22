"""The object helper proves data while the existing checker still refuses it."""
from dataclasses import replace
import hashlib
import os
import subprocess

import pytest

from cluster.control_plane.local_karen import checks, contract_objects as objects
from cluster.control_plane.local_karen.config import KarenConfig


FROZEN_CONTRACT = objects.APPROVED


@pytest.fixture
def packet(tmp_path, monkeypatch):
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
    def git(*args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args],
            cwd=tmp_path, env=env, stderr=subprocess.DEVNULL)
    git('init', '-q')
    git('config', 'user.name', 'Frozen packet test')
    git('config', 'user.email', 'policy-test')
    record = objects.APPROVED
    path = tmp_path / record.path
    path.parent.mkdir(parents=True)
    old = '# fixture\n' * 182 + 'def ' + record.function + '():\n    # guardian\n' + record.old + '\n'
    path.write_text(old)
    git('add', '.');git('commit', '-qm', 'baseline')
    baseline = git('rev-parse', 'HEAD').decode().strip()
    path.write_text(old.replace(record.old, record.new))
    git('add', '.');git('commit', '-qm', 'correction')
    head = git('rev-parse', 'HEAD').decode().strip()
    raw = git(*objects.DIFF_ARGS, baseline, head)
    # A disposable unit graph, never represented as the real approved objects.
    monkeypatch.setattr(objects, 'APPROVED', replace(record, baseline=baseline,
        head=head, diff_sha256=hashlib.sha256(raw).hexdigest()))
    return git, raw


def prove(git):
    record = objects.APPROVED
    return objects.prove_packet(record.owner, record.repo, record.number, record.head, git=git)


def test_exact_objects_are_data_and_do_not_authorize_the_checker(packet):
    git, raw = packet
    value = prove(git)
    assert value.diff.encode() == raw
    assert value.files == ((objects.APPROVED.path, 'M'),)
    assert value.new_line == 185
    record = objects.APPROVED
    pr = checks.PRInfo(record.owner, record.repo, record.number, record.head,
        'Correction', 'author', False, value.diff, {'test': 'success'}, [record.path], [])
    pr.test_contract_proof = value
    assert checks.check_tests_not_weakened(pr, KarenConfig()).failed


def test_packet_cannot_authorize_even_the_real_frozen_pr_identity(packet):
    git, _ = packet
    value = prove(git)
    # Capture the real code-owned identity before the disposable reader fixture.
    # This packet data still supplies no installed-source or runtime proof.
    record = FROZEN_CONTRACT
    pr = checks.PRInfo(record.owner, record.repo, record.number, record.head,
        'Correction', 'author', False, value.diff, {'test': 'success'}, [record.path], [])
    assert checks.check_tests_not_weakened(pr, KarenConfig()).failed
    pr.test_contract_proof = value
    assert checks.check_tests_not_weakened(pr, KarenConfig()).failed


@pytest.mark.parametrize('index,value', [(0, 'other'), (1, 'other'), (2, 105),
                                      (2, 104.0), (3, 'a' * 40)])
def test_other_identity_refuses_before_any_object_read(packet, index, value):
    record = objects.APPROVED
    args = [record.owner, record.repo, record.number, record.head]
    args[index] = value
    calls = []
    with pytest.raises(objects.ContractUnavailable):
        objects.prove_packet(*args, git=lambda *args: calls.append(args))
    assert calls == []


@pytest.mark.parametrize('change', ['digest', 'old', 'new', 'function', 'line',
    'missing', 'nonbytes', 'oversized', 'syntax', 'encoding', 'duplicate-function',
    'nested-assertion', 'name-shape', 'name-duplicate', 'name-status', 'name-target'])
def test_missing_altered_or_incomplete_packet_refuses(packet, change):
    git, _ = packet
    def reader(*args):
        if change == 'missing':raise FileNotFoundError()
        if change == 'nonbytes':return 'not bytes'
        if change == 'oversized':return b'x' * (1024 * 1024 + 1)
        result = git(*args)
        record = objects.APPROVED
        if args[0] == 'diff' and '--name-status' not in args and change == 'digest':return result + b'\n'
        if args[0] == 'show':
            if change in ('old', 'new'):return result.replace(getattr(record, change).encode(), b'    assert True')
            if change == 'function':return result.replace(record.function.encode(), b'test_elsewhere')
            if change == 'line':return b'# extra\n' + result
            if change == 'syntax':return b'def invalid syntax\n'
            if change == 'encoding':return b'\xff'
            if change == 'duplicate-function':return result + result
            if change == 'nested-assertion':return result.replace(record.old.encode(), b'    if False:\n    ' + record.old.encode())
        if '--name-status' in args:
            if change == 'name-shape':return b'M\0'
            if change == 'name-duplicate':return result + result
            if change == 'name-status':return result.replace(b'M\0', b'R100\0', 1)
            if change == 'name-target':return b'M\0another.py\0'
        return result
    with pytest.raises(objects.ContractUnavailable):prove(reader)
