"""Typed worker failures round-trip durable retries without granting a reset."""
import pytest

from cluster.control_plane.coordination import ClaimClient, ClaimLost, CoordinationError
from cluster.control_plane.retry_policy import FAILURE_REASONS, REASONS, reserve
from cluster.tests.nats_test_util import EphemeralNats


@pytest.fixture
def client():
    server = EphemeralNats(); server.start()
    client = ClaimClient(server.url, ttl=2)
    client.call('bootstrap', bootstrap=True)
    try:
        yield client
    finally:
        server.stop()


def test_every_canonical_worker_failure_round_trips_without_changing_budget(client):
    for number, code in enumerate(FAILURE_REASONS, 1):
        claim = client.acquire('fixture/repo', number, 'a'*40, 'nucbox')
        assert client.retry_state(claim) is None
        original = dict(attempts=1, retry_after=1300, code=code)
        client.retry_state(claim, original)
        saved = client.retry_state(claim)
        assert saved == original
        assert reserve(saved, historical_attempts=1, now=1299)[0] is None
        second, reason = reserve(saved, historical_attempts=1, now=1300)
        assert reason == '' and second == dict(attempts=2, retry_after=1900, code='transient')
        assert client.retry_state(claim) == original  # Pure reserve did not persist it.
        client.retry_state(claim, dict(original, attempts=3))
        assert 'retry limit' in reserve(client.retry_state(claim), now=10**12)[1]
        # Ledger history remains an independent lower bound on attempt count.
        assert 'retry limit' in reserve(saved, historical_attempts=3, now=10**12)[1]
        client.call('release', claim)


def test_new_codes_retain_exact_head_and_owner_fences(client):
    claim = client.acquire('fixture/repo', 7, 'a'*40, 'mini')
    assert client.retry_state(claim) is None
    state = dict(attempts=1, retry_after=12345, code='tests-failed')
    client.retry_state(claim, state)
    stale = dict(claim)
    client.call('release', claim)
    successor = client.acquire('fixture/repo', 7, 'a'*40, 'nucbox')
    assert client.retry_state(successor) == state
    with pytest.raises(ClaimLost):
        client.retry_state(stale)
    with pytest.raises(ClaimLost):
        client.retry_state(stale, dict(state, attempts=1, code='transient', retry_after=0))
    assert client.retry_state(successor) == state
    client.call('release', successor)
    fresh = client.acquire('fixture/repo', 7, 'b'*40, 'nucbox')
    assert client.retry_state(fresh) is None
    client.call('release', fresh)


@pytest.mark.parametrize('change', [
    {'code': 'unknown-private-provider-message'}, {'code': None}, {'code': {}},
    {'attempts': True}, {'attempts': 0}, {'attempts': '1'},
    {'retry_after': True}, {'retry_after': '1300'},
])
def test_malformed_or_unknown_values_still_fail_closed(client, change):
    claim = client.acquire('fixture/repo', 7, 'a'*40, 'nucbox')
    assert client.retry_state(claim) is None
    client.retry_state(claim, dict(dict(attempts=1, retry_after=1300, code='deadline'), **change))
    with pytest.raises(CoordinationError, match='Invalid retry state; new fixes paused'):
        client.retry_state(claim)
    client.call('release', claim)


def test_permanent_preflight_reasons_remain_permanently_blocked(client):
    for number, code in enumerate(REASONS, 1):
        claim = client.acquire('fixture/permanent', number, 'a'*40, 'nucbox')
        assert client.retry_state(claim) is None
        original = dict(attempts=1, retry_after=0, code=code)
        client.retry_state(claim, original)
        saved = client.retry_state(claim)
        assert saved == original and reserve(saved, now=10**12) == (None, REASONS[code])
        client.call('release', claim)
