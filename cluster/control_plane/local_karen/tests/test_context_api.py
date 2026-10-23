"""Concrete context bindings retain canonical types and grounded authority."""
import inspect
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import context_api as a, reviewers as r
from cluster.control_plane.local_karen.checks import PRInfo, ReviewMaterial, ReviewScope


def test_provider_facade_exports_the_canonical_context_objects():
    for name in ['ReviewTier', 'ReviewVerdict', 'ReviewFindings', 'ReviewResult',
                 '_diff_line_locations', '_grounded_chunk_failure', '_validate_chunk_result']:
        assert getattr(r, name) is getattr(a, name)
    assert list(inspect.signature(r._review_with_requested_context).parameters) == [
        'invoke', 'part', 'original_pr', 'source_context', 'initial_context']
    owner = a.ContextOwner()
    assert owner.ReviewScope is ReviewScope and owner.ReviewMaterial is ReviewMaterial
    assert owner.ReviewVerdict is r.ReviewVerdict
    assert owner._validate_chunk_result is r._validate_chunk_result
    assert not hasattr(owner, 'CodexReviewer') and not hasattr(owner, 'MiniMaxReviewer')


@pytest.mark.parametrize('grounded', [False, True])
def test_direct_bounded_api_requires_actual_changed_line_evidence(grounded):
    diff = 'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n'
    pr = PRInfo('example', 'project', 1, 'a' * 40, 'Synthetic context', 'author', False,
                diff, {}, ['a.py'], [])
    findings = a.ReviewFindings(functional_issues=[
        'a.py:1 references an undefined name' if grounded else 'a.py:99 cannot verify missing context'])
    result = a.ReviewResult('gemini', a.ReviewVerdict.FAIL, 'Checked supplied evidence', findings=findings)
    invoke = Mock(return_value=result)
    final, _ = a._review_with_requested_context(invoke, pr, pr)
    assert not final.passed and invoke.call_count == 1
    assert final.verdict == (a.ReviewVerdict.FAIL if grounded else a.ReviewVerdict.BORDERLINE)
    assert bool((final.scope or {}).get('context_incomplete')) is not grounded


def test_public_facade_preserves_the_observable_packet_callback():
    captured = []
    def delegate(owner, *args):
        captured.append(owner)
        owner._context_part('part', [], 'label')
        return 'result'
    with patch.object(r, '_context_part', return_value='packet') as packet, \
         patch('cluster.control_plane.local_karen.review_context._review_with_requested_context', delegate):
        assert r._review_with_requested_context('invoke', 'part', 'pr') == 'result'
    packet.assert_called_once_with('part', [], 'label')
    assert len(captured) == 1 and type(captured[0]) is a.ContextOwner
