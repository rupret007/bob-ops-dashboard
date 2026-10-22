"""Reviews share fix admission; broker errors cannot publish fake failures."""
import concurrent.futures
import threading
from unittest.mock import patch

import pytest

from cluster.control_plane.tests.test_coordination import client
from cluster.control_plane.coordination import ClaimClient, CoordinationError
from cluster.control_plane.local_karen import gate
from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.reviewers import ReviewResult, ReviewVerdict


def info():
    return PRInfo('owner', 'repo', 7, 'a' * 40, 'Fix', 'author', False,
                  '+safe', {}, ['file.py'], [])


def vote():
    review = ReviewResult('codex', ReviewVerdict.PASS, 'Checked supplied changes')
    return review, [review]


def test_two_karens_only_one_provider_review(client, tmp_path):
    entered, finish = threading.Event(), threading.Event()
    def review(*args):
        entered.set()
        assert finish.wait(5)
        return vote()
    config = KarenConfig(verdict_dir=str(tmp_path), state_dir=str(tmp_path))
    with patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(gate, 'fetch_pr_info', return_value=info()), \
         patch.object(gate, 'verify_sha_current', return_value=True), \
         patch.object(gate, 'run_review_chain', side_effect=review) as provider:
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            first = pool.submit(gate.run_gate, 'owner', 'repo', 7, config)
            try:
                assert entered.wait(3)
                second = pool.submit(gate.run_gate, 'owner', 'repo', 7, config).result(3)
                assert second.error and second.verdict.status == 'ERROR'
                assert second.verdict_path is None
            finally:
                finish.set()
            assert first.result(3).sha_verified
        assert provider.call_count == 1
    assert client.acquire('owner/repo', 7, 'b' * 40, 'nucbox')


@pytest.mark.parametrize('blocked', ['outage', 'fix-active'])
def test_unavailable_or_fix_owned_pr_never_reviews(client, tmp_path, blocked):
    if blocked == 'fix-active':
        client.acquire('owner/repo', 7, 'a' * 40, 'nucbox')
    else:
        client = ClaimClient('nats://127.0.0.1:1')
    with patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(gate, 'fetch_pr_info', return_value=info()), \
         patch.object(gate, 'run_review_chain') as provider, \
         patch.object(gate, 'save_verdict') as save:
        result = gate.run_gate('owner', 'repo', 7, KarenConfig(verdict_dir=str(tmp_path)))
    assert result.error and result.verdict.status == 'ERROR'
    assert result.verdict.head_sha == 'a' * 40
    assert result.verdict_path is None
    provider.assert_not_called()
    save.assert_not_called()


def test_claim_lost_during_review_never_saves_or_posts(client, tmp_path):
    original = client.call
    lost = [False]
    def operation(kind, *args, **kwargs):
        if kind == 'renew' and lost[0]:
            raise CoordinationError('Cluster claim lost')
        return original(kind, *args, **kwargs)
    def review(*args):
        lost[0] = True
        return vote()
    with patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(client, 'call', side_effect=operation), \
         patch.object(gate, 'fetch_pr_info', return_value=info()), \
         patch.object(gate, 'run_review_chain', side_effect=review), \
         patch.object(gate, 'save_verdict') as save, \
         patch('cluster.control_plane.local_karen.github_client.post_verdict_comment') as post:
        result = gate.run_gate('owner', 'repo', 7, KarenConfig(verdict_dir=str(tmp_path)), True)
    assert result.error and result.verdict.status == 'ERROR'
    save.assert_not_called()
    post.assert_not_called()


def test_missing_configuration_returns_retryable_exact_sha(tmp_path):
    with patch.object(ClaimClient, 'from_env', side_effect=CoordinationError('not configured')), \
         patch.object(gate, 'fetch_pr_info', return_value=info()), \
         patch.object(gate, 'run_review_chain') as provider:
        result = gate.run_gate('owner', 'repo', 7, KarenConfig(verdict_dir=str(tmp_path)))
    assert result.verdict.status == 'ERROR' and result.verdict.head_sha == 'a' * 40
    assert not result.verdict_path
    provider.assert_not_called()


def test_lost_claim_stops_second_provider(client, tmp_path):
    from cluster.control_plane.local_karen import reviewers as r
    from unittest.mock import Mock
    def review(*args):
        gate._review_lease.get().lost = True
        return vote()[0]
    primary = Mock(review=Mock(side_effect=review))
    with patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(gate, 'fetch_pr_info', return_value=info()), \
         patch.object(r, 'get_available_reviewers', return_value=[('codex', primary)]), \
         patch.object(r.MiniMaxReviewer, 'review_as_second_judge') as second, \
         patch.object(gate, 'save_verdict') as save:
        result = gate.run_gate('owner', 'repo', 7, KarenConfig(verdict_dir=str(tmp_path)))
    assert result.verdict.status == 'ERROR'
    second.assert_not_called()
    save.assert_not_called()
