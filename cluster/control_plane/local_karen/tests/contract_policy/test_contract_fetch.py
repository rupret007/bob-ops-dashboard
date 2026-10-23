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
from .conftest import packet

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
