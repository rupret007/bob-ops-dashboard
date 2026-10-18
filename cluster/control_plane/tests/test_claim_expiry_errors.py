"""Broker expiry errors remain safe and recoverable on every Python version."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nats.js.errors import APIError, KeyNotFoundError

from cluster.control_plane.coordination import ClaimClient, ClaimBusy, ClaimLost, CoordinationError


@pytest.mark.parametrize('operation,expected', [
    ('acquire', ClaimBusy), ('renew', ClaimLost), ('release', ClaimLost),
])
def test_expired_key_without_api_error_code_defers_safely(operation, expected):
    client = ClaimClient('nats://127.0.0.1:1')
    kv = MagicMock()
    kv.status = AsyncMock(return_value=MagicMock(ttl=300))
    kv.create = AsyncMock(side_effect=KeyNotFoundError(message='synthetic-private-value'))
    kv.update = AsyncMock(side_effect=KeyNotFoundError())
    kv.delete = AsyncMock(side_effect=KeyNotFoundError())
    nc = MagicMock()
    nc.close = AsyncMock()
    nc.jetstream.return_value.key_value = AsyncMock(return_value=kv)
    claim = dict(key='pr.test', owner='test-owner', repo='owner/repo', pr=1,
                 sha='a' * 40, machine='nucbox', revision=1)
    with patch('nats.connect', AsyncMock(return_value=nc)):
        with pytest.raises(expected) as error:
            client.call(operation, claim)
    assert 'synthetic-private-value' not in str(error.value)
    nc.close.assert_awaited_once()


def test_api_error_without_code_remains_fail_closed():
    class IncompleteAPIError(APIError):
        def __init__(self):
            pass
    with patch('nats.connect', AsyncMock(side_effect=IncompleteAPIError())):
        with pytest.raises(CoordinationError, match='new work paused'):
            ClaimClient('nats://127.0.0.1:1').acquire('owner/repo', 1, 'a' * 40, 'mini')
