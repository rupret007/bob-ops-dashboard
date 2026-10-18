"""Operational errors cannot masquerade as reviews or consume the retry label."""
from unittest.mock import patch, Mock
import json
import subprocess
from pathlib import Path
import pytest

from cluster.control_plane.local_karen import gate, poller, github_client, reviewers
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.reviewers import ReviewResult, ReviewVerdict, ReviewFindings, ReviewTier


def pr():
    return PRInfo('owner', 'repo', 1, 'a'*40, 'Test', 'author', False,
                  '+safe', {}, ['file.py'], [])


def test_errors_never_save_or_post(tmp_path):
    config = KarenConfig(verdict_dir=str(tmp_path), state_dir=str(tmp_path))
    for error in ('fetch', 'review', 'sha', 'save'):
        with patch.object(gate, 'fetch_pr_info', return_value=pr()) as fetch, \
             patch.object(gate, 'run_review_chain') as review, \
             patch.object(gate, 'verify_sha_current', return_value=True) as sha, \
             patch.object(gate, 'save_verdict') as save, \
             patch.object(github_client, 'post_verdict_comment') as post:
            vote = ReviewResult('codex', ReviewVerdict.PASS, 'Inspected complete diff')
            review.return_value = (vote, [vote])
            if error == 'fetch': fetch.side_effect = github_client.GitHubError('network')
            if error == 'review': vote.verdict = ReviewVerdict.ERROR
            if error == 'sha': sha.return_value = False
            if error == 'save': save.side_effect = OSError('disk')
            result = gate.run_gate('owner', 'repo', 1, config, post_comment=True)
            assert result.error and result.verdict.status == 'ERROR'
            assert result.verdict.verdict_type == 'ERROR'
            assert result.verdict_path is None
            post.assert_not_called()
            if error != 'save': save.assert_not_called()


def test_poller_error_keeps_retry_label(tmp_path):
    with patch.object(poller, 'list_prs_with_label', return_value=[{'number': 1}]), \
         patch.object(poller, 'run_gate', side_effect=RuntimeError('review unavailable')), \
         patch.object(poller, 'add_label') as add, patch.object(poller, 'remove_label') as remove:
        results = poller.poll_and_run(['owner/repo'], KarenConfig(state_dir=str(tmp_path)))
    assert results[0]['error'] == 'review unavailable'
    add.assert_not_called()
    remove.assert_not_called()


def test_failing_checks_json_is_not_a_fetch_error():
    result = subprocess.CompletedProcess([], 1, '[{"bucket":"fail","name":"build"}]', '')
    with patch.object(github_client.subprocess, 'run', return_value=result):
        data = github_client._run_gh(['pr', 'checks'], allowed_codes=(0,1,8))
    assert github_client._parse_ci_status(json.loads(data)) == {'build': 'failure'}


def test_oversized_diff_falls_back_to_pinned_objects():
    data = {'headRefOid':'a'*40,'baseRefOid':'b'*40,'files':[], 'number':1}
    def gh(args, **kwargs):
        if args[:2] == ['pr','diff']: raise github_client.GitHubError('HTTP 406 too_large')
        if args[:2] == ['pr','checks']: return '[]'
        return json.dumps(data)
    with patch.object(github_client, '_run_gh', side_effect=gh), \
         patch.object(github_client, 'fetch_git_diff', return_value=('complete diff', [])) as fallback:
        result = github_client.fetch_pr_info('owner','repo',1)
    fallback.assert_called_once_with('owner','repo','a'*40,'b'*40)
    assert result.diff == 'complete diff'


def test_codex_receives_full_diff_and_reads_only_final_output(tmp_path):
    info = pr()
    info.diff = '+' + 'x'*18000 + '\n+TAIL_REVIEW_REQUIRED'
    response = json.dumps(dict(verdict='FAIL', reason='A real functional defect at file.py:12', confidence=.9,
                               findings={'functional_issues':['file.py:12 wrong result']}))
    def execute(args, **kwargs):
        assert 'TAIL_REVIEW_REQUIRED' in kwargs['input']
        assert info.head_sha in kwargs['input']
        assert 'model_reasoning_effort="max"' in args
        assert args[args.index('-m')+1] == 'gpt-6-astra'
        Path(args[args.index('-o')+1]).write_text(response)
        return subprocess.CompletedProcess(args, 0, 'progress text is not JSON', '')
    with patch.object(reviewers.subprocess, 'run', side_effect=execute):
        result = reviewers.CodexReviewer().review(info, ReviewTier.LOW,
                         KarenConfig(codex_host='local', state_dir=str(tmp_path)))
    assert result.verdict == ReviewVerdict.FAIL
    assert result.findings.functional_issues == ['file.py:12 wrong result']


def test_malformed_review_is_retryable_error():
    for body in ('not json', '[]', '{"verdict":"PASS","reason":"unknown"}'):
        result = reviewers.CodexReviewer()._parse_structured_verdict(body, pr(), ReviewTier.LOW)
        assert result.verdict == ReviewVerdict.ERROR
        assert not result.passed


def test_macos_swap_is_measured_and_unknown_blocks():
    from cluster.control_plane import guards
    with patch.object(guards.platform, 'system', return_value='Darwin'), \
         patch.object(guards.subprocess, 'run', return_value=Mock(stdout='total = 5120.00M used = 1024.00M free = 4096.00M')):
        assert guards.get_swap_pct() == 20
    with patch.object(guards.platform, 'system', return_value='Darwin'), \
         patch.object(guards.subprocess, 'run', side_effect=OSError('denied')):
        assert guards.get_swap_pct() == 100
        assert not guards.MachineGuards('mini', load_fn=lambda:1, disk_fn=lambda:30).check_all().passed
