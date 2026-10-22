"""Every byte and independent vote is required for an oversized review."""
import json
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import gate, reviewers as r
from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.config import KarenConfig


def pr_info(diff):
    return PRInfo('owner', 'repo', 1, 'a' * 40, 'Refactor', 'author', False,
                  diff, {'tests': 'success'}, ['first.py', 'last.py'], [],
                  description='Original refactor scope and validation details.')


def large_diff():
    return ('diff --git a/first.py b/first.py\n@@ -1 +1 @@\n' + '+safe\n' * 70000
            + 'diff --git a/last.py b/last.py\n@@ -1 +1 @@\n+TAIL_MUST_BE_REVIEWED\n')


def vote(name='codex', verdict=r.ReviewVerdict.PASS):
    return r.ReviewResult(name, verdict, 'Inspected supplied changes independently',
                          confidence=0.8, findings=r.ReviewFindings(risk_notes=['Reviewed supplied diff']))


def run_with_votes(pr, primary_vote=None, second_vote=None):
    primary = Mock(review=Mock(side_effect=primary_vote or (lambda *args: vote())))
    with patch.object(r, 'get_available_reviewers', return_value=[('codex', primary)]), \
         patch.object(r.MiniMaxReviewer, 'is_available', return_value=(True, 'ok')), \
         patch.object(r.MiniMaxReviewer, 'review_as_second_judge',
                      side_effect=second_vote or (lambda *args: vote('minimax'))) as second:
        result, reviews = r.run_review_chain(pr, KarenConfig())
    return result, reviews, primary, second


def test_complete_large_diff_is_reviewed_by_both_models_at_same_sha():
    pr = pr_info(large_diff())
    result, reviews, primary, second = run_with_votes(pr)
    supplied = [c.args[0] for c in primary.review.call_args_list]
    assert len(supplied) > 1
    assert ''.join(p.diff for p in supplied) == pr.diff
    assert all(len(p.diff) <= 12000 for p in supplied)
    assert all(p.head_sha == pr.head_sha and p.changed_files == pr.changed_files for p in supplied)
    assert all(p.description == pr.description for p in supplied)
    assert all(c.args[1] == r.classify_risk_tier(pr) for c in primary.review.call_args_list)
    assert [c.args[0].diff for c in second.call_args_list] == [p.diff for p in supplied]
    assert result.passed
    assert len(reviews) == 2 * len(supplied)
    assert all(v.to_dict()['scope']['head_sha'] == pr.head_sha for v in reviews)
    scopes = [v.scope for v in reviews[::2]]
    assert scopes[0]['start'] == 0 and scopes[-1]['end'] == len(pr.diff)
    assert all(a['end'] == b['start'] for a, b in zip(scopes, scopes[1:]))
    assert 'TAIL_MUST_BE_REVIEWED' in supplied[-1].diff


@pytest.mark.parametrize('verdict', [r.ReviewVerdict.FAIL, r.ReviewVerdict.BORDERLINE, r.ReviewVerdict.ERROR])
@pytest.mark.parametrize('reject_second', [False, True])
def test_rejection_or_error_in_tail_never_becomes_pass(verdict, reject_second):
    def reject(pr, *args):
        return vote('minimax' if reject_second else 'codex',
                    verdict if 'TAIL_MUST_BE_REVIEWED' in pr.diff else r.ReviewVerdict.PASS)
    result, _, _, _ = run_with_votes(pr_info(large_diff()),
                                    second_vote=reject if reject_second else None,
                                    primary_vote=None if reject_second else reject)
    assert not result.passed
    assert (result.verdict == r.ReviewVerdict.ERROR) == (verdict == r.ReviewVerdict.ERROR)


def test_long_single_line_and_file_boundaries_lose_no_characters():
    diff = 'diff --git a/big.py b/big.py\n+' + 'x' * 30001 + '\n'
    chunks = r.split_review_diff(diff)
    assert ''.join(chunks) == diff
    assert all(0 < len(c) <= 12000 for c in chunks)
    first = 'diff --git a/a.py b/a.py\n+a\n'
    last = 'diff --git a/b.py b/b.py\n+b\n'
    assert r.split_review_diff(first + last, limit=30) == [first, last]


def test_small_review_retains_original_behavior():
    pr = pr_info('+small change')
    result, reviews, primary, second = run_with_votes(pr)
    assert result.passed
    assert primary.review.call_count == second.call_count == 1
    assert primary.review.call_args.args[0] is pr


def test_adjacent_complete_files_share_context_without_skipping_either_vote():
    sections = [('diff --git a/file{0}.py b/file{0}.py\n--- a/file{0}.py\n'
                 '+++ b/file{0}.py\n@@ -1 +1 @@\n'.format(index) + '+safe\n' * 600)
                for index in range(6)]
    pr = pr_info(''.join(sections))
    pr.changed_files = ['file{}.py'.format(index) for index in range(6)]
    result, reviews, primary, second = run_with_votes(pr)
    supplied = [call.args[0] for call in primary.review.call_args_list]
    assert len(supplied) == 2
    assert ''.join(part.diff for part in supplied) == pr.diff
    assert all(0 < len(part.diff) <= r.REVIEW_DIFF_LIMIT for part in supplied)
    assert all(part.diff.count('diff --git ') == 3 for part in supplied)
    assert [call.args[0].diff for call in second.call_args_list] == [part.diff for part in supplied]
    for index, part in enumerate(supplied):
        assert part.description == pr.description
        context = part.review_scope.data
        assert context['current_chunk']['files'] == pr.changed_files[index * 3:index * 3 + 3]
    assert result.passed and len(reviews) == 4


def test_packing_around_oversized_file_preserves_every_byte_and_boundary():
    small = 'diff --git a/small.py b/small.py\n+small\n'
    oversized = 'diff --git a/large.py b/large.py\n+' + 'x' * 30001 + '\n'
    tail = 'diff --git a/tail.py b/tail.py\n+TAIL_MUST_BE_REVIEWED\n'
    diff = small + small + oversized + tail + tail
    chunks = r.split_review_diff(diff)
    assert ''.join(chunks) == diff
    assert chunks[0] == small + small and chunks[-1] == tail + tail
    assert all(0 < len(chunk) <= r.REVIEW_DIFF_LIMIT for chunk in chunks)


def test_gemini_prompt_keeps_tail_in_direct_review():
    pr = pr_info('x' * 13000 + 'TAIL_MUST_BE_REVIEWED')
    gemini = r.GeminiReviewer()
    with patch.object(gemini, '_load_key', return_value='fixture'), \
         patch('urllib.request.urlopen', side_effect=RuntimeError('offline')) as request:
        gemini.review(pr, r.ReviewTier.HIGH, KarenConfig())
    payload = json.loads(request.call_args.args[0].data)
    assert pr.diff in payload['contents'][0]['parts'][0]['text']


@pytest.mark.parametrize('current', [True, False])
def test_chunk_pass_still_requires_final_sha_verification(tmp_path, current):
    pr = pr_info(large_diff())
    config = KarenConfig(verdict_dir=str(tmp_path), state_dir=str(tmp_path))
    with patch.object(gate, 'fetch_pr_info', return_value=pr), \
         patch.object(gate, 'verify_sha_current', return_value=current), \
         patch.object(r, 'get_available_reviewers', return_value=[('codex', Mock(review=Mock(return_value=vote())))]), \
         patch.object(r.MiniMaxReviewer, 'is_available', return_value=(True, 'ok')), \
         patch.object(r.MiniMaxReviewer, 'review_as_second_judge', return_value=vote('minimax')):
        result = gate.run_gate('owner', 'repo', 1, config)
    assert result.sha_verified == current
    assert bool(result.verdict_path) == current
    if current:
        saved = json.loads(open(result.verdict_path).read())
        assert len(saved['review_results']) > 2
        assert all(v['scope']['head_sha'] == pr.head_sha for v in saved['review_results'])
    else:
        assert result.verdict.status == 'ERROR'
        assert not list(tmp_path.glob('*.json'))


def test_legacy_flag_cannot_bypass_second_vote_on_chunks():
    with patch.object(r, 'get_available_reviewers', return_value=[('codex', Mock(review=Mock(return_value=vote())))]), \
         patch.object(r.MiniMaxReviewer, 'is_available', return_value=(False, 'offline')):
        result, _ = r.run_review_chain(pr_info(large_diff()), KarenConfig(), require_two_pass=False)
    assert result.verdict == r.ReviewVerdict.ERROR


def test_deterministic_secret_check_covers_tail_before_chunking(tmp_path):
    pr = pr_info(large_diff() + '+TOKEN=' + 'ghp_' + 'B7c9' * 9 + '\n')
    with patch.object(gate, 'fetch_pr_info', return_value=pr), \
         patch.object(gate, 'verify_sha_current', return_value=True), \
         patch.object(gate, 'run_review_chain') as review:
        result = gate.run_gate('owner', 'repo', 1, KarenConfig(verdict_dir=str(tmp_path)))
    assert result.verdict.verdict_type == 'KARENFAIL'
    assert result.sha_verified
    review.assert_not_called()
