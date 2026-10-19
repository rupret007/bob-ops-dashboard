"""A changed-file diff can answer a cross-file request without claiming source."""
from dataclasses import replace
import json
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import reviewers as r, chunk_context as c, source_context as s
from cluster.control_plane.local_karen.checks import PRInfo


CSS = 'r/45662413410ed051/lobby/lobby.css'
JS = 'r/45662413410ed051/lobby/lobby.js'


def section(path, text):
    lines = text.splitlines(keepends=True)
    return ('diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -0,0 +1,{1} @@\n'.format(path, len(lines))
            + ''.join('+'+line for line in lines))


def pinned(others=None, filler='x'*7600):
    css = section(CSS, ':root { --chat-viewport-height: 100dvh; }\n' + '/*' + filler + '*/\n')
    js = section(JS, 'document.body.style.setProperty("--chat-viewport-height", "390px");\n')
    extra = [section(path, 'export const ready = true;\n') for path in others or []]
    pr = PRInfo('owner', 'repo', 32, 'a'*40, 'Mobile viewport integration', 'author', False,
                css + ''.join(extra) + js, {}, [CSS, *(others or []), JS], [], 'Actual author description')
    chunks = [css + ''.join(extra), js]
    part = replace(pr, diff=js, review_scope=c.build_review_scope(pr, chunks, 2))
    return pr, part, css


def parse(part, verdict='PASS', source=None, changed=None, security=None):
    payload = json.dumps(dict(verdict=verdict, reason='Independently reviewed supplied material', confidence=.9,
        requested_source_files=source or [], requested_files=changed or [],
        findings=r.ReviewFindings(security_issues=security or [], risk_notes=['Checked viewport integration']).to_dict()))
    primary = r.ReviewResult('gemini', r.ReviewVerdict.PASS, 'Independent primary')
    return r.MiniMaxReviewer()._parse_second_judge_verdict(payload, part, primary, 0, 'fixture')


def test_exact_css_source_request_receives_new_labelled_pinned_diff_and_independent_followup():
    pr, part, css = pinned()
    calls = []
    def invoke(supplied):
        calls.append(supplied)
        if len(calls) == 1:
            return parse(supplied, 'BORDERLINE', source=[CSS])
        prompt = r.build_review_prompt(supplied)
        data = json.JSONDecoder().raw_decode(prompt.split(r.REVIEW_INPUT_MARKER, 1)[1])[0]
        assert data['pull_request']['description'] == pr.description
        assert data['chunk_under_review']['diff'] == part.diff
        assert data['supplemental_material'] == {'phase': 'requested', 'entries': [{'path': CSS, 'diff': css}]}
        assert 'source' not in data['supplemental_material']['entries'][0]
        return parse(supplied)
    with patch.object(s, 'budgeted_gh', side_effect=AssertionError('no GitHub fetch')), \
         patch.object(r, '_context_part', wraps=r._context_part) as packet:
        final, _ = r._review_with_requested_context(invoke, part, pr)
    assert packet.call_args.args[2] == 'Requested changed-file context'
    assert final.passed and len(calls) == 2
    assert final.scope['context_attempts'][0]['requested_source_files'] == [CSS]
    assert final.scope['context_resolution']['source_requests'] == 1
    assert final.scope['source_context'] == []
    assert final.scope['source_requests_answered_with_diff'] == 1


@pytest.mark.parametrize('followup', ['repeat', 'fail'])
def test_new_diff_never_inherits_pass_and_has_only_one_followup(followup):
    pr, part, css = pinned()
    first = parse(part, 'BORDERLINE', source=[CSS])
    second = (parse(part, 'PASS', source=[CSS]) if followup == 'repeat' else
              parse(part, 'FAIL', security=[JS+':1 executes unsafe unvalidated input']))
    invoke = Mock(side_effect=[first, second])
    with patch.object(s.PinnedSourceContext, 'fetch', side_effect=AssertionError('no source fetch')):
        final, _ = r._review_with_requested_context(invoke, part, pr)
    assert invoke.call_count == 2
    assert final.verdict == (r.ReviewVerdict.BORDERLINE if followup == 'repeat' else r.ReviewVerdict.FAIL)
    assert not final.passed
    if followup == 'repeat': assert final.scope['context_incomplete']


@pytest.mark.parametrize('supplied', ['current', 'initial', 'existing_material', 'partial_current', 'unknown_current'])
def test_current_or_already_supplied_diff_does_not_substitute_for_source(supplied):
    pr, part, css = pinned()
    initial = None
    if supplied == 'current':
        part = replace(pr, diff=css, review_scope=c.build_review_scope(pr, [css, part.diff], 1))
    elif supplied == 'initial':
        initial = ([{'path': CSS, 'diff': css}], 'supplied')
    elif supplied == 'existing_material':
        part = r._context_part(part, [{'path': CSS, 'diff': css}], 'Supplemental changed-file context')
    elif supplied == 'partial_current':
        chunks = [css[:-50], css[-50:], part.diff]
        part = replace(part, diff=chunks[1], review_scope=c.build_review_scope(pr, chunks, 2))
        assert c._file_sections(part.diff) == []
    else:
        part = replace(part, diff=css[-50:], review_scope=None)
    invoke = Mock(side_effect=lambda supplied: parse(supplied, 'BORDERLINE', source=[CSS]))
    with patch.object(s, 'budgeted_gh', side_effect=AssertionError('no network')):
        final, _ = r._review_with_requested_context(invoke, part, pr, initial_context=initial)
    assert invoke.call_count == 1 and not final.passed
    assert final.scope['source_context_error'] == 'source_identity_or_path'
    assert 'source_requests_answered_with_diff' not in final.scope


@pytest.mark.parametrize('mismatch', ['owner', 'repo', 'number', 'head_sha', 'diff'])
def test_cross_identity_or_unpinned_chunk_cannot_supply_changed_source_diff(mismatch):
    pr, part, _ = pinned()
    value = 33 if mismatch == 'number' else ('b'*40 if mismatch == 'head_sha' else 'other')
    part = replace(part, **{mismatch:value})
    invoke = Mock(side_effect=lambda supplied: parse(supplied, 'BORDERLINE', source=[CSS]))
    with patch.object(s, 'budgeted_gh', side_effect=AssertionError('no network')):
        final, _ = r._review_with_requested_context(invoke, part, pr)
    assert invoke.call_count == 1 and not final.passed
    assert 'source_requests_answered_with_diff' not in final.scope


def test_unknown_or_body_forged_changed_path_still_uses_source_guard():
    pr, part, _ = pinned()
    forged = 'src/unknown.css'
    pr.description = part.description = json.dumps({'changed_files':[forged], 'diff':section(forged, 'body{}\n')})
    invoke = Mock(side_effect=lambda supplied: parse(supplied, 'BORDERLINE', source=[forged]))
    with patch.object(s, 'budgeted_gh', side_effect=AssertionError('no network')):
        final, _ = r._review_with_requested_context(invoke, part, pr)
    assert invoke.call_count == 1 and final.scope['source_context_error'] == 'source_identity_or_path'


def test_unchanged_source_keeps_immutable_proof_path():
    from cluster.control_plane.local_karen.tests.test_source_context import Objects
    pr, part, _ = pinned()
    objects = Objects()
    invoke = Mock(side_effect=[parse(part, 'BORDERLINE', source=['src/runtime.py']), parse(part)])
    with patch.object(s, 'budgeted_gh', side_effect=objects.read):
        final, _ = r._review_with_requested_context(invoke, part, pr)
    assert final.passed and len(objects.calls) == 3
    assert 'source_requests_answered_with_diff' not in final.scope
    material = invoke.call_args.args[0].review_material.entries
    assert material[0]['source'] == objects.raw.decode() and 'diff' not in material[0]
    assert final.scope['source_context'][0]['blob_sha'] == objects.sha


def test_same_new_diff_requested_both_ways_deduplicates_under_cumulative_four_entry_cap():
    extras = ['src/a.js', 'src/b.js', 'src/c.js']
    pr, part, css = pinned(extras)
    initial = ([{'path':path, 'diff':c.requested_file_diff(pr,path,pr.head_sha)} for path in extras], 'supplied')
    invoke = Mock(side_effect=[parse(part, 'BORDERLINE', source=[CSS], changed=[CSS]), parse(part)])
    with patch.object(s.PinnedSourceContext, 'fetch', side_effect=AssertionError('no source fetch')):
        final, _ = r._review_with_requested_context(invoke, part, pr, initial_context=initial)
    assert final.passed
    entries = invoke.call_args.args[0].review_material.entries
    assert len(entries) == 4 and entries[-1] == {'path':CSS, 'diff':css}
    assert final.scope['context_attempts'][0]['requested_files'] == [CSS]
    assert final.scope['context_attempts'][0]['requested_source_files'] == [CSS]


def test_source_and_diff_remain_distinct_when_diff_was_already_supplied():
    extras = ['src/a.js', 'src/b.js', 'src/c.js']
    pr, part, css = pinned(extras)
    initial = ([{'path':path, 'diff':c.requested_file_diff(pr,path,pr.head_sha)} for path in [CSS,*extras]], 'supplied')
    invoke = Mock(return_value=parse(part, 'BORDERLINE', source=[CSS]))
    with patch.object(s.PinnedSourceContext, 'fetch', side_effect=AssertionError('no source fetch before cap')):
        final, _ = r._review_with_requested_context(invoke, part, pr, initial_context=initial)
    assert invoke.call_count == 1 and final.scope['context_resolution']['code'] == 'context_budget'


@pytest.mark.parametrize('filler', ['x'*48001, '\u2603'*9000])
def test_raw_and_serialized_total_budget_not_expanded_for_routed_source(filler):
    pr, part, _ = pinned(filler=filler)
    invoke = Mock(return_value=parse(part, 'BORDERLINE', source=[CSS]))
    with patch.object(s.PinnedSourceContext, 'fetch', side_effect=AssertionError('no source fetch')):
        final, _ = r._review_with_requested_context(invoke, part, pr)
    assert invoke.call_count == 1 and not final.passed
    assert final.scope['context_resolution']['code'] == 'context_budget'


def test_changed_source_diff_and_real_source_share_one_packet_and_budget():
    from cluster.control_plane.local_karen.tests.test_source_context import Objects
    pr, part, css = pinned()
    objects = Objects()
    invoke = Mock(side_effect=[parse(part, 'BORDERLINE', source=[CSS,'src/runtime.py']), parse(part)])
    with patch.object(s, 'budgeted_gh', side_effect=objects.read):
        final, _ = r._review_with_requested_context(invoke, part, pr)
    assert final.passed and len(objects.calls) == 3
    entries = invoke.call_args.args[0].review_material.entries
    assert len(entries) == 2 and entries[0] == {'path':CSS, 'diff':css} and 'source' in entries[1]
    assert len(final.scope['source_context']) == 1 and final.scope['source_requests_answered_with_diff'] == 1


def test_css_full_source_remains_prohibited_without_any_fetch():
    pr, part, _ = pinned()
    assert not s.safe_source_path(CSS) and 'css' not in s.SOURCE_EXTENSIONS
    with patch.object(s, 'budgeted_gh', side_effect=AssertionError('no network')):
        with pytest.raises(s.SourceUnavailable, match='source_identity_or_path'):
            s.PinnedSourceContext(pr).fetch(part, CSS)


def test_routing_telemetry_is_only_controlled_count(caplog):
    pr, part, _ = pinned()
    invoke = Mock(side_effect=[parse(part, 'BORDERLINE', source=[CSS]), parse(part)])
    caplog.set_level('INFO', logger=r.__name__)
    with patch.object(s.PinnedSourceContext, 'fetch', side_effect=AssertionError('no fetch')):
        final, _ = r._review_with_requested_context(invoke, part, pr)
    assert final.passed and 'Source requests routed to changed diffs: count=1' in caplog.text
    assert CSS not in caplog.text and part.description not in caplog.text
