"""Background observations leave room in the same durable forty-read budget."""
import asyncio
from collections import Counter
import concurrent.futures
import json
import subprocess
import threading
import time
from unittest.mock import Mock

import pytest

from cluster.control_plane import github_budget as budget, github_reads as reads, main_ci
from cluster.control_plane.tests.test_github_budget import server, _stored


def reserve(state, *, now=1000, background=False, resource='core'):
    budget._mutate(state, 'reserve', resource, {}, False, now, background=background)


def test_two_61_pr_observers_leave_five_foreground_reads_under_shared_cas(server):
    barrier = threading.Barrier(2)

    def observer(_):
        barrier.wait()
        admitted = 0
        for _ in range(61):
            for resource in ('graphql', 'core', 'core'):
                try:
                    budget.reserve(resource, background=True)
                    admitted += 1
                except budget.GitHubDeferred as exc:
                    assert exc.code == 'background-exhausted'
                    assert 0 <= exc.retry_after <= 60
                    return admitted
        return admitted

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        admitted = list(pool.map(observer, range(2)))
    assert sum(admitted) == budget.BACKGROUND_MINUTE_LIMIT == 24
    for _ in range(5):
        budget.reserve('graphql')
    state = asyncio.run(_stored(server))
    assert len(state['requests']) == 29
    assert len(state['foreground_requests']) == 5
    assert len(state['requests']) - len(state['foreground_requests']) == 24
    for _ in range(11):
        budget.reserve('core')
    with pytest.raises(budget.GitHubDeferred) as error:
        budget.reserve('graphql')
    assert error.value.code == 'minute-exhausted'
    assert len(asyncio.run(_stored(server))['requests']) == budget.MINUTE_LIMIT == 40


def test_legacy_v1_reservations_conservatively_consume_background_allowance():
    state = budget._state(dict(version=1, requests=[1000] * 24, resources={}))
    with pytest.raises(budget.GitHubDeferred) as error:
        reserve(state, background=True)
    assert error.value.code == 'background-exhausted' and error.value.retry_after == 60
    assert len(state['requests']) == 24  # No reset or failed-request reservation.
    for _ in range(5):
        reserve(state)
    assert state['foreground_requests'] == [1000] * 5
    assert len(state['requests']) == 29


def test_rolling_legacy_writer_can_expire_total_but_leave_old_auxiliary_timestamps():
    state = dict(version=1, requests=[], resources={})
    reserve(state, now=1000)
    reserve(state, now=1020)
    reserve(state, now=1021, background=True)
    # Existing v1 writer behavior: preserve unknown fields, prune total ledger,
    # then append an unclassified reservation without changing the new field.
    state['requests'] = [t for t in state['requests'] if t > 1061 - 60]
    state['requests'].append(1061)
    assert state['foreground_requests'] == [1000, 1020]
    reserve(budget._state(state), now=1061, background=True)
    assert state['foreground_requests'] == [1020]
    assert Counter(state['requests']) == Counter([1020, 1021, 1061, 1061])
    assert budget._public(state, 1061)['background_requests_last_minute'] == 3


def test_fixed_clock_duplicates_use_multiset_and_never_hide_unclassified_reads():
    state = dict(version=1, requests=[1000] * 26, foreground_requests=[1000] * 2, resources={})
    with pytest.raises(budget.GitHubDeferred) as error:
        reserve(state, background=True)
    assert error.value.code == 'background-exhausted'
    reserve(state)
    assert len(state['foreground_requests']) == 3
    assert budget._public(state, 1000)['background_requests_last_minute'] == 24


@pytest.mark.parametrize('foreground', [[1000] * 3, [999], [1001]])
def test_active_unmatched_auxiliary_entries_fail_closed(foreground):
    state = dict(version=1, requests=[1000] * 2, foreground_requests=foreground, resources={})
    with pytest.raises(budget.GitHubDeferred, match='invalid'):
        reserve(budget._state(state), background=True)
    assert len(state['requests']) == 2


@pytest.mark.parametrize('value', [None, {}, 'bad', [True], ['bad'], [float('nan')],
                                 [float('inf')], [-1], [1000] * 41])
def test_malformed_optional_ledger_never_defaults_to_empty(value):
    with pytest.raises(budget.GitHubDeferred, match='invalid'):
        budget._state(dict(version=1, requests=[1000], foreground_requests=value, resources={}))


@pytest.mark.parametrize('bad', ['bad-type', 'unmatched-active'])
def test_corrupt_shared_priority_ledger_is_never_repaired_by_reset(server, bad):
    foreground = ['bad'] if bad == 'bad-type' else [time.time()]
    original = dict(version=1, requests=[], foreground_requests=foreground, resources={})
    asyncio.run(_stored(server, original))
    with pytest.raises(budget.GitHubDeferred, match='invalid'):
        budget.reserve(background=True)
    assert asyncio.run(_stored(server)) == original


def test_observations_do_not_create_foreground_reservations_or_increase_quota():
    state = dict(version=1, requests=[1000] * 24, resources={})
    budget._mutate(state, 'observe', 'core', {'x-ratelimit-remaining': '100',
                    'x-ratelimit-reset': '2000'}, False, 1000)
    assert state['foreground_requests'] == []
    for background in (True, False):
        with pytest.raises(budget.GitHubDeferred, match='quota reserve'):
            reserve(state, background=background)


@pytest.mark.parametrize('value', [None, 0, 1, 'background'])
def test_invalid_request_priority_defers_before_broker_or_cli(monkeypatch, value):
    operation, cli = Mock(), Mock()
    monkeypatch.setattr(budget, '_operation', operation)
    monkeypatch.setattr(reads.subprocess, 'run', cli)
    with pytest.raises(budget.GitHubDeferred, match='priority invalid'):
        budget.reserve(background=value)
    with pytest.raises(budget.GitHubDeferred, match='priority invalid'):
        reads.run(['pr', 'view', '1'], background=value)
    operation.assert_not_called()
    cli.assert_not_called()


def test_background_priority_applies_to_every_resource_of_a_mixed_cli_command(monkeypatch):
    reservation, cli = Mock(), Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
    monkeypatch.setattr(reads.budget, 'reserve', reservation)
    monkeypatch.setattr(reads.subprocess, 'run', cli)
    reads.run(['pr', 'diff', '1'], background=True)
    assert [(c.args, c.kwargs) for c in reservation.call_args_list] == [
        (('core',), dict(background=True)), (('graphql',), dict(background=True))]


@pytest.mark.parametrize('args,background', [
    (['api', 'repos/owner/repo/commits/main/status'], True),
    (['api', '--paginate', '--slurp', 'repos/owner/repo/commits/main/check-runs?per_page=100'], True),
    (['search', 'prs', '--owner', 'owner'], True),
    (['pr', 'view', '1', '--repo', 'owner/repo'], True),
    (['pr', 'list', '--repo', 'owner/repo'], True),
    (['pr', 'create', '--repo', 'owner/repo'], False),
    (['api', '--method', 'POST', 'repos/owner/repo/issues'], False),
    (['api', '-XPOST', 'repos/owner/repo/issues'], False),
    (['api', 'repos/owner/repo/issues', '-f', 'title=fixture'], False),
])
@pytest.mark.parametrize('scheduled', [False, True])
def test_only_scheduled_main_ci_reads_receive_lower_priority(monkeypatch, args, background, scheduled):
    command = Mock(return_value=subprocess.CompletedProcess([], 0, '{}', ''))
    monkeypatch.setattr(main_ci.github_reads, 'run', command)
    token = main_ci._deadline.set(main_ci.time.monotonic()+120 if scheduled else None)
    try:
        assert main_ci.command(['gh'] + args) == '{}'
    finally:
        main_ci._deadline.reset(token)
    assert command.call_args.kwargs['background'] is (background and scheduled)


def test_new_deferral_is_typed_bounded_and_background_batch_does_not_wait_or_claim_green():
    error = budget.GitHubDeferred('fixed message', code='background-exhausted', retry_after=60)
    assert error.code == 'background-exhausted' and error.retry_after == 60
    assert not main_ci._wait_for_minute(error.code, error.retry_after)
    assert budget.GitHubDeferred('fixed message', code='private-value').code == 'other'


def test_snapshot_exposes_only_numeric_priority_counts_with_legacy_fallback(tmp_path):
    data = dict(status='ok', updated_at=1000, resources={}, requests_last_minute=29,
                background_requests_last_minute=24, foreground_requests=['private-value'])
    path = tmp_path / budget.SNAPSHOT
    path.write_text(json.dumps(data))
    result = budget.snapshot(tmp_path)
    assert result['background_minute_limit'] == 24 and result['minute_limit'] == 40
    assert result['background_requests_last_minute'] == 24
    assert 'private-value' not in json.dumps(result)
    del data['background_requests_last_minute']
    path.write_text(json.dumps(data))
    assert budget.snapshot(tmp_path)['background_requests_last_minute'] == 29
