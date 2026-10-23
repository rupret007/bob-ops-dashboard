"""The complete policy must prove both parents of the reviewed141 merge."""
from dataclasses import replace

import pytest

from cluster.control_plane.local_karen import contract_policy as policy, checks
from cluster.control_plane.local_karen.config import KarenConfig
from .conftest import packet


@pytest.fixture
def merged(packet, monkeypatch):
    git, root = packet['git'], packet['root']
    first = packet['admitted_base']
    git('checkout', '-q', '-b', 'reviewed-side', first)
    (root / 'reviewed-side.txt').write_text('the exact reviewed side head\n')
    git('add', '.')
    git('commit', '-qm', 'reviewed side')
    second = git('rev-parse', 'HEAD').decode().strip()
    git('checkout', '-q', '--detach', first)
    git('merge', '--no-ff', '-qm', 'reviewed merge', second)
    merge = git('rev-parse', 'HEAD').decode().strip()
    assert git('rev-list', '--parents', '-n', '1', merge).decode().split() == [merge, first, second]
    monkeypatch.setattr(policy, 'BASE_ADVANCEMENTS',
                        policy.BASE_ADVANCEMENTS + ((merge, (first, second)),))
    monkeypatch.setattr(policy, 'ADMITTED_BASE', merge)
    for name, raw in packet['loaded'].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    git('add', '.')
    git('commit', '-qm', 'policy after reviewed merge')
    base = git('rev-parse', 'HEAD').decode().strip()
    packet['runtime'].update(installed_head=base, loaded_head=base)
    monkeypatch.setattr(policy, '_LOADED_BASE', base)
    return dict(packet, base=base, merge=merge, parents=(first, second))


def test_exact_reviewed_merge_allows_only_the_frozen_assertion_pair(merged):
    raw, files, proof = policy._prove(merged['base'], merged['git'])
    pr = replace(merged['pr'], base_sha=merged['base'], test_contract_proof=proof)
    assert raw == pr.diff
    assert proof.contract == merged['record']
    assert tuple(value['path'] for value in files) == proof.files
    assert checks.check_tests_not_weakened(pr, KarenConfig()).passed


@pytest.mark.parametrize('kind', ['sole-parent', 'missing', 'altered', 'reordered',
    'duplicate', 'extra', 'list', 'empty', 'self', 'short', 'not-text'])
def test_unproved_merge_parent_list_refuses(merged, monkeypatch, kind):
    first, second = merged['parents']
    values = {
        'sole-parent': first, 'missing': (first,), 'altered': (first, 'a' * 40),
        'reordered': (second, first), 'duplicate': (first, first),
        'extra': (first, second, 'a' * 40), 'list': [first, second],
        'empty': (), 'self': (first, merged['merge']),
        'short': (first, second[:12]), 'not-text': (first, True),
    }
    monkeypatch.setattr(policy, 'BASE_ADVANCEMENTS',
        policy.BASE_ADVANCEMENTS[:-1] + ((merged['merge'], values[kind]),))
    with pytest.raises(policy.ContractUnavailable):
        policy._prove(merged['base'], merged['git'])


def test_another_merge_cannot_replace_the_reviewed_floor(merged, monkeypatch):
    git, root = merged['git'], merged['root']
    first, second = merged['parents']
    git('checkout', '-q', '-b', 'unlisted-side', first)
    (root / 'unlisted-side.txt').write_text('not the reviewed side\n')
    git('add', '.')
    git('commit', '-qm', 'unlisted side')
    other = git('rev-parse', 'HEAD').decode().strip()
    git('checkout', '-q', '--detach', first)
    git('merge', '--no-ff', '-qm', 'another merge', other)
    assert other != second
    for name, raw in merged['loaded'].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    git('add', '.')
    git('commit', '-qm', 'policy on unlisted merge')
    base = git('rev-parse', 'HEAD').decode().strip()
    merged['runtime'].update(installed_head=base, loaded_head=base)
    monkeypatch.setattr(policy, '_LOADED_BASE', base)
    with pytest.raises(policy.ContractUnavailable):
        policy._prove(base, git)
