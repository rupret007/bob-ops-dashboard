import json
import subprocess
from unittest.mock import Mock
from cluster.control_plane.local_karen import github_client

def test_staged_unrelated_sha_checks_do_not_request_contract_refs(monkeypatch):
    monkeypatch.setattr(github_client, '_run_gh', lambda *a, **k: json.dumps({'headRefOid': 'a' * 40}))
    refs = Mock(side_effect=AssertionError('Unrelated SHA check requested special policy refs'))
    monkeypatch.setattr(github_client, '_contract_refs', refs)
    assert github_client.verify_sha_current('other-owner', 'other-repo', 7, 'a' * 40)
    refs.assert_not_called()

def test_staged_live_ref_observation_charges_graphql_budget(monkeypatch):
    calls = []

    def request(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, '{}', '')
    monkeypatch.setattr(github_client, 'budgeted_gh', request)
    assert github_client._run_gh(['api', 'graphql', '-f', 'query={}']) == '{}'
    assert calls[0][1]['resource'] == 'graphql'
    assert calls[0][1]['timeout'] == 30
