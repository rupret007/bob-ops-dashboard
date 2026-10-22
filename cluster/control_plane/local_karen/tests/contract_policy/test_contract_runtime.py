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

from cluster.control_plane.local_karen.tests.contract_policy.test_contract_client import contract_observation

@pytest.mark.parametrize('change', ['head', 'saved-base', 'live-base'])
def test_every_ref_is_rechecked_after_immutable_proof(packet, monkeypatch, change):
    pr = packet['pr']; metadata = dict(headRefOid=pr.head_sha, baseRefOid=packet['baseline'])
    observations = []
    def gh(args, **kwargs):
        if args[:2] != ['api', 'graphql']: return json.dumps(metadata)
        observations.append(args)
        result = contract_observation(metadata, pr.base_sha)
        if len(observations) > 1:
            repository = result['data']['repository']; view = repository['pullRequest']
            if change == 'head': view['headRefOid'] = 'f' * 40
            elif change == 'saved-base': view['baseRefOid'] = 'f' * 40
            else:
                repository['ref']['target']['oid'] = view['baseRef']['target']['oid'] = 'f' * 40
        return json.dumps(result)
    monkeypatch.setattr(github_client, '_run_gh', gh)
    monkeypatch.setattr(github_client, 'fetch_contract_packet', lambda *args: (
        pr.diff, [{'path': name, 'status': 'modified'} for name in pr.changed_files], pr.test_contract_proof))
    with pytest.raises(github_client.GitHubError, match='head/base changed'):
        github_client.fetch_pr_info(pr.owner, pr.repo, pr.number)


def test_final_sha_check_rejects_relabelled_runtime_and_live_base(packet, monkeypatch):
    pr = packet['pr']; live = [pr.base_sha]
    metadata = dict(headRefOid=pr.head_sha, baseRefOid=packet['baseline'])
    def gh(args, **kwargs):
        return json.dumps(contract_observation(metadata, live[0])
                          if args[:2] == ['api', 'graphql'] else metadata)
    monkeypatch.setattr(github_client, '_run_gh', gh)
    assert github_client.verify_sha_current(pr.owner, pr.repo, pr.number, pr.head_sha)
    # A change outside Karen may leave its hashes identical. A newer reported
    # head still cannot replace the base captured by this process at startup.
    live[0] = 'f' * 40
    packet['runtime'].update(installed_head=live[0], loaded_head=live[0], source_matches=True)
    assert not github_client.verify_sha_current(pr.owner, pr.repo, pr.number, pr.head_sha)


def test_unrelated_sha_checks_do_not_request_contract_refs(monkeypatch):
    monkeypatch.setattr(github_client, '_run_gh', lambda *a, **k: json.dumps({'headRefOid': 'a' * 40}))
    refs = Mock(side_effect=AssertionError('Unrelated SHA check requested special policy refs'))
    monkeypatch.setattr(github_client, '_contract_refs', refs)
    assert github_client.verify_sha_current('other-owner', 'other-repo', 7, 'a' * 40)
    refs.assert_not_called()


def test_live_ref_observation_charges_graphql_budget(monkeypatch):
    calls = []
    def request(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, '{}', '')
    monkeypatch.setattr(github_client, 'budgeted_gh', request)
    assert github_client._run_gh(['api', 'graphql', '-f', 'query={}']) == '{}'
    assert calls[0][1]['resource'] == 'graphql'
    assert calls[0][1]['timeout'] == 30


@pytest.mark.parametrize('field,value', [('source_matches', False), ('source_matches', 1),
                                       ('installed_head', 'a' * 40), ('loaded_head', 'b' * 40)])
def test_stale_overlay_or_unmatched_installed_policy_is_not_authority(packet, field, value):
    packet['runtime'][field] = value
    with pytest.raises(p.ContractUnavailable):
        p._prove(packet['base'], packet['git'])


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


@pytest.mark.parametrize('loaded', [None, 'f' * 40])
def test_missing_or_other_startup_base_is_not_recovered_from_runtime(packet, monkeypatch, loaded):
    monkeypatch.setattr(p, '_LOADED_BASE', loaded)
    assert not p.runtime_base_matches(packet['base'])
    with pytest.raises(p.ContractUnavailable): p._prove(packet['base'], packet['git'])


@pytest.mark.parametrize('failure', [OSError, TypeError, ValueError, subprocess.SubprocessError])
def test_recovered_telemetry_cannot_authorize_a_failed_startup(packet, monkeypatch, failure):
    def unavailable(): raise failure('startup telemetry unavailable')
    monkeypatch.setattr(p, 'deployment_status', unavailable)
    monkeypatch.setattr(p, '_LOADED_BASE', p._UNSET)
    p.bind_startup_base()
    monkeypatch.setattr(p, 'deployment_status', lambda: dict(packet['runtime']))
    p.bind_startup_base()
    assert not p.runtime_base_matches(packet['base'])
    with pytest.raises(p.ContractUnavailable): p._prove(packet['base'], packet['git'])


def test_review_client_startup_binding_cannot_be_replaced(packet, monkeypatch):
    monkeypatch.setattr(p, '_LOADED_BASE', p._UNSET)
    observed = Mock(side_effect=[packet['base'], 'f' * 40])
    monkeypatch.setattr(p, '_startup_base', observed)
    p.bind_startup_base()
    p.bind_startup_base()
    assert p.runtime_base_matches(packet['base'])
    observed.assert_called_once_with()
