"""Exact contract policy test_contract_objects checks."""
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




def test_fetch_is_bounded_isolated_and_rejects_invalid_git_output(packet, monkeypatch):
    pr = packet['pr']
    monkeypatch.setenv('GIT_DIR', '/untrusted')
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    seen = []
    def execute(args, **kw):
        seen.append(args)
        assert 'GIT_DIR' not in kw['env'] and 'GIT_CONFIG_COUNT' not in kw['env']
        assert kw['env']['GIT_CONFIG_GLOBAL'] == os.devnull
        assert kw['env']['GIT_CONFIG_NOSYSTEM'] == '1'
        assert kw['timeout'] <= 90 and kw['timeout'] > 0
        return subprocess.CompletedProcess(args, 1, b'', b'private remote error')
    monkeypatch.setattr(p.subprocess, 'run', execute)
    with pytest.raises(p.ContractUnavailable) as error:
        p.fetch_contract_packet(pr.owner, pr.repo, pr.number, pr.head_sha, pr.base_sha)
    assert 'private remote error' not in str(error.value)
    assert len(seen) == 1


def test_arbitrary_repository_or_non_full_base_never_starts_git(packet, monkeypatch):
    execute = Mock(side_effect=AssertionError('must not execute'))
    monkeypatch.setattr(p.subprocess, 'run', execute)
    pr = packet['pr']
    for owner, base in [('someone', pr.base_sha), (pr.owner, 'short'), (pr.owner, None)]:
        with pytest.raises(p.ContractUnavailable):
            p.fetch_contract_packet(owner, pr.repo, pr.number, pr.head_sha, base)
    execute.assert_not_called()


def test_shell_metacharacters_and_git_options_in_metadata_never_start_git(packet, monkeypatch):
    execute = Mock(side_effect=AssertionError('untrusted metadata must not execute'))
    monkeypatch.setattr(p.subprocess, 'run', execute)
    pr = packet['pr']
    metadata = [pr.owner, pr.repo, pr.number, pr.head_sha, pr.base_sha]
    for index in (0, 1, 2, 3, 4):
        for value in ('$(printf untrusted)', '; printf untrusted',
                      '--upload-pack=untrusted', '-C', 'a' * 40 + '\n'):
            supplied = list(metadata)
            supplied[index] = value
            with pytest.raises(p.ContractUnavailable):
                p.fetch_contract_packet(*supplied)
    execute.assert_not_called()


def test_complete_fetch_path_proves_immutable_objects_without_a_checkout(packet, monkeypatch):
    original = subprocess.run
    commands = []
    def execute(args, **kw):
        commands.append(args)
        assert kw['cwd'] != str(packet['root'])
        assert 'checkout' not in args
        assert kw['env']['GIT_NO_REPLACE_OBJECTS'] == '1'
        if 'fetch' in args:
            # Substitute only test transport. All Git object/merge-base/blob
            # operations still run against the new isolated bare repository.
            args = [str(packet['root']) if a == 'https://github.com/rupret007/bob-ops-dashboard.git'
                    else a for a in args]
        return original(args, **kw)
    monkeypatch.setattr(p.subprocess, 'run', execute)
    pr = packet['pr']
    diff, files, proof = p.fetch_contract_packet(pr.owner, pr.repo, pr.number, pr.head_sha, pr.base_sha)
    assert diff == pr.diff and proof == pr.test_contract_proof
    assert [f['path'] for f in files] == pr.changed_files
    assert any('merge-base' in args for args in commands)
    assert any('show' in args for args in commands)


def test_policy_files_do_not_add_pages_publication_paths(monkeypatch):
    paths = sorted(p.POLICY_FILES)
    assert all(not path.startswith('docs/') for path in paths)
    diff = ''.join('diff --git a/{0} b/{0}\n'.format(path) for path in paths)
    pr = checks.PRInfo('rupret007', 'bob-ops-dashboard', 106, 'a' * 40,
                       'Policy', 'author', False, diff, {}, paths, [])
    pages = Mock(side_effect=AssertionError('operational note must stay outside the Pages tree'))
    monkeypatch.setattr(checks, '_pages_source', pages)
    assert checks.check_pages_exposure(pr, KarenConfig()).passed
    pages.assert_not_called()


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
