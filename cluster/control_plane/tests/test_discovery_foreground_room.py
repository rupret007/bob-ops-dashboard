"""Discovery must yield before it consumes the foreground review allowance."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cluster.control_plane import github_budget as budget, github_reads as reads
from cluster.control_plane.local_karen import github_client, review_queue


@pytest.fixture
def full_background(tmp_path, monkeypatch):
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(tmp_path))
    state = dict(version=1, requests=[1000] * 24, resources={})

    def reserve(resource, *, background=False):
        budget._mutate(state, 'reserve', resource, {}, False, 1000, background=background)

    monkeypatch.setattr(budget, 'reserve', reserve)
    monkeypatch.setattr(budget, 'observe', Mock())
    cli = Mock(return_value=SimpleNamespace(returncode=0, stdout='{}', stderr=''))
    monkeypatch.setattr(reads.subprocess, 'run', cli)
    return state, cli


@pytest.mark.parametrize('operation', [
    lambda: reads.rest_json('repos/owner/repo/labels'),
    lambda: reads.active_repos('owner'),
    lambda: reads.labels('owner/repo'),
])
def test_each_cached_discovery_path_yields_before_cli(full_background, operation):
    state, cli = full_background
    with pytest.raises(budget.GitHubDeferred) as error:
        operation()
    assert error.value.code == 'background-exhausted'
    assert len(state['requests']) == 24 and state['foreground_requests'] == []
    cli.assert_not_called()


def test_bounded_admission_metadata_keeps_foreground_access(full_background):
    state, cli = full_background
    candidate = review_queue.Candidate('owner/repo', dict(number=1, headRefOid='a' * 40))
    review_queue.enrich([candidate], max_batches=1)
    assert candidate.lines is None and candidate.ci == 'unknown'
    assert len(state['requests']) == 25 and state['foreground_requests'] == [1000]
    cli.assert_called_once()


@pytest.mark.parametrize('operation', [
    reads.refresh_quota,
    lambda: github_client.list_prs_with_label('owner', 'repo', 'needs-karen'),
])
def test_quota_and_review_listing_keep_reserved_foreground_room(full_background, operation):
    state, cli = full_background
    cli.return_value.stdout = json.dumps([] if operation.__name__ == '<lambda>' else {})
    operation()
    assert len(state['requests']) == 25 and state['foreground_requests'] == [1000]
    cli.assert_called_once()


def test_cached_discovery_hit_does_not_need_a_new_reservation(full_background):
    state, cli = full_background
    state['requests'].clear()
    cli.return_value.stdout = json.dumps([dict(name='needs-karen')])
    expected = reads.labels('owner/repo')
    assert len(state['requests']) == 1 and state['foreground_requests'] == []
    state['requests'] = [1000] * 24
    assert reads.labels('owner/repo') == expected
    assert len(state['requests']) == 24 and state['foreground_requests'] == []
    cli.assert_called_once()


def test_foreground_exact_head_reads_keep_room_but_never_exceed_forty(full_background):
    state, cli = full_background
    for _ in range(16):
        reads.run(['api', 'repos/owner/repo/git/commits/' + 'a' * 40])
    assert len(state['requests']) == 40 and len(state['foreground_requests']) == 16
    with pytest.raises(budget.GitHubDeferred) as error:
        reads.run(['pr', 'view', '1', '--repo', 'owner/repo'])
    assert error.value.code == 'minute-exhausted'
    assert cli.call_count == 16 and len(state['requests']) == 40


@pytest.mark.parametrize('background', [False, True])
def test_priority_does_not_bypass_account_quota_reserve(full_background, background):
    state, cli = full_background
    state['resources']['core'] = dict(remaining=100, reset=2000)
    with pytest.raises(budget.GitHubDeferred, match='quota reserve'):
        if background:
            reads.labels('owner/repo')
        else:
            reads.run(['api', 'repos/owner/repo/git/commits/' + 'a' * 40])
    assert len(state['requests']) == 24
    cli.assert_not_called()


@pytest.mark.parametrize('bad', [None, 0, 1, {}, 'foreground'])
def test_cached_priority_requires_an_actual_boolean_before_any_admission(full_background, bad):
    state, cli = full_background
    with pytest.raises(budget.GitHubDeferred, match='priority invalid'):
        reads.rest_json('repos/owner/repo/labels', background=bad)
    assert len(state['requests']) == 24
    cli.assert_not_called()
