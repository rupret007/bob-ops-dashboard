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


def test_only_the_proved_exact_packet_discharges_the_removed_assertion(packet):
    pr = packet['pr']
    assert checks.check_tests_not_weakened(replace(pr, test_contract_proof=None), KarenConfig()).failed
    assert checks.check_tests_not_weakened(pr, KarenConfig()).passed
    assert p.authorized_assertion(pr)[1] == 185


@pytest.mark.parametrize('field,value', [
    ('owner', 'someone'), ('repo', 'another-repo'), ('number', 105),
    ('head_sha', 'a' * 40), ('base_sha', 'b' * 40),
    ('test_contract_proof', None), ('test_contract_proof', {'approved': True}),
    ('changed_files', []), ('deleted_files', ['test_deleted.py']),
])
def test_other_identity_metadata_or_untyped_proof_has_no_exception(packet, field, value):
    pr = replace(packet['pr'], **{field: value})
    assert checks.check_tests_not_weakened(pr, KarenConfig()).failed


@pytest.mark.parametrize('change', ['reverse', 'alter', 'duplicate', 'relocate', 'fixture', 'branch',
                                   'neighbor-assert', 'delete-test', 'delete-decorator', 'skip'])
def test_changed_review_packet_never_uses_the_exception(packet, change):
    pr = packet['pr']
    changes = {
        'reverse': pr.diff.replace('-' + p.APPROVED.old, '-' + p.APPROVED.new).replace('+' + p.APPROVED.new, '+' + p.APPROVED.old),
        'alter': pr.diff.replace(p.APPROVED.new, p.APPROVED.new.replace("'none'", "'other'")),
        'duplicate': pr.diff + pr.diff,
        'relocate': pr.diff.replace('@@ -182,4 +182,4 @@', '@@ -282,4 +282,4 @@'),
        'fixture': pr.diff + '\n+    peer["passed"] = True\n',
        'branch': pr.diff + '\n+    if False: pass\n',
        'neighbor-assert': pr.diff + '\n-    assert another_guard\n',
        'delete-test': pr.diff + '\n-def test_another_guard():\n',
        'delete-decorator': pr.diff + '\n-@pytest.mark.parametrize("case", cases)\n',
        'skip': pr.diff + '\n+@pytest.mark.skip(reason="later")\n',
    }
    altered = changes[change]
    # Move the real hunk irrespective of its context size.
    if change == 'relocate': altered = pr.diff.replace('@@ -', '@@ -9', 1)
    assert altered != pr.diff
    assert checks.check_tests_not_weakened(replace(pr, diff=altered), KarenConfig()).failed


@pytest.mark.parametrize('field,value', [
    ('base', None), ('base', 'short'), ('diff_sha256', 'bad'),
    ('new_line', False), ('new_line', 0), ('new_line', 186),
    ('files', ()), ('deleted', ('tests/test_other.py',)),
])
def test_malformed_typed_proof_cannot_hide_a_finding(packet, field, value):
    pr = packet['pr']
    candidate = replace(pr, test_contract_proof=replace(pr.test_contract_proof, **{field: value}))
    assert checks.check_tests_not_weakened(candidate, KarenConfig()).failed


@pytest.mark.parametrize('field,value', [
    ('baseline', 'a' * 40), ('path', 'tests/test_other.py'), ('function', 'test_other'),
    ('old', '    assert True'), ('new', '    assert True'), ('old_line', 184),
])
def test_proof_for_a_different_contract_is_rejected(packet, field, value):
    pr = packet['pr']
    forged = replace(pr.test_contract_proof, contract=replace(p.APPROVED, **{field: value}))
    assert checks.check_tests_not_weakened(replace(pr, test_contract_proof=forged), KarenConfig()).failed


@pytest.mark.parametrize('kind', ['unrelated', 'policy-content', 'policy-mode', 'missing-snapshot',
                                  'merge-base', 'baseline-ancestor', 'packet-digest', 'object-missing'])
def test_unproved_base_advancement_and_incomplete_objects_fail_closed(packet, kind, monkeypatch):
    git = packet['git']
    base = packet['base']
    if kind in ('unrelated', 'policy-content', 'policy-mode'):
        path = 'other.py' if kind == 'unrelated' else sorted(p.POLICY_FILES)[0]
        dest = packet['root'] / path
        if kind == 'policy-mode': dest.chmod(0o755)
        else: dest.write_text('changed source\n')
        git('add', '.')
        git('commit', '-qm', kind)
        base = git('rev-parse', 'HEAD').decode().strip()
        packet['runtime'].update(installed_head=base, loaded_head=base)
        monkeypatch.setattr(p, '_LOADED_BASE', base)
    elif kind == 'missing-snapshot': monkeypatch.setattr(p, '_LOADED_FILES', {})
    elif kind == 'packet-digest': monkeypatch.setattr(p, 'APPROVED', replace(p.APPROVED, diff_sha256='bad'))
    else:
        original = git
        def git(*args):
            if kind == 'merge-base' and args[:2] == ('merge-base', base): return b'a' * 40 + b'\n'
            if kind == 'baseline-ancestor' and args[:2] == ('merge-base', packet['admitted_base']): return b'b' * 40 + b'\n'
            if kind == 'object-missing' and args[0] == 'show': raise p.ContractUnavailable('objects unavailable')
            return original(*args)
    with pytest.raises(p.ContractUnavailable): p._prove(base, git)


@pytest.mark.parametrize('transform', ['duplicate', 'rename', 'nested', 'different', 'reversed'])
def test_assertion_requires_unique_exact_direct_enclosing_function(packet, transform):
    source = ('def ' + p.APPROVED.function + '():\n' + p.APPROVED.new + '\n')
    if transform == 'duplicate': source += source
    elif transform == 'rename': source = source.replace(p.APPROVED.function, 'test_elsewhere')
    elif transform == 'nested': source = source.replace(p.APPROVED.new, '    if False:\n    ' + p.APPROVED.new)
    elif transform == 'different': source = source.replace("'none'", "'else'")
    elif transform == 'reversed': source = source.replace(p.APPROVED.new, p.APPROVED.old)
    with pytest.raises(p.ContractUnavailable): p._assertion_line(source.encode(), p.APPROVED.new)


def test_other_gates_still_reject_the_proved_packet(packet):
    pr = replace(packet['pr'], is_draft=True, ci_status={'test': 'failure'})
    results = checks.run_all_checks(pr, KarenConfig())
    by_name = {r.check_name: r for r in results}
    assert by_name['tests_not_weakened'].passed
    assert by_name['not_draft'].failed and by_name['ci_green'].failed
