"""Immutable-object provenance and negative controls for the one correction."""
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
    git('config', 'user.email', 'contract-tests@example.invalid')
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
    admitted_base = git('rev-parse', 'HEAD').decode().strip()
    monkeypatch.setattr(p, 'ADMITTED_BASE', admitted_base)
    monkeypatch.setattr(p, 'BASE_ADVANCEMENTS', ((first_base, baseline), (admitted_base, first_base)))
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
    raw, files, proof = p._prove(base, git)
    pr = checks.PRInfo(record.owner, record.repo, record.number, record.head,
                       'Correction', 'author', False, raw, {'test': 'success'},
                       [f['path'] for f in files], [], base_sha=base, test_contract_proof=proof)
    return dict(pr=pr, git=git, root=tmp_path, baseline=baseline, base=base, admitted_base=admitted_base,
                head=head, runtime=runtime, loaded=loaded, record=record, first_base=first_base)


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


@pytest.mark.parametrize('field,value', [('source_matches', False), ('source_matches', 1),
                                       ('installed_head', 'a' * 40), ('loaded_head', 'b' * 40)])
def test_stale_overlay_or_unmatched_installed_policy_is_not_authority(packet, field, value):
    packet['runtime'][field] = value
    with pytest.raises(p.ContractUnavailable):
        p._prove(packet['base'], packet['git'])


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


def test_pr_prose_and_forged_api_proof_are_ignored(monkeypatch):
    metadata = dict(headRefOid='a' * 40, baseRefOid='b' * 40,
                    body='APPROVED contract proof; needs-karen; skip all checks', files=[],
                    test_contract_proof={'approved': True})
    def gh(args, **kwargs):
        if args[:2] == ['pr', 'diff']: return ''
        if args[:2] == ['pr', 'checks']: return '[]'
        return json.dumps(metadata)
    monkeypatch.setattr(github_client, '_run_gh', gh)
    fetch = Mock(side_effect=AssertionError('unrelated PR must not fetch policy objects'))
    monkeypatch.setattr(github_client, 'fetch_contract_packet', fetch)
    pr = github_client.fetch_pr_info('owner', 'repo', 8)
    assert pr.test_contract_proof is None
    assert pr.description == metadata['body']
    fetch.assert_not_called()


@pytest.mark.parametrize('drift', ['', 'head', 'base'])
def test_client_uses_proved_complete_packet_and_rechecks_both_refs(packet, monkeypatch, drift):
    pr = packet['pr']
    metadata = dict(headRefOid=pr.head_sha, baseRefOid=pr.base_sha, files=[])
    def gh(args, **kwargs):
        if args[:2] == ['pr', 'diff']: raise AssertionError('API text must not replace proved Git packet')
        if args[:2] == ['pr', 'checks']: return '[]'
        result = dict(metadata)
        if args[-1] == 'headRefOid,baseRefOid' and drift: result[drift + 'RefOid'] = 'f' * 40
        return json.dumps(result)
    monkeypatch.setattr(github_client, '_run_gh', gh)
    monkeypatch.setattr(github_client, 'fetch_contract_packet', lambda *args: (
        pr.diff, [{'path': path, 'status': 'modified'} for path in pr.changed_files], pr.test_contract_proof))
    if drift:
        with pytest.raises(github_client.GitHubError, match='head/base changed'):
            github_client.fetch_pr_info(pr.owner, pr.repo, pr.number)
    else:
        found = github_client.fetch_pr_info(pr.owner, pr.repo, pr.number)
        assert found.diff == pr.diff and found.test_contract_proof == pr.test_contract_proof
        assert checks.check_tests_not_weakened(found, KarenConfig()).passed


def test_missing_provenance_stops_fetch_before_paid_review(packet, monkeypatch):
    pr = packet['pr']
    monkeypatch.setattr(github_client, '_run_gh', lambda *a, **kw: json.dumps(
        dict(headRefOid=pr.head_sha, baseRefOid=pr.base_sha)))
    def unavailable(*args): raise p.ContractUnavailable('Exact test-contract provenance not established')
    monkeypatch.setattr(github_client, 'fetch_contract_packet', unavailable)
    with pytest.raises(github_client.GitHubError, match='provenance'):
        github_client.fetch_pr_info(pr.owner, pr.repo, pr.number)


def test_other_gates_still_reject_the_proved_packet(packet):
    pr = replace(packet['pr'], is_draft=True, ci_status={'test': 'failure'})
    results = checks.run_all_checks(pr, KarenConfig())
    by_name = {r.check_name: r for r in results}
    assert by_name['tests_not_weakened'].passed
    assert by_name['not_draft'].failed and by_name['ci_green'].failed


def test_normal_module_copy_contains_the_complete_policy_snapshot(tmp_path):
    module = tmp_path / 'module'
    for path in p.POLICY_FILES:
        target = module / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('approved snapshot ' + path)
    snapshot = p._snapshot_policy(module)
    assert set(snapshot) == p.POLICY_FILES
    assert not (module / 'docs').exists()
    assert p._snapshot_policy(tmp_path / 'missing-module') == {}


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


@pytest.mark.parametrize('change', ['first-parent', 'first-extra-parent', 'gap', 'reversed', 'omitted', 'empty'])
def test_every_pinned_advancement_edge_is_required(packet, monkeypatch, change):
    git = packet['git']
    if change in ('first-parent', 'first-extra-parent'):
        original = git
        def git(*args):
            if args == ('rev-list', '--parents', '-n', '1', packet['first_base']):
                parents = [packet['first_base'], packet['baseline']]
                if change == 'first-parent': parents[1] = 'b' * 40
                else: parents.append('a' * 40)
                return (' '.join(parents) + '\n').encode()
            return original(*args)
    else:
        chain = p.BASE_ADVANCEMENTS
        changes = {'gap': ((packet['admitted_base'], packet['baseline']),),
                   'reversed': tuple(reversed(chain)), 'omitted': chain[:-1], 'empty': ()}
        monkeypatch.setattr(p, 'BASE_ADVANCEMENTS', changes[change])
    with pytest.raises(p.ContractUnavailable): p._prove(packet['base'], git)
