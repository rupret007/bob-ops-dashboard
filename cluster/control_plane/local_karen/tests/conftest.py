"""Isolate broker I/O in unit tests; integration races use real JetStream."""
from unittest.mock import patch

import pytest

from cluster.control_plane.coordination import ClaimClient


@pytest.fixture(autouse=True)
def isolated_review_claims():
    client = ClaimClient('nats://unit-test.invalid')
    revision = [0]
    def operation(kind, claim=None, **kwargs):
        revision[0] += 1
        return {**claim, 'revision': revision[0]} if kind != 'release' else None
    with patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(client, 'call', side_effect=operation):
        yield
