"""Real JetStream races and fail-closed account quota/backoff checks."""
import asyncio
import concurrent.futures
import json
import threading
import time
from unittest.mock import patch

import pytest

from cluster.control_plane import github_budget as budget
from cluster.control_plane.coordination import ClaimClient
from cluster.coordination_buckets import bucket_name
from cluster.tests.nats_test_util import EphemeralNats


@pytest.fixture
def server(tmp_path, monkeypatch):
    server = EphemeralNats()
    assert server.bin, 'nats-server is required for shared budget regression tests'
    server.start()
    client = ClaimClient(server.url)
    client.call('bootstrap', bootstrap=True)
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(tmp_path))
    # Each call gets an independent client, as on the two production machines.
    monkeypatch.setattr(ClaimClient, 'from_env', lambda: ClaimClient(server.url))
    try:
        yield server
    finally:
        server.stop()


async def _stored(server, value=None):
    import nats
    nc = await nats.connect(server.url)
    try:
        kv = await nc.jetstream().key_value(bucket_name('retries'))
        if value is not None:
            await kv.put(budget.KEY, json.dumps(value).encode())
        return json.loads((await kv.get(budget.KEY)).value)
    finally:
        await nc.close()


def test_account_cas_race_admits_exactly_40_across_resources(server):
    barrier = threading.Barrier(8)
    def machine(index):
        barrier.wait()
        admitted = 0
        for _ in range(8):
            try:
                budget.reserve('graphql' if index % 2 else 'core')
                admitted += 1
            except budget.GitHubDeferred:
                pass
        return admitted
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(machine, range(8))) == 40
    state = asyncio.run(_stored(server))
    assert len(state['requests']) == 40
    assert set(state['resources']) == {'core', 'graphql'}


@pytest.mark.parametrize('resource,reserve', [('core', 100), ('graphql', 100), ('search', 1)])
def test_each_resource_stops_at_its_reserved_quota(server, resource, reserve):
    budget.observe({'X-RateLimit-Remaining': str(reserve + 1),
                    'X-RateLimit-Reset': str(int(time.time()) + 3600)}, resource)
    budget.reserve(resource)
    with pytest.raises(budget.GitHubDeferred, match='quota reserve'):
        budget.reserve(resource)
    assert budget.snapshot()['resources'][resource]['remaining'] == reserve


def test_rate_limit_is_shared_and_does_not_block_unrelated_resource(server):
    now = int(time.time())
    budget.observe({'Retry-After': '120', 'X-RateLimit-Remaining': '0',
                    'X-RateLimit-Reset': str(now + 300)}, limited=True)
    with pytest.raises(budget.GitHubDeferred, match='backoff'):
        budget.reserve()
    assert budget.snapshot()['resources']['core']['blocked_until'] >= now + 300
    budget.reserve('graphql')


def test_secondary_limits_get_exponential_backoff_and_late_success_cannot_clear_it():
    state = {'version': 1, 'requests': [], 'resources': {}}
    budget._mutate(state, 'observe', 'core', {}, True, 1000)
    assert state['resources']['core']['blocked_until'] == 1060
    budget._mutate(state, 'observe', 'core', {}, True, 1001)
    assert state['resources']['core']['blocked_until'] == 1121
    budget._mutate(state, 'observe', 'core', {}, False, 1002)
    assert state['resources']['core']['blocked_until'] == 1121
    budget._mutate(state, 'observe', 'core', {}, False, 1122)
    budget._mutate(state, 'observe', 'core', {'retry-after': '180'}, True, 1123)
    assert state['resources']['core']['blocked_until'] == 1303


def test_rolling_window_has_no_fixed_minute_boundary_burst():
    state = {'version': 1, 'requests': [], 'resources': {}}
    for _ in range(40):
        budget._mutate(state, 'reserve', 'core', {}, False, 119.9)
    with pytest.raises(budget.GitHubDeferred, match='minute budget'):
        budget._mutate(state, 'reserve', 'graphql', {}, False, 120.1)
    budget._mutate(state, 'reserve', 'core', {}, False, 180)
    assert state['requests'] == [180]


def test_quota_updates_cannot_raise_same_period_remaining_or_restore_old_period():
    state = {'version': 1, 'requests': [], 'resources': {}}
    budget._mutate(state, 'observe', 'core', {
        'x-ratelimit-remaining': '150', 'x-ratelimit-reset': '2000'}, False, 1000)
    budget._mutate(state, 'reserve', 'core', {}, False, 1001)
    budget._mutate(state, 'observe', 'core', {
        'x-ratelimit-remaining': '150', 'x-ratelimit-reset': '2000'}, False, 1002)
    assert state['resources']['core']['remaining'] == 149
    budget._mutate(state, 'observe', 'core', {
        'x-ratelimit-remaining': '5000', 'x-ratelimit-reset': '3000'}, False, 2001)
    budget._mutate(state, 'observe', 'core', {
        'x-ratelimit-remaining': '1', 'x-ratelimit-reset': '2000'}, False, 2002)
    assert state['resources']['core']['remaining'] == 5000
    assert state['resources']['core']['reset'] == 3000


def test_coordination_outage_defers_without_local_fallback_or_secret_details(server):
    budget.reserve()
    server.stop()
    with pytest.raises(budget.GitHubDeferred, match='unavailable') as error:
        budget.reserve()
    assert 'nats://' not in str(error.value)
    assert budget.snapshot()['status'] == 'unavailable'
    assert budget.snapshot()['requests_last_minute'] == 1


def test_missing_configuration_defers_without_admission(tmp_path, monkeypatch):
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(ClaimClient, 'from_env', lambda: (_ for _ in ()).throw(
        RuntimeError('private-token-and-address')))
    with pytest.raises(budget.GitHubDeferred) as error:
        budget.reserve()
    assert 'private-token-and-address' not in str(error.value)


def test_corrupt_shared_state_never_resets_budget(server):
    asyncio.run(_stored(server, {'version': 1, 'requests': ['bad'], 'resources': {}}))
    with pytest.raises(budget.GitHubDeferred, match='invalid'):
        budget.reserve()
    assert asyncio.run(_stored(server))['requests'] == ['bad']


def test_snapshot_is_local_allowlisted_and_observe_never_persists_headers(server, tmp_path):
    budget.observe({'Authorization': 'private-auth-value',
                    'X-RateLimit-Remaining': '4900', 'X-RateLimit-Limit': '5000'})
    persisted = (tmp_path / budget.SNAPSHOT).read_text()
    shared = json.dumps(asyncio.run(_stored(server)))
    assert 'private-auth-value' not in persisted + shared
    with patch.object(ClaimClient, 'from_env', side_effect=AssertionError('no network')):
        assert budget.snapshot(tmp_path)['resources']['core']['remaining'] == 4900
    (tmp_path / budget.SNAPSHOT).write_text(json.dumps({
        'status': 'ok', 'updated_at': 1000,
        'resources': {'core': {'remaining': 1000}},
        'reason': 'private-details', 'Authorization': 'private-auth-value'}))
    assert 'private-' not in json.dumps(budget.snapshot(tmp_path))
