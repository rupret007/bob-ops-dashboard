"""Frozen authorization data is evidence, never an assertion-check waiver."""
from dataclasses import FrozenInstanceError
import re

import pytest

from cluster.control_plane.local_karen import checks, contract_record as record
from cluster.control_plane.local_karen.config import KarenConfig


def test_record_retains_the_authorized_frozen_identity_and_assertion_contract():
    value = record.APPROVED
    assert (value.owner, value.repo, value.number) == ('rupret007', 'bob-ops-dashboard', 104)
    assert value.head == '1f45a6f87a5ff8588bb4c4cb5b58a5690cc4b582'
    assert value.baseline == 'c5650be27329e507fe73ef5fc7a3905b64991469'
    assert value.path == 'cluster/control_plane/tests/test_update_coordination.py'
    assert value.function == 'test_target_gate_requires_direct_fresh_exact_active_observation'
    assert value.old == "    assert updater.target_canary_ready('mini', TARGET) is (change in ('none', 'semantic-yellow'))"
    assert value.new == "    assert updater.target_canary_ready('mini', TARGET) is (change == 'none')"
    assert (value.old_line, value.diff_sha256) == (
        185, '5012f4fa1be0aaf9d271cf210961c600caa20b57509d08ddbee1cf17a40fe956')


@pytest.mark.parametrize('field', ['owner', 'repo', 'number', 'head', 'baseline',
    'path', 'function', 'old', 'new', 'diff_sha256'])
def test_authorization_record_cannot_be_retargeted(field):
    with pytest.raises(FrozenInstanceError):
        setattr(record.APPROVED, field, 'neighboring-unapproved-value')


def test_record_requires_a_contiguous_explicit_parent_chain():
    previous = record.APPROVED.baseline
    assert type(record.BASE_ADVANCEMENTS) is tuple and record.BASE_ADVANCEMENTS
    for commit, parent in record.BASE_ADVANCEMENTS:
        parents = (parent,) if type(parent) is str else parent
        assert type(parents) is tuple and len(parents) in (1, 2)
        assert parents[0] == previous and len(set(parents)) == len(parents)
        assert all(type(v) is str and re.fullmatch('[0-9a-f]{40}', v)
                   for v in (commit, *parents))
        assert commit not in parents
        previous = commit
    assert previous == record.ADMITTED_BASE


def test_declarative_proof_and_a_partial_packet_cannot_relax_the_assertion_guard():
    value = record.APPROVED
    diff = ('diff --git a/'+value.path+' b/'+value.path+'\n'
        '--- a/'+value.path+'\n+++ b/'+value.path+'\n@@ -185 +185 @@\n'
        '-'+value.old+'\n+'+value.new+'\n')
    pr = checks.PRInfo(value.owner, value.repo, value.number, value.head,
        'Unproved partial packet', 'qualification', False, diff,
        {'modeled-ci-only': 'success'}, [value.path], [])
    pr.base_sha = record.ADMITTED_BASE
    proof = record.VerifiedContract(value, record.ADMITTED_BASE,
        value.diff_sha256, (value.path,), (), 185)
    pr.test_contract_proof = proof
    assert checks.check_tests_not_weakened(pr, KarenConfig()).failed
    with pytest.raises(FrozenInstanceError):
        proof.base = '0'*40
