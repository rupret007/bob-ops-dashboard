"""Complete-file frozen-contract evidence for this review scope."""
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
from .conftest import packet, fourth_advancement, fifth_advancement

@pytest.mark.parametrize('change', ['other-floor', 'wrong-floor-parent', 'extra-parent'])
def test_only_the_explicit_reviewed_base_advancement_is_admitted(packet, monkeypatch, change):
    git = packet['git']
    if change == 'other-floor':
        monkeypatch.setattr(p, 'ADMITTED_BASE', packet['base'])
    else:
        original = git
        def git(*args):
            if args == ('rev-list', '--parents', '-n', '1', packet['admitted_base']):
                parents = [packet['admitted_base'], packet['baseline']]
                if change == 'extra-parent': parents.append('a' * 40)
                else: parents[1] = 'b' * 40
                return (' '.join(parents) + '\n').encode()
            return original(*args)
    with pytest.raises(p.ContractUnavailable): p._prove(packet['base'], git)


@pytest.mark.parametrize('change', ['first-parent', 'first-extra-parent', 'second-parent',
                                   'second-extra-parent', 'gap', 'reversed', 'omitted',
                                   'omitted-middle', 'empty'])
def test_every_pinned_advancement_edge_is_required(packet, monkeypatch, change):
    git = packet['git']
    if change in ('first-parent', 'first-extra-parent', 'second-parent', 'second-extra-parent'):
        original = git
        selected = packet['second_base'] if change.startswith('second') else packet['first_base']
        parent = packet['first_base'] if change.startswith('second') else packet['baseline']
        def git(*args):
            if args == ('rev-list', '--parents', '-n', '1', selected):
                parents = [selected, parent]
                if change.endswith('-parent') and not change.endswith('extra-parent'): parents[1] = 'b' * 40
                else: parents.append('a' * 40)
                return (' '.join(parents) + '\n').encode()
            return original(*args)
    else:
        chain = p.BASE_ADVANCEMENTS
        changes = {'gap': ((packet['admitted_base'], packet['baseline']),),
                   'reversed': tuple(reversed(chain)), 'omitted': chain[:-1],
                   'omitted-middle': (chain[0], chain[-1]), 'empty': ()}
        monkeypatch.setattr(p, 'BASE_ADVANCEMENTS', changes[change])
    with pytest.raises(p.ContractUnavailable): p._prove(packet['base'], git)


def test_fourth_reviewed_context_advancement_preserves_the_exact_contract(fourth_advancement):
    case = fourth_advancement
    raw, files, proof = p._prove(case['base'], case['git'])
    pr = replace(case['pr'], base_sha=case['base'], test_contract_proof=proof)
    assert raw == pr.diff and tuple(f['path'] for f in files) == proof.files
    assert checks.check_tests_not_weakened(pr, KarenConfig()).passed
    assert proof.contract == case['record'] and proof.contract.head == case['head']


@pytest.mark.parametrize('change', ['missing-last', 'wrong-parent', 'extra-parent'])
def test_fourth_context_edge_cannot_be_inferred_or_widened(fourth_advancement, monkeypatch, change):
    case = fourth_advancement
    git = case['git']
    if change == 'missing-last':
        monkeypatch.setattr(p, 'BASE_ADVANCEMENTS', p.BASE_ADVANCEMENTS[:-1])
    else:
        original = git
        def git(*args):
            if args == ('rev-list', '--parents', '-n', '1', case['fourth']):
                parents = [case['fourth'], case['admitted_base']]
                if change == 'wrong-parent': parents[1] = 'b' * 40
                else: parents.append('a' * 40)
                return (' '.join(parents) + '\n').encode()
            return original(*args)
    with pytest.raises(p.ContractUnavailable): p._prove(case['base'], git)


def test_fifth_reviewed_context_advancement_preserves_the_exact_contract(fifth_advancement):
    case = fifth_advancement
    raw, files, proof = p._prove(case['base'], case['git'])
    pr = replace(case['pr'], base_sha=case['base'], test_contract_proof=proof)
    assert raw == pr.diff and tuple(f['path'] for f in files) == proof.files
    assert checks.check_tests_not_weakened(pr, KarenConfig()).passed
    assert proof.contract == case['record'] and proof.contract.head == case['head']


@pytest.mark.parametrize('change', ['missing-last', 'wrong-parent', 'extra-parent'])
def test_fifth_context_edge_cannot_be_inferred_or_widened(fifth_advancement, monkeypatch, change):
    case = fifth_advancement
    git = case['git']
    if change == 'missing-last':
        monkeypatch.setattr(p, 'BASE_ADVANCEMENTS', p.BASE_ADVANCEMENTS[:-1])
    else:
        original = git
        def git(*args):
            if args == ('rev-list', '--parents', '-n', '1', case['fifth']):
                parents = [case['fifth'], case['fourth']]
                if change == 'wrong-parent': parents[1] = 'b' * 40
                else: parents.append('a' * 40)
                return (' '.join(parents) + '\n').encode()
            return original(*args)
    with pytest.raises(p.ContractUnavailable): p._prove(case['base'], git)


def test_fifth_context_edge_does_not_admit_an_unlisted_later_release(fifth_advancement, monkeypatch):
    case = fifth_advancement
    (case['root'] / 'unlisted-release.txt').write_text('not the approved context change\n')
    case['git']('add', '.')
    case['git']('commit', '-qm', 'unlisted later release')
    base = case['git']('rev-parse', 'HEAD').decode().strip()
    case['runtime'].update(installed_head=base, loaded_head=base)
    monkeypatch.setattr(p, '_LOADED_BASE', base)
    with pytest.raises(p.ContractUnavailable): p._prove(base, case['git'])
