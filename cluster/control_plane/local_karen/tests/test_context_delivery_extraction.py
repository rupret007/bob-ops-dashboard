"""Context delivery retains unresolved-review authority after extraction."""
from dataclasses import replace
from unittest.mock import Mock
from cluster.control_plane.local_karen import reviewers as r
from cluster.control_plane.local_karen.checks import PRInfo


def test_repeated_request_cannot_grant_pass_or_start_a_third_invocation():
    diff = 'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n'
    pr = PRInfo('owner', 'repo', 1, 'a'*40, 'Context', 'author', False, diff, {}, ['a.py'], [])
    first = r.ReviewResult('codex', r.ReviewVerdict.BORDERLINE,
                          'Missing required context', requested_files=['a.py'])
    invoke = Mock(side_effect=[first, replace(first, verdict=r.ReviewVerdict.PASS)])
    final, _ = r._review_with_requested_context(invoke, pr, pr)
    assert invoke.call_count == 2 and not final.passed
    assert final.verdict == r.ReviewVerdict.BORDERLINE and final.scope['context_incomplete']
