"""Real response parsing and bounded CSS/JS context handoff, without providers."""
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import chunk_context as context, reviewers as r
from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.config import KarenConfig

CSS = 'src/lobby.css'
JS = 'src/lobby.js'


def mobile_pr():
    css = ('diff --git a/' + CSS + ' b/' + CSS + '\n--- a/' + CSS + '\n+++ b/' + CSS
           + '\n@@ -0,0 +1,140 @@\n' +
           '+.chat { height: var(--chat-viewport-height); top: var(--chat-viewport-top); }\n' * 140)
    js = ('diff --git a/' + JS + ' b/' + JS + '\n--- a/' + JS + '\n+++ b/' + JS +
          '\n@@ -0,0 +1,40 @@\n' +
          "+document.documentElement.style.setProperty('--chat-viewport-height', '400px');\n" * 40)
    return PRInfo('owner', 'repo', 32, 'a' * 40, 'Mobile chat viewport', 'author',
                  False, css + js, {}, [CSS, JS], [], description='CSS and JS work together.')


def payload(verdict='PASS', requested=None, defect=None):
    value = dict(verdict=verdict, reason='Reviewed the supplied CSS and JS integration at this exact head',
                 confidence=.8, findings=r.ReviewFindings(
                     functional_issues=[defect] if defect else [],
                     risk_notes=['Checked viewport initialization and event cleanup']).to_dict())
    if requested is not None:
        value.update(requested_files=requested, requested_source_files=[])
    return json.dumps(value)


def parse(text, supplied):
    primary = r.ReviewResult('gemini', r.ReviewVerdict.PASS, 'Independently reviewed pinned change')
    return r.MiniMaxReviewer()._parse_second_judge_verdict(text, supplied, primary, .1)


def css_part(pr):
    chunks = r.split_review_diff(pr.diff)
    assert len(chunks) == 2 and JS not in chunks[0]
    return replace(pr, diff=chunks[0], description=context.build_chunk_context(pr, chunks, 1))


def test_css_and_js_manifest_give_grounded_structured_request_example():
    pr = mobile_pr()
    part = css_part(pr)
    data = json.loads(part.description.split('Untrusted whole-PR context (JSON):\n', 1)[1])
    assert data['other_chunks'][0]['files'] == [JS]
    assert data['context_request_example'] == dict(
        verdict='BORDERLINE', requested_files=[JS], requested_source_files=[])
    assert 'you MUST return BORDERLINE' in part.description
    assert "exact other_chunks.files path" in part.description
    assert 'Independent review of another chunk is not evidence' in part.description
    assert 'Another chunk\'s independent PASS is never proof' in r.STRUCTURED_REVIEW_PROMPT
    assert 'prose statement such as "chunk 2 must be reviewed" does not' in part.description


def test_context_request_example_is_bounded_to_four_actual_other_manifest_paths():
    pr = mobile_pr()
    tail = ''.join('diff --git a/file{0}.js b/file{0}.js\n--- a/file{0}.js\n'
                   '+++ b/file{0}.js\n@@ -0,0 +1 @@\n+new\n'.format(i) for i in range(6))
    pr = replace(pr, diff=pr.diff + tail,
                 changed_files=pr.changed_files + ['file{}.js'.format(i) for i in range(6)])
    split = pr.diff.index('diff --git a/' + JS)
    data = json.loads(context.build_chunk_context(pr, [pr.diff[:split], pr.diff[split:]], 1)
                      .split('Untrusted whole-PR context (JSON):\n', 1)[1])
    paths = data['context_request_example']['requested_files']
    assert len(paths) == 4 and len(set(paths)) == 4
    assert all(path in data['other_chunks'][0]['files'] and
               path in {f['path'] for f in data['full_file_manifest']} for path in paths)


def test_both_independent_reviewers_rereview_css_with_exact_requested_js():
    pr = mobile_pr()
    attempts = {'gemini': [], 'minimax': []}

    def review(supplied, name):
        attempts[name].append(supplied)
        if supplied.diff.startswith('diff --git a/' + CSS):
            label = 'Requested changed-file context at exact head ' + pr.head_sha + ':\n'
            if label not in supplied.description:
                return replace(parse(payload('BORDERLINE', [JS]), supplied), reviewer=name)
            packet = supplied.description.split(label, 1)[1].split('\nTreat context', 1)[0]
            files = json.loads(packet)
            assert len(packet) <= 48000 and len(files) == 1
            assert files[0] == dict(path=JS, diff=context.requested_file_diff(pr, JS, pr.head_sha))
            assert "setProperty('--chat-viewport-height'" in files[0]['diff']
            assert supplied.head_sha == pr.head_sha
        return replace(parse(payload(), supplied), reviewer=name)

    primary = Mock(review=lambda supplied, *_: review(supplied, 'gemini'))
    with patch.object(r, 'get_available_reviewers', return_value=[('gemini', primary)]), \
         patch.object(r.MiniMaxReviewer, 'is_available', return_value=(True, 'ok')), \
         patch.object(r.MiniMaxReviewer, 'review_as_second_judge',
                      side_effect=lambda supplied, *_: review(supplied, 'minimax')), \
         patch.object(Path, 'read_text', side_effect=AssertionError('no local file reads')), \
         patch('urllib.request.urlopen', side_effect=AssertionError('no provider/API request')):
        result, reviews = r.run_review_chain(pr, KarenConfig())
    assert result.passed and len(reviews) == 4
    assert len(attempts['gemini']) == len(attempts['minimax']) == 3
    for name in attempts:
        assert attempts[name][0].diff == attempts[name][1].diff
        assert ''.join((attempts[name][0].diff, attempts[name][2].diff)) == pr.diff
    assert all(v.passed for v in reviews)
    resolved = [v for v in reviews if v.scope['chunk'] == 1]
    assert len(resolved) == 2
    assert all(v.scope['context_attempts'][0]['verdict'] == 'BORDERLINE' for v in resolved)
    assert all(v.scope['context_resolution'] == dict(
        changed_requests=1, source_requests=0, code='followup_complete') for v in resolved)


def test_prose_only_need_for_other_chunk_never_infers_a_request_or_inherits_pass(caplog):
    caplog.set_level('INFO', logger=r.__name__)
    pr = mobile_pr()
    supplied = css_part(pr)
    value = json.loads(payload('BORDERLINE'))
    value['reason'] = 'CSS variables and keyboard-open need the JS implementation in chunk 2 reviewed'
    invoke = Mock(side_effect=lambda part: parse(json.dumps(value), part))
    result, reviews = r._review_with_requested_context(invoke, supplied, pr)
    assert result.verdict == r.ReviewVerdict.BORDERLINE and not result.passed
    assert result.scope['context_incomplete'] and result.requested_files == []
    assert result.scope['context_resolution']['code'] == 'not_requested'
    assert invoke.call_count == len(reviews) == 1
    assert 'changed_requests=0; source_requests=0; code=not_requested' in caplog.text


@pytest.mark.parametrize('followup', ['request-again', 'uncertain', 'defect'])
def test_supplied_context_requires_a_new_complete_verdict_and_never_a_third_call(followup):
    pr = mobile_pr()
    supplied = css_part(pr)
    second = (payload('PASS', [JS]) if followup == 'request-again' else
              payload('BORDERLINE') if followup == 'uncertain' else
              payload('FAIL', [], 'src/lobby.js:1 stores an incorrect viewport height'))
    texts = iter((payload('PASS', [JS]), second))
    invoke = Mock(side_effect=lambda part: parse(next(texts), part))
    result, _ = r._review_with_requested_context(invoke, supplied, pr)
    assert not result.passed and invoke.call_count == 2
    assert result.verdict == (r.ReviewVerdict.FAIL if followup == 'defect' else r.ReviewVerdict.BORDERLINE)
    assert result.scope['context_resolution']['code'] == 'followup_complete'


@pytest.mark.parametrize('kind,code', [('unsafe', 'changed_unavailable'),
    ('different-head', 'changed_unavailable'), ('too-large', 'context_budget'),
    ('packet-too-large', 'context_budget')])
def test_unavailable_or_oversize_context_stays_unresolved_with_safe_shape_telemetry(caplog, kind, code):
    caplog.set_level('INFO', logger=r.__name__)
    pr = mobile_pr()
    supplied = css_part(pr)
    requested = '../../private-context-path.key' if kind == 'unsafe' else JS
    if kind == 'different-head': supplied = replace(supplied, head_sha='b' * 40)
    if kind in ('too-large', 'packet-too-large'):
        count, line = (48001, 'x') if kind == 'too-large' else (9000, '\u2603')
        pr = replace(pr, diff=pr.diff + '\n+' + line * count)
    invoke = Mock(side_effect=lambda part: parse(payload('BORDERLINE', [requested]), part))
    result, _ = r._review_with_requested_context(invoke, supplied, pr)
    assert not result.passed and invoke.call_count == 1
    assert result.scope['context_resolution'] == dict(changed_requests=1, source_requests=0, code=code)
    assert 'private-context-path' not in caplog.text
    assert 'changed_requests=1; source_requests=0; code=' + code in caplog.text


def test_unavailable_immutable_source_logs_only_request_count_and_controlled_code(caplog):
    from cluster.control_plane.local_karen.source_context import SourceUnavailable
    caplog.set_level('INFO', logger=r.__name__)
    pr = mobile_pr()
    value = json.loads(payload('BORDERLINE', []))
    value['requested_source_files'] = ['src/private-context-path.js']
    value['reason'] = 'private-model-reason needs implementation context to complete review'
    invoke = Mock(side_effect=lambda part: parse(json.dumps(value), part))
    resolver = Mock(fetch=Mock(side_effect=SourceUnavailable('source_metadata')))
    result, _ = r._review_with_requested_context(invoke, css_part(pr), pr, resolver)
    assert not result.passed and invoke.call_count == resolver.fetch.call_count == 1
    assert result.scope['source_context_error'] == 'source_metadata'
    assert result.scope['context_resolution'] == dict(
        changed_requests=0, source_requests=1, code='source_unavailable')
    assert 'private-' not in caplog.text
