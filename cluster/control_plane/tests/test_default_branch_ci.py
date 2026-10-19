"""Implicit CI follows one default-branch commit; explicit refs keep their API."""
import json
import subprocess
from unittest.mock import Mock, call, patch

import pytest

from cluster.control_plane import conductor, github_budget, main_ci as m


REPO = 'owner/project'
SHA = 'a' * 40
OTHER = 'b' * 40


def default_head(branch='master', sha=SHA):
    return {'data': {'repository': {'nameWithOwner': REPO, 'defaultBranchRef': {
        'name': branch, 'target': {'oid': sha}}}}}


def checks(conclusion='success'):
    return [{'check_runs': [{'id': 1, 'name': 'test', 'app': {'id': 1},
                            'status': 'completed', 'conclusion': conclusion}]}]


@pytest.mark.parametrize('branch,default_state,main_state,expected', [
    ('master', 'success', 'failure', (True, 'success')),
    ('master', 'failure', 'success', (False, 'failure')),
    ('main', 'success', 'failure', (True, 'success')),
])
def test_conductor_uses_actual_default_commit_not_literal_main(
        tmp_path, branch, default_state, main_state, expected):
    def response(*args):
        if args[:2] == ('api', 'graphql'):
            return default_head(branch)
        endpoint = args[-1]
        if endpoint == f'repos/{REPO}/commits/{SHA}/check-runs?per_page=100':
            return checks(default_state)
        if endpoint == f'repos/{REPO}/commits/{SHA}/status':
            return {'statuses': []}
        if '/commits/main/' in endpoint:
            return checks(main_state) if 'check-runs' in endpoint else {'statuses': []}
        raise AssertionError('Unexpected unpinned CI request')

    runner = conductor.Conductor(log_dir=tmp_path, budget_tracker=Mock(), metrics_tracker=Mock())
    with patch.object(m, 'gh_json', side_effect=response) as api:
        assert runner._check_main_ci_after_merge(REPO) == expected
    assert api.call_count == 3
    assert api.call_args_list[-2:] == [
        call('api', '--paginate', '--slurp', f'repos/{REPO}/commits/{SHA}/check-runs?per_page=100'),
        call('api', f'repos/{REPO}/commits/{SHA}/status'),
    ]


def test_default_and_branch_can_move_but_both_ci_reads_keep_snapshot_sha():
    state = {'branch': 'master', 'sha': SHA}

    def response(*args):
        if args[:2] == ('api', 'graphql'):
            result = default_head(state['branch'], state['sha'])
            # An asynchronous default-branch switch/head advance after the
            # single metadata response cannot split checks and statuses.
            state.update(branch='main', sha=OTHER)
            return result
        assert f'/commits/{SHA}/' in args[-1]
        state['sha'] = 'c' * 40
        return checks() if 'check-runs' in args[-1] else {'statuses': [{}], 'state': 'failure'}

    with patch.object(m, 'gh_json', side_effect=response) as api:
        assert m.check_ci(REPO) == (False, 'failure')
    assert api.call_count == 3


@pytest.mark.parametrize('response', [
    None, [], {}, {'data': {}}, {'data': {'repository': None}},
    {'data': {'repository': {'nameWithOwner': REPO, 'defaultBranchRef': None}}},
    {'data': {'repository': {'nameWithOwner': REPO, 'defaultBranchRef': {'name': '', 'target': {'oid': SHA}}}}},
    {'data': {'repository': {'nameWithOwner': REPO, 'defaultBranchRef': {'name': 5, 'target': {'oid': SHA}}}}},
    {'data': {'repository': {'nameWithOwner': REPO, 'defaultBranchRef': {'name': 'master', 'target': None}}}},
    default_head(sha='a' * 39), default_head(sha='a' * 41), default_head(sha='g' * 40),
    default_head(sha=True), default_head(sha=None),
    {'data': {'repository': {'nameWithOwner': 'other/project', 'defaultBranchRef': {
        'name': 'master', 'target': {'oid': SHA}}}}},
    {'data': {'repository': {'nameWithOwner': None, 'defaultBranchRef': {
        'name': 'master', 'target': {'oid': SHA}}}}},
    dict(default_head(), errors=[{'type': 'UNAVAILABLE'}]),
])
def test_invalid_default_metadata_never_reads_ci_or_claims_green(response):
    with patch.object(m, 'gh_json', return_value=response) as api:
        assert m.check_ci(REPO) == (False, 'unknown')
    api.assert_called_once()


@pytest.mark.parametrize('repo', ['', 'owner', '../owner/project', 'owner/project/extra', None])
def test_invalid_implicit_repo_does_not_read_api(repo):
    with patch.object(m, 'gh_json') as api:
        assert m.check_ci(repo) == (False, 'unknown')
    api.assert_not_called()


@pytest.mark.parametrize('ref', [SHA, 'main', 'release/stable', ''])
def test_explicit_refs_preserve_original_two_requests(ref):
    with patch.object(m, 'gh_json', side_effect=[checks(), {'statuses': []}]) as api:
        assert m.check_ci(REPO, ref) == (True, 'success')
    assert api.call_args_list == [
        call('api', '--paginate', '--slurp', f'repos/{REPO}/commits/{ref}/check-runs?per_page=100'),
        call('api', f'repos/{REPO}/commits/{ref}/status'),
    ]


@pytest.mark.parametrize('scheduled', [False, True])
def test_default_resolution_uses_existing_shared_graphql_admission(scheduled):
    responses = [default_head(), checks(), {'statuses': []}]
    token = m._deadline.set(220 if scheduled else None)
    with patch.object(m.time, 'monotonic', return_value=100), \
         patch.object(m.github_reads, 'run', side_effect=[
             subprocess.CompletedProcess([], 0, json.dumps(value)) for value in responses]) as api, \
         patch.object(m.subprocess, 'run') as raw:
        try:
            assert m.check_ci(REPO) == (True, 'success')
        finally:
            m._deadline.reset(token)
    first = api.call_args_list[0]
    assert first.args[0][:2] == ['api', 'graphql']
    assert first.kwargs == dict(timeout=120, resource='graphql',
                                deadline=220 if scheduled else None, background=scheduled)
    assert len(api.call_args_list) == 3
    raw.assert_not_called()


@pytest.mark.parametrize('scheduled', [False, True])
@pytest.mark.parametrize('stage', range(3))
def test_shared_budget_defers_at_any_read_without_fake_ci(scheduled, stage):
    responses = [default_head(), checks(), {'statuses': []}]
    deferred = github_budget.GitHubDeferred('Shared API budget unavailable; requests paused')
    token = m._deadline.set(220 if scheduled else None)
    try:
        with patch.object(m, 'gh_json', side_effect=responses[:stage] + [deferred]) as api:
            if scheduled:
                with pytest.raises(github_budget.GitHubDeferred) as raised:
                    m.check_ci(REPO)
                assert raised.value is deferred
            else:
                assert m.check_ci(REPO) == (False, 'deferred')
            assert api.call_count == stage + 1
    finally:
        m._deadline.reset(token)
