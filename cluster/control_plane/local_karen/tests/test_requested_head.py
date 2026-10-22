"""A poll result cannot silently authorize review of another head."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cluster.control_plane.local_karen import gate
from cluster.control_plane.local_karen.config import KarenConfig


@pytest.mark.parametrize('expected', ['b' * 40, 'short', ''])
def test_requested_head_mismatch_defers_before_claim_checks_or_models(tmp_path, expected):
    pr = SimpleNamespace(head_sha='a' * 40)
    config = KarenConfig(verdict_dir=str(tmp_path), state_dir=str(tmp_path))
    with patch.object(gate, 'fetch_pr_info', return_value=pr), \
         patch.object(gate.ClaimClient, 'from_env') as client, \
         patch.object(gate, 'run_all_checks') as checks:
        result = gate.run_gate('owner', 'repo', 7, config=config, expected_sha=expected)
    assert result.error and not result.verdict_path and not result.sha_verified
    assert result.verdict.head_sha == 'a' * 40
    client.assert_not_called()
    checks.assert_not_called()
