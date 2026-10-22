"""Exact contract policy conftest checks."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import Mock
import pytest
from cluster.control_plane.local_karen import contract_policy as p, checks, github_client
from cluster.control_plane.local_karen.config import KarenConfig


@pytest.fixture
def packet(tmp_path, monkeypatch):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
    def git(*args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args],
                                       cwd=tmp_path, env=env, stderr=subprocess.DEVNULL)
    git('init', '-q')
    git('config', 'user.name', 'Contract tests')
    git('config', 'user.email', 'policy-test')
    path = tmp_path / p.APPROVED.path
    path.parent.mkdir(parents=True)
    old = ('# baseline fixture\n' * 182 + 'def ' + p.APPROVED.function + '():\n'
           + '    # semantic-yellow has a failed unknown guardian check\n' + p.APPROVED.old + '\n')
    path.write_text(old)
    git('add', '.')
    git('commit', '-qm', 'frozen baseline')
    baseline = git('rev-parse', 'HEAD').decode().strip()
    path.write_text(old.replace(p.APPROVED.old, p.APPROVED.new))
    git('add', '.')
    git('commit', '-qm', 'contract correction')
    head = git('rev-parse', 'HEAD').decode().strip()
    diff = git(*p._DIFF_ARGS, baseline, head)
    record = replace(p.APPROVED, baseline=baseline, head=head,
                     diff_sha256=hashlib.sha256(diff).hexdigest())
    monkeypatch.setattr(p, 'APPROVED', record)
    git('checkout', '-q', '-b', 'installed-policy', baseline)
    (tmp_path / 'approved-base-change.txt').write_text('separately reviewed base advance\n')
    git('add', '.')
    git('commit', '-qm', 'separately reviewed admitted base fixture')
    first_base = git('rev-parse', 'HEAD').decode().strip()
    (tmp_path / 'second-approved-base-change.txt').write_text('second separately reviewed advancement\n')
    git('add', '.')
    git('commit', '-qm', 'second separately reviewed admitted base fixture')
    second_base = git('rev-parse', 'HEAD').decode().strip()
    (tmp_path / 'third-approved-base-change.txt').write_text('third separately reviewed advancement\n')
    git('add', '.')
    git('commit', '-qm', 'third separately reviewed admitted base fixture')
    admitted_base = git('rev-parse', 'HEAD').decode().strip()
    monkeypatch.setattr(p, 'ADMITTED_BASE', admitted_base)
    monkeypatch.setattr(p, 'BASE_ADVANCEMENTS', (
        (first_base, baseline), (second_base, first_base), (admitted_base, second_base)))
    loaded = {f: ('# loaded policy fixture: ' + f + '\n').encode() for f in p.POLICY_FILES}
    for f, content in loaded.items():
        dest = tmp_path / f
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    git('add', '.')
    git('commit', '-qm', 'normally installed policy fixture')
    base = git('rev-parse', 'HEAD').decode().strip()
    monkeypatch.setattr(p, '_LOADED_FILES', loaded)
    runtime = dict(installed_head=base, loaded_head=base, source_matches=True)
    monkeypatch.setattr(p, 'deployment_status', lambda: dict(runtime))
    monkeypatch.setattr(p, '_LOADED_BASE', base)  # This fixture models a newly started installed policy.
    raw, files, proof = p._prove(base, git)
    pr = checks.PRInfo(record.owner, record.repo, record.number, record.head,
                       'Correction', 'author', False, raw, {'test': 'success'},
                       [f['path'] for f in files], [], base_sha=base, test_contract_proof=proof)
    return dict(pr=pr, git=git, root=tmp_path, baseline=baseline, base=base, admitted_base=admitted_base,
                head=head, runtime=runtime, loaded=loaded, record=record,
                first_base=first_base, second_base=second_base)


@pytest.fixture
def fourth_advancement(packet, monkeypatch):
    git = packet['git']
    git('checkout', '-q', '-b', 'fourth-admitted', packet['admitted_base'])
    (packet['root'] / 'fourth-reviewed-context.txt').write_text('separately reviewed context change\n')
    git('add', '.')
    git('commit', '-qm', 'fourth separately reviewed base fixture')
    fourth = git('rev-parse', 'HEAD').decode().strip()
    chain = p.BASE_ADVANCEMENTS + ((fourth, packet['admitted_base']),)
    monkeypatch.setattr(p, 'BASE_ADVANCEMENTS', chain)
    monkeypatch.setattr(p, 'ADMITTED_BASE', fourth)
    for name, content in packet['loaded'].items():
        dest = packet['root'] / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    git('add', '.')
    git('commit', '-qm', 'normally installed policy after fourth fixture')
    base = git('rev-parse', 'HEAD').decode().strip()
    packet['runtime'].update(installed_head=base, loaded_head=base)
    monkeypatch.setattr(p, '_LOADED_BASE', base)
    return dict(packet, base=base, fourth=fourth)


@pytest.fixture
def fifth_advancement(fourth_advancement, monkeypatch):
    case = fourth_advancement
    git = case['git']
    git('checkout', '-q', '-b', 'fifth-admitted', case['fourth'])
    (case['root'] / 'fifth-reviewed-context.txt').write_text('reviewed context extraction\n')
    git('add', '.')
    git('commit', '-qm', 'fifth separately reviewed base fixture')
    fifth = git('rev-parse', 'HEAD').decode().strip()
    monkeypatch.setattr(p, 'BASE_ADVANCEMENTS', p.BASE_ADVANCEMENTS + ((fifth, case['fourth']),))
    monkeypatch.setattr(p, 'ADMITTED_BASE', fifth)
    for name, content in case['loaded'].items():
        dest = case['root'] / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    git('add', '.')
    git('commit', '-qm', 'normally installed policy after fifth fixture')
    base = git('rev-parse', 'HEAD').decode().strip()
    case['runtime'].update(installed_head=base, loaded_head=base)
    monkeypatch.setattr(p, '_LOADED_BASE', base)
    return dict(case, base=base, fifth=fifth)
