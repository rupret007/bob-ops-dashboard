"""Network admission, conditional caching and warning classification regressions."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane import github_reads as reads, backlog, watchdog


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(reads.budget, 'reserve', Mock())
    monkeypatch.setattr(reads.budget, 'observe', Mock())
    run = Mock()
    monkeypatch.setattr(reads.subprocess, 'run', run)
    return run


def response(value, status=200, etag='"one"', code=0):
    return SimpleNamespace(returncode=code, stderr='', stdout='HTTP/2 '+str(status)+' OK\nETag: '+etag+'\nx-ratelimit-remaining: 4000\n\n'+(json.dumps(value) if value is not None else ''))


def test_watchdog_rate_limit_is_deferred_warning_not_execution_failure(api):
    api.return_value = SimpleNamespace(returncode=1, stdout='', stderr='API rate limit already exceeded')
    result = watchdog.check_labels_exist('owner/repo')
    assert result.passed is True
    assert result.execution_error is False
    assert result.can_repair is False
    assert 'deferred' in result.message.lower()
    reads.budget.observe.assert_called()


def test_conditional_cache_304_and_fresh_hit(api, monkeypatch):
    api.side_effect = [response([{'name': 'needs-karen'}]), response(None, status=304)]
    first = reads.rest_json('repos/owner/repo/labels', ttl=60)
    assert reads.rest_json('repos/owner/repo/labels', ttl=60) == first
    assert api.call_count == 1
    assert reads.rest_json('repos/owner/repo/labels', ttl=0) == first
    assert api.call_count == 2
    assert 'If-None-Match: "one"' in api.call_args.args[0]
    assert reads.budget.reserve.call_count == 2


def test_rate_limit_never_serves_stale_data_or_authorizes_work(api):
    api.side_effect = [response([]), SimpleNamespace(returncode=1,stdout='',stderr='secondary rate limit')]
    reads.rest_json('repos/owner/repo/labels')
    with pytest.raises(reads.budget.GitHubDeferred):
        reads.rest_json('repos/owner/repo/labels', ttl=0)
    assert reads.budget.observe.call_args.kwargs['limited'] is True


def test_discovery_only_active_repos_and_deduplicates(api):
    api.return_value = response({'total_count': 3, 'items': [
        {'repository_url': 'https://api.github.com/repos/owner/active'},
        {'repository_url': 'https://api.github.com/repos/owner/active'},
        {'repository_url': 'https://api.github.com/repos/owner/other'}]})
    assert reads.active_repos('owner') == [{'nameWithOwner':'owner/active'}, {'nameWithOwner':'owner/other'}]
    assert api.call_count == 1


def test_real_missing_labels_still_fail_and_offer_repair(api):
    api.return_value = response([{'name':'unrelated'}])
    result = watchdog.check_labels_exist('owner/repo')
    assert result.passed is False
    assert result.can_repair is True
    assert result.repair_action == 'create_labels'


def test_auth_failure_remains_execution_error(api):
    api.return_value = SimpleNamespace(returncode=1, stdout='', stderr='authentication failed')
    result = watchdog.check_labels_exist('owner/repo')
    assert result.passed is False
    assert result.execution_error is True


def test_budget_outage_does_not_call_github(api):
    reads.budget.reserve.side_effect = reads.budget.GitHubDeferred('coordination unavailable')
    with pytest.raises(reads.budget.GitHubDeferred):
        reads.rest_json('repos/owner/repo/labels')
    api.assert_not_called()


def test_karen_poll_uses_rest_and_cached_discovery_never_replaces_exact_head_read(api):
    from cluster.control_plane.local_karen import github_client
    api.return_value = response([{'number':8, 'head':{'sha':'a'*40}, 'labels':[{'name':'needs-karen'}], 'draft':False}])
    assert github_client.list_prs_with_label('owner','repo','needs-karen')[0]['headRefOid'] == 'a'*40
    assert 'api' in api.call_args.args[0]
    assert 'pulls?state=open' in api.call_args.args[0][5]
    api.return_value = SimpleNamespace(returncode=0,stdout=json.dumps({'headRefOid':'b'*40}),stderr='')
    assert github_client.verify_sha_current('owner','repo',8,'a'*40) is False
    assert api.call_args.args[0][1:3] == ['pr','view']


def test_dashboard_exposes_safe_numeric_quota_without_network(api, tmp_path, monkeypatch):
    from cluster.control_plane.dashboard.tachometer import EngineCollector
    # Host telemetry is unrelated to the API-only mock; never run a subprocess.
    monkeypatch.setattr('cluster.control_plane.reporting_telemetry.local_resources',
                        lambda *args: {'machine': 'nucbox', 'fresh': False, 'passed': False})
    monkeypatch.setattr('cluster.control_plane.dashboard.tachometer.read_codex_quota',
                        lambda **kwargs: {'status': 'unknown', 'weekly_pct': None})
    (tmp_path/'github-api-budget.json').write_text(json.dumps({'status':'ok','updated_at':1000,
        'resources':{'core':{'remaining':4200,'limit':5000,'reset':9999999999},
                     'graphql':{'remaining':3100,'limit':5000,'reset':9999999999}},
        'password':'do not expose'}))
    value=EngineCollector(tmp_path,tmp_path/'verdicts','nucbox').collect()['github_api']
    assert value['resources']['graphql']['remaining']==3100
    assert 'password' not in json.dumps(value)
    api.assert_not_called()


@pytest.mark.parametrize('args,resources', [
    (['label','create','needs-karen'], ['core']),
    (['pr','diff','8'], ['core','graphql']),
    (['pr','view','8'], ['graphql']),
    (['pr','edit','8'], ['core','graphql']),
])
def test_cli_accounts_for_rest_graphql_and_mixed_contracts(api,args,resources):
    api.return_value=SimpleNamespace(returncode=0,stdout='',stderr='')
    reads.run(args)
    assert [call.args[0] for call in reads.budget.reserve.call_args_list]==resources


def test_incomplete_discovery_never_looks_like_empty_backlog(api):
    api.return_value=response({'incomplete_results':True,'items':[],'total_count':0})
    with pytest.raises(reads.GitHubReadError):
        reads.active_repos('owner')


def test_rate_limited_poll_is_retry_without_review_or_fail_label(api):
    from cluster.control_plane.local_karen import poller
    from cluster.control_plane.local_karen.config import KarenConfig
    api.return_value=SimpleNamespace(returncode=1,stdout='',stderr='API rate limit already exceeded')
    with patch.object(poller,'run_gate') as gate:
        with pytest.raises(reads.budget.GitHubDeferred):
            poller.poll_and_run(['owner/repo'],KarenConfig(),guard_machine=None)
    gate.assert_not_called()
