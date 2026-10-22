"""Whole-queue scheduling improves throughput without changing review authority."""
import json
import stat
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.github_budget import GitHubDeferred
from cluster.control_plane.local_karen import poller, review_queue as queue
from cluster.control_plane.local_karen.config import KarenConfig


def candidate(repo='owner/repo', number=1, lines=None, ci='unknown', sha=None):
    return queue.Candidate(repo, {'number': number, 'headRefOid': sha or f'{number:040x}',
                                 'isDraft': False}, lines=lines, ci=ci)


def metadata(values):
    return SimpleNamespace(returncode=0, stdout=json.dumps({'data': {
        'r%d' % index: {'pullRequest': {
            'headRefOid': item.pr['headRefOid'], 'additions': 40, 'deletions': 10,
            'commits': {'nodes': [{'commit': {'statusCheckRollup': {'state': 'SUCCESS'}}}]}}}
        for index, item in enumerate(values)}}))


def test_metadata_uses_bounded_graphql_batches_and_explicit_budget_resource():
    candidates = [candidate(number=n) for n in range(1, 61)]
    batches = [candidates[start:start + queue.METADATA_BATCH]
               for start in range(0, 60, queue.METADATA_BATCH)]
    with patch.object(queue.github_reads, 'run', side_effect=[metadata(batch) for batch in batches]) as gh:
        queue.enrich(candidates)
    assert gh.call_count == 3
    assert all(call.kwargs['resource'] == 'graphql' and call.kwargs['timeout'] <= 10
               for call in gh.call_args_list)
    assert all(call.args[0][:2] == ['api', 'graphql'] for call in gh.call_args_list)
    assert all(item.lines == 50 and item.ci == 'green' for item in candidates)
    query = gh.call_args.args[0][-1]
    assert query.count('{') == query.count('}')


@pytest.mark.parametrize('response', [SimpleNamespace(returncode=1, stdout=''),
    SimpleNamespace(returncode=0, stdout='not json'),
    SimpleNamespace(returncode=0, stdout='{"data":{}}')])
def test_unavailable_metadata_is_unknown_not_green(response):
    item = candidate()
    with patch.object(queue.github_reads, 'run', return_value=response):
        queue.enrich([item])
    assert item.ci == 'unknown' and item.lines is None


def test_stale_metadata_head_cannot_boost_scheduling_priority():
    item = candidate()
    response = metadata([item])
    response.stdout = response.stdout.replace(item.pr['headRefOid'], 'f' * 40)
    with patch.object(queue.github_reads, 'run', return_value=response):
        queue.enrich([item])
    assert item.ci == 'unknown' and item.lines is None


def test_metadata_elapsed_budget_stops_additional_reads():
    candidates = [candidate(number=n) for n in range(1, 61)]
    with patch.object(queue.time, 'monotonic', side_effect=[0, 1, 31]), \
         patch.object(queue.github_reads, 'run', return_value=metadata(candidates[:25])) as gh:
        queue.enrich(candidates)
    assert gh.call_count == 1
    assert candidates[-1].ci == 'unknown'


def test_small_green_rank_crosses_repo_order_and_does_not_treat_unknown_as_green(tmp_path):
    state = queue.ReviewQueue(KarenConfig(state_dir=str(tmp_path)))
    huge = candidate('first/repo', lines=10000, ci='green')
    tiny_unknown = candidate('second/repo', lines=1)
    small = candidate('last/repo', lines=120, ci='green')
    red = candidate('other/repo', lines=5, ci='red')
    assert state.order([huge, tiny_unknown, red, small]) == [small, huge, tiny_unknown, red]


def test_large_deferred_twice_gets_first_admission_and_state_is_private(tmp_path):
    config = KarenConfig(state_dir=str(tmp_path))
    large = candidate('last/repo', lines=10000, ci='green')
    small = [candidate('first/repo', number=n, lines=100, ci='green') for n in range(1, 5)]
    for cycle in range(3):
        state = queue.ReviewQueue(config)
        ordered = state.order(small + [large])
        if cycle < 2:
            assert large not in ordered[:3]
        else:
            assert ordered[0] is large
        state.begin(small + [large])
        for item in ordered[:3]:
            state.attempted(item)
    assert stat.S_IMODE(state.path.stat().st_mode) == 0o600
    # A changed head starts fresh and cannot inherit its predecessor's state.
    changed = candidate('last/repo', lines=10000, ci='green', sha='b' * 40)
    assert queue.ReviewQueue(config).order(small + [changed])[0] is not changed


def test_unknown_sized_work_also_cannot_starve_forever(tmp_path):
    state = queue.ReviewQueue(KarenConfig(state_dir=str(tmp_path)))
    unknown = candidate('last/repo')
    fast = candidate('first/repo', lines=20, ci='green')
    state.deferred[unknown.key] = 3
    assert state.order([fast, unknown])[0] is unknown


def run_cycle(tmp_path, candidates, *, gate=None, enrich_error=None, clock=None):
    config = KarenConfig(state_dir=str(tmp_path))
    mapping = {}
    for item in candidates:
        mapping.setdefault(item.repo, []).append(item.pr)

    def enrich(items):
        if enrich_error:
            raise enrich_error
        for item in items:
            source = next(value for value in candidates if value.key == item.key)
            item.lines, item.ci = source.lines, source.ci

    gate = gate or Mock(side_effect=RuntimeError('provider unavailable'))
    with patch.object(poller, 'list_prs_with_label',
                      side_effect=lambda owner, repo, label: mapping[owner + '/' + repo]), \
         patch.object(poller, 'enrich', side_effect=enrich), \
         patch.object(poller, 'run_gate', gate), \
         patch.object(poller.time, 'monotonic', side_effect=clock) as now, \
         patch('cluster.control_plane.node_control.is_draining', return_value=False), \
         patch.object(poller, 'add_label') as add, patch.object(poller, 'remove_label') as remove:
        if clock is None:
            now.side_effect = None
            now.return_value = 0
        results = poller.poll_and_run(list(mapping), config, post_comment=False)
    return gate, results, add, remove


def test_poller_collects_whole_queue_then_admits_at_most_three_exact_heads(tmp_path):
    large = candidate('first/repo', lines=10000, ci='green')
    small = [candidate('last/repo', number=n, lines=100, ci='green') for n in range(1, 5)]
    gate, results, add, remove = run_cycle(tmp_path, [large] + small)
    assert gate.call_count == 3
    assert all(call.kwargs['repo'] == 'repo' and call.kwargs['owner'] == 'last'
               and call.kwargs['expected_sha'] == f'{n:040x}'
               for n, call in enumerate(gate.call_args_list, 1))
    assert len(results) == 3 and all('error' in result for result in results)
    add.assert_not_called()
    remove.assert_not_called()


def test_twenty_minute_admission_window_does_not_start_another_review(tmp_path):
    items = [candidate(number=n) for n in range(1, 5)]
    gate, results, add, remove = run_cycle(tmp_path, items, clock=[0, 1, 1201])
    assert gate.call_count == 1 and len(results) == 1


def test_shared_api_budget_deferral_starts_no_model_work(tmp_path):
    gate = Mock()
    with pytest.raises(GitHubDeferred):
        run_cycle(tmp_path, [candidate()], gate=gate,
            enrich_error=GitHubDeferred('shared API budget unavailable'))
    gate.assert_not_called()
