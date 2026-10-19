"""Chunk uncertainty stays retryable; only grounded defects are actionable."""
import json
from dataclasses import replace
from unittest.mock import Mock, patch

from cluster.control_plane.local_karen import gate, reviewers as r
from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.config import KarenConfig


def pr_info():
    diff = ('diff --git a/docs.md b/docs.md\n--- a/docs.md\n+++ b/docs.md\n'
            '@@ -1 +1,1800 @@\n-old\n' + '+context\n' * 1800 +
            'diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n'
            '@@ -1 +1 @@\n-old\n+return unsafe(user_input)\n')
    return PRInfo('owner', 'repo', 1, 'a' * 40, 'Implement safe app', 'author',
                  False, diff, {'tests': 'success'}, ['docs.md', 'app.py'], [],
                  description='The implementation and its tests span this whole PR.')


def vote(verdict=r.ReviewVerdict.PASS, defect=None, requests=None):
    result = r.ReviewResult('codex', verdict, 'Reviewed the supplied pinned implementation',
                            confidence=.8,
                            findings=r.ReviewFindings(functional_issues=[defect] if defect else [],
                                                     risk_notes=['Checked complete supplied scope']))
    result.requested_files = requests or []
    return result


def run(primary, second=None):
    reviewer = Mock(review=Mock(side_effect=primary))
    with patch.object(r, 'get_available_reviewers', return_value=[('codex', reviewer)]), \
         patch.object(r.MiniMaxReviewer, 'is_available', return_value=(True, 'ok')), \
         patch.object(r.MiniMaxReviewer, 'review_as_second_judge',
                      side_effect=second or (lambda *args: replace(vote(), reviewer='minimax'))):
        outcome, reviews = r.run_review_chain(pr_info(), KarenConfig())
    return outcome, reviews, reviewer


def test_each_chunk_receives_whole_pr_context_and_other_chunk_map():
    result, _, reviewer = run(lambda *args: vote())
    assert result.passed
    for call in reviewer.review.call_args_list:
        supplied = call.args[0]
        assert supplied.title == 'Implement safe app'
        assert 'The implementation and its tests span this whole PR.' in supplied.description
        assert 'docs.md' in supplied.description and 'app.py' in supplied.description
        assert 'additions' in supplied.description and 'deletions' in supplied.description
        assert 'chunk' in supplied.description


def test_missing_context_with_valid_citation_is_never_actionable_or_pass():
    result, reviews, _ = run(lambda *args: vote(r.ReviewVerdict.FAIL,
                              'app.py:1 cannot verify the installer because it is not included'))
    assert result.verdict == r.ReviewVerdict.BORDERLINE
    assert result.scope['context_incomplete']
    assert all(item.verdict != r.ReviewVerdict.FAIL for item in reviews)


def test_ungrounded_defect_retries_without_approving():
    result, _, _ = run(lambda *args: vote(r.ReviewVerdict.FAIL,
                              'app.py:999 subprocess injection accepts unsanitized input'))
    assert not result.passed and result.scope['context_incomplete']


def test_concrete_tail_defect_survives_earlier_operational_error():
    def review(pr, *args):
        if 'unsafe(user_input)' in pr.diff:
            return vote(r.ReviewVerdict.FAIL, 'app.py:1 invokes unsafe with unvalidated user input')
        return vote(r.ReviewVerdict.ERROR)
    result, reviews, reviewer = run(review)
    assert result.verdict == r.ReviewVerdict.FAIL
    assert 'app.py:1' in result.findings.functional_issues[0]
    assert reviewer.review.call_count == len(r.split_review_diff(pr_info().diff))
    assert any(item.verdict == r.ReviewVerdict.ERROR for item in reviews)


def test_concrete_early_defect_does_not_hide_remaining_chunks():
    def review(pr, *args):
        return vote(r.ReviewVerdict.FAIL, 'app.py:1 invokes unsafe with unvalidated user input')
    result, _, reviewer = run(review)
    assert result.verdict == r.ReviewVerdict.FAIL
    assert reviewer.review.call_count == len(r.split_review_diff(pr_info().diff))
    assert 'unsafe(user_input)' in reviewer.review.call_args_list[-1].args[0].diff


def test_requested_file_is_supplied_at_exact_head_then_two_passes_required():
    def review(pr, *args):
        if 'Requested changed-file context at exact head' not in pr.description:
            return vote(r.ReviewVerdict.BORDERLINE, requests=['app.py'])
        assert 'return unsafe(user_input)' in pr.description
        assert pr.head_sha in pr.description
        return vote()
    result, reviews, reviewer = run(review)
    assert result.passed
    assert all(item.verdict == r.ReviewVerdict.PASS for item in reviews)
    assert any((item.scope or {}).get('context_attempts') for item in reviews)
    from cluster.control_plane.local_karen.verdict import create_verdict
    from cluster.control_plane.local_karen.verdict_push import reviewed
    verdict = create_verdict(pr_info(), [], reviews, True, result.reason, result.reviewer)
    assert reviewed(verdict)
    assert reviewer.review.call_count == 2 * len(r.split_review_diff(pr_info().diff))


def test_unknown_context_request_never_reads_or_approves():
    result, _, reviewer = run(lambda *args: vote(r.ReviewVerdict.BORDERLINE,
                                                requests=['../../outside.key']))
    assert not result.passed and result.scope['context_incomplete']
    assert reviewer.review.call_count == len(r.split_review_diff(pr_info().diff))


def test_requested_file_followup_can_find_real_defect():
    def review(pr, *args):
        if 'Requested changed-file context at exact head' not in pr.description:
            return vote(r.ReviewVerdict.BORDERLINE, requests=['app.py'])
        return vote(r.ReviewVerdict.FAIL, 'app.py:1 invokes unsafe with unvalidated user input')
    result, _, _ = run(review)
    assert result.verdict == r.ReviewVerdict.FAIL


def test_gate_never_publishes_or_repairs_context_only_rejection(tmp_path):
    pr = pr_info()
    incomplete = vote(r.ReviewVerdict.BORDERLINE)
    incomplete.scope = {'context_incomplete': True}
    with patch.object(gate, 'fetch_pr_info', return_value=pr), \
         patch.object(gate, 'run_all_checks', return_value=[]), \
         patch.object(gate, 'run_review_chain', return_value=(incomplete, [])), \
         patch.object(gate, 'save_verdict') as save:
        result = gate.run_gate('owner', 'repo', 1, KarenConfig(verdict_dir=str(tmp_path)))
    assert result.verdict.status == 'ERROR'
    assert result.verdict_path is None
    save.assert_not_called()


def test_parser_preserves_specific_context_request():
    payload = json.dumps({'verdict': 'BORDERLINE', 'reason': 'Need installer context to finish this review',
                          'requested_files': ['app.py'], 'findings': {}})
    parsed = r.CodexReviewer()._parse_structured_verdict(payload, pr_info(), r.ReviewTier.HIGH)
    assert parsed.requested_files == ['app.py']
    assert parsed.verdict == r.ReviewVerdict.BORDERLINE


def test_small_pass_with_unresolved_context_request_never_approves():
    pr = pr_info()
    pr.diff = 'diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n+new\n'
    primary = Mock(review=Mock(return_value=vote(requests=['unavailable.py'])))
    with patch.object(r, 'get_available_reviewers', return_value=[('codex', primary)]):
        result, _ = r.run_review_chain(pr, KarenConfig())
    assert not result.passed and result.scope['context_incomplete']


def test_absent_implementation_in_chunk_with_citation_is_context_uncertainty():
    result, _, _ = run(lambda *args: vote(r.ReviewVerdict.FAIL,
        'docs.md:1 describes incidents; implementation and tests are absent from this chunk'))
    assert result.verdict == r.ReviewVerdict.BORDERLINE
    assert result.scope['context_incomplete']


def test_actual_missing_context_parameter_is_a_concrete_defect():
    result, _, _ = run(lambda *args: vote(r.ReviewVerdict.FAIL,
        'app.py:1 missing context parameter causes TypeError on every invocation'))
    assert result.verdict == r.ReviewVerdict.FAIL


def test_code_that_looks_like_diff_header_cannot_fabricate_evidence_path():
    pr = pr_info()
    pr.diff = ('diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n'
               '@@ -1 +1,2 @@\n+++ b/fake.py\n+unsafe()\n')
    assert ('app.py', 2) in r._diff_line_locations(pr)
    assert not any(path == 'fake.py' for path, _ in r._diff_line_locations(pr))


def test_missing_authorization_input_remains_a_real_actionable_defect():
    result, _, _ = run(lambda *args: vote(r.ReviewVerdict.FAIL,
        'app.py:1 accepts privileged requests when Authorization is not provided'))
    assert result.verdict == r.ReviewVerdict.FAIL


def test_implementation_not_present_in_chunk_remains_retryable():
    result, _, _ = run(lambda *args: vote(r.ReviewVerdict.FAIL,
        'app.py:1 the implementation is not present in this chunk'))
    assert result.verdict == r.ReviewVerdict.BORDERLINE
    assert result.scope['context_incomplete']


def test_chunk_rubber_stamp_is_retryable_without_concrete_defect():
    def review(*args):
        return r.ReviewResult('codex', r.ReviewVerdict.PASS, 'Approved without any findings')
    result, _, _ = run(review)
    assert result.verdict == r.ReviewVerdict.ERROR
