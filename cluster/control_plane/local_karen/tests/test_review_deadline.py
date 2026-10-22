"""Timeouts release review ownership without a partial PASS/FAIL artifact."""
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import deadline, gate, reviewers as r
from cluster.control_plane.local_karen.config import KarenConfig
from .test_chunked_reviews import large_diff, pr_info, vote


def test_expired_chunk_chain_releases_claim_and_never_publishes(tmp_path):
    clock = [100.0]
    calls = []

    def review(part, *args):
        calls.append(part.diff)
        clock[0] += 700
        return vote()

    with patch.object(deadline, 'time', Mock(monotonic=lambda: clock[0])), \
         patch.object(gate, 'fetch_pr_info', return_value=pr_info(large_diff())), \
         patch.object(gate, 'run_all_checks', return_value=[]), \
         patch.object(r, 'get_available_reviewers', return_value=[('codex', Mock(review=review))]), \
         patch.object(r.MiniMaxReviewer, 'is_available', return_value=(True, 'ok')), \
         patch.object(r.MiniMaxReviewer, 'review_as_second_judge', return_value=vote('minimax')), \
         patch.object(gate, 'save_verdict') as save:
        client = gate.ClaimClient.from_env()
        result = gate.run_gate('owner', 'repo', 1, KarenConfig(verdict_dir=str(tmp_path)))
        assert client.call.call_args.args[0] == 'release'
    assert result.verdict.status == 'ERROR'
    assert '20-minute budget' in result.error
    assert result.verdict.head_sha == 'a' * 40
    assert not result.sha_verified and result.verdict_path is None
    assert len(calls) == 2
    assert 'TAIL_MUST_BE_REVIEWED' not in ''.join(calls)
    save.assert_not_called()
    assert deadline.remaining_timeout(900) == 900  # Next PR gets its own budget.


def test_provider_timeout_uses_remaining_monotonic_budget():
    with patch.object(deadline.time, 'monotonic', return_value=100) as clock:
        with deadline.review_budget():
            assert deadline.remaining_timeout(60) == 60
            clock.return_value = 1290
            assert deadline.remaining_timeout(900) == 10
            clock.return_value = 1300
            with pytest.raises(deadline.ReviewDeadlineExceeded):
                gate.review_checkpoint()
    assert deadline.remaining_timeout(900) == 900
