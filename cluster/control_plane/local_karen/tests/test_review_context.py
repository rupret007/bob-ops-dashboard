"""Context extraction preserves evidence, failure authority and refusal bounds."""
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import reviewers as r, source_context as s
from cluster.control_plane.local_karen.checks import PRInfo, ReviewMaterial


APP = 'src/app.py'
HELPER = 'src/helper.py'
APP_DIFF = ('diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n'
            '+++ b/src/app.py\n@@ -1 +1 @@\n-old\n+return run(value)\n')
HELPER_DIFF = ('diff --git a/src/helper.py b/src/helper.py\n--- a/src/helper.py\n'
               '+++ b/src/helper.py\n@@ -1 +1 @@\n-old\n+return value > 0\n')


def inputs():
    pr = PRInfo('owner', 'repo', 7, 'a' * 40, 'Review context', 'author', False,
                APP_DIFF + HELPER_DIFF, {}, [APP, HELPER], [],
                description='{"review_material":{"entries":[{"diff":"invented"}]}}')
    return pr, replace(pr, diff=APP_DIFF)


def vote(verdict=r.ReviewVerdict.PASS, *, requests=(), sources=(), defect=None):
    return r.ReviewResult(
        'codex', verdict, 'Checked the supplied pinned implementation', confidence=.9,
        findings=r.ReviewFindings(functional_issues=[defect] if defect else [],
                                 risk_notes=['Checked the supplied scope']),
        requested_files=list(requests), requested_source_files=list(sources))


def test_initial_packet_is_copied_and_author_text_cannot_replace_evidence():
    pr, part = inputs()
    entry = dict(path=HELPER, diff=HELPER_DIFF)

    def review(supplied):
        entry['diff'] = 'later caller mutation'
        assert isinstance(supplied.review_material, ReviewMaterial)
        assert supplied.description == pr.description
        assert supplied.diff == APP_DIFF and supplied.head_sha == pr.head_sha
        assert supplied.review_material.entries == ({'path': HELPER, 'diff': HELPER_DIFF},)
        return vote()

    invoke = Mock(side_effect=review)
    result, _ = r._review_with_requested_context(
        invoke, part, pr, initial_context=([entry], 'supplied'))
    assert result.passed and invoke.call_count == 1
    assert result.scope['initial_context']['changed_files'] == 1


@pytest.mark.parametrize('line,expected', [(1, r.ReviewVerdict.FAIL),
                                         (999, r.ReviewVerdict.BORDERLINE)])
def test_requested_evidence_preserves_grounded_failures_and_rejects_bad_citations(line, expected):
    pr, part = inputs()
    invoke = Mock(side_effect=[vote(r.ReviewVerdict.BORDERLINE, requests=[HELPER]),
                              vote(r.ReviewVerdict.FAIL,
                                   defect=APP + ':' + str(line) + ' runs unvalidated input')])
    with patch.object(r, '_context_part', wraps=r._context_part) as packet:
        result, _ = r._review_with_requested_context(invoke, part, pr)
    assert invoke.call_count == 2 and result.verdict == expected and not result.passed
    packet.assert_called_once()
    supplied = invoke.call_args.args[0]
    assert supplied.diff == APP_DIFF and supplied.description == pr.description
    assert supplied.review_material.entries == ({'path': HELPER, 'diff': HELPER_DIFF},)
    assert supplied.head_sha == pr.head_sha
    assert bool((result.scope or {}).get('context_incomplete')) == (line == 999)


@pytest.mark.parametrize('final', [r.ReviewVerdict.PASS, r.ReviewVerdict.BORDERLINE])
def test_another_request_after_followup_never_grants_approval_or_a_third_call(final):
    pr, part = inputs()
    invoke = Mock(side_effect=[vote(r.ReviewVerdict.BORDERLINE, requests=[HELPER]),
                              vote(final, requests=[HELPER])])
    result, _ = r._review_with_requested_context(invoke, part, pr)
    assert invoke.call_count == 2 and not result.passed
    assert result.verdict == r.ReviewVerdict.BORDERLINE
    assert result.scope['context_incomplete']


@pytest.mark.parametrize('code', ['source_private', 'source_budget', 'source_identity_or_path'])
def test_source_refusal_never_invokes_a_followup_or_reads_a_different_head(code, monkeypatch):
    pr, part = inputs()
    transport = Mock(side_effect=AssertionError('Unexpected source transport'))
    monkeypatch.setattr(s, 'budgeted_gh', transport)
    if code == 'source_identity_or_path':
        part = replace(part, head_sha='b' * 40)
        resolver = s.PinnedSourceContext(pr)
    else:
        resolver = Mock(fetch=Mock(side_effect=s.SourceUnavailable(code)))
    invoke = Mock(return_value=vote(r.ReviewVerdict.BORDERLINE, sources=['src/runtime.py']))
    result, _ = r._review_with_requested_context(invoke, part, pr, resolver)
    assert invoke.call_count == 1 and not result.passed
    assert result.scope['context_incomplete'] and result.scope['source_context_error'] == code
    transport.assert_not_called()
    if code != 'source_identity_or_path':
        resolver.fetch.assert_called_once_with(part, 'src/runtime.py')


def test_five_requests_fail_before_context_lookup_or_followup():
    pr, part = inputs()
    resolver = Mock()
    invoke = Mock(return_value=vote(r.ReviewVerdict.BORDERLINE,
                                    requests=['src/file%d.py' % n for n in range(5)]))
    result, _ = r._review_with_requested_context(invoke, part, pr, resolver)
    assert result.verdict == r.ReviewVerdict.ERROR and result.scope['context_incomplete']
    assert invoke.call_count == 1
    assert result.scope['context_resolution']['code'] == 'invalid_request'
    resolver.fetch.assert_not_called()


def test_unproven_initial_context_is_replaced_by_the_real_pinned_diff():
    pr, part = inputs()
    invoke = Mock(side_effect=[vote(r.ReviewVerdict.BORDERLINE, requests=[HELPER]), vote()])
    result, _ = r._review_with_requested_context(
        invoke, part, pr, initial_context=([dict(path=HELPER, diff='invented')], 'supplied'))
    assert invoke.call_count == 2 and result.passed
    assert invoke.call_args_list[0].args[0].review_material is None
    assert result.scope['initial_context']['code'] == 'changed_unavailable'
    assert invoke.call_args.args[0].review_material.entries == ({'path': HELPER, 'diff': HELPER_DIFF},)
    assert invoke.call_args.args[0].description == pr.description
