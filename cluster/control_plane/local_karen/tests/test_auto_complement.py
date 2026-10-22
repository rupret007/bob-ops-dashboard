"""Pinned automatic context adds evidence, never authority or a second budget."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import chunk_context as c, reviewers as r
from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.config import KarenConfig


CSS, JS, TEST = 'src/chat.css', 'src/chat.js', 'tests/chat.spec.js'


def section(path, size=200, character='x'):
    header = 'diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -0,0 +1 @@\n+'.format(path)
    return header + character * (size-len(header)-1) + '\n'


def pr_info(sections):
    return PRInfo('owner', 'repo', 32, 'a'*40, 'Mobile chat', 'author', False,
                  ''.join(sections), {}, [s['path'] for s in c._file_sections(''.join(sections))], [],
                  description='The original PR body describes this pinned integration.')


def mobile_pr():
    metadata = [section(path, size) for path, size in [
        ('runner.js', 381), ('docs/screens.md', 1762), ('screens/a.png', 200),
        ('screens/b.png', 200), ('screens/c.png', 242), ('screens/d.png', 242),
        ('src/index.html', 994)]]
    return pr_info(metadata + [section(CSS, 7858), section(JS, 4032), section(TEST, 7069)])


def vote(verdict=r.ReviewVerdict.PASS, changed=None, source=None):
    return r.ReviewResult('gemini', verdict, 'Independently checked supplied integration evidence',
        findings=r.ReviewFindings(risk_notes=['Checked supplied implementation and tests']),
        requested_files=changed or [], requested_source_files=source or [])


def packet(part, phase):
    if part.review_material is None or part.review_material.phase != phase:
        return []
    return list(part.review_material.entries)


def test_css_receives_js_and_tests_before_either_judge_without_repacking():
    pr = mobile_pr()
    chunks = r.split_review_diff(pr.diff)
    assert list(map(len, chunks)) == [11879, 11101]
    attempts = {'gemini': [], 'minimax': []}
    def review(part, name):
        attempts[name].append(part)
        assert part.description == pr.description
        supplied = packet(part, 'supplemental') or packet(part, 'requested')
        paths = [item['path'] for item in supplied]
        if CSS in part.diff:
            # Old flow requests JS first, then still needs tests on its only
            # follow-up. Automatic complement supplies both before the first vote.
            if JS not in paths:
                return vote(r.ReviewVerdict.BORDERLINE, changed=[JS])
            if TEST not in paths:
                return vote(r.ReviewVerdict.BORDERLINE, changed=[TEST])
            assert paths == [JS, TEST]
        elif CSS not in paths:
            return vote(r.ReviewVerdict.BORDERLINE, changed=[CSS])
        assert part.head_sha == pr.head_sha
        return replace(vote(), reviewer=name)
    primary = Mock(review=lambda part, *_: review(part, 'gemini'))
    with patch.object(r, 'get_available_reviewers', return_value=[('gemini', primary)]), \
         patch.object(r.MiniMaxReviewer, 'is_available', return_value=(True, 'ok')), \
         patch.object(r.MiniMaxReviewer, 'review_as_second_judge',
                      side_effect=lambda part, *_: review(part, 'minimax')), \
         patch.object(Path, 'read_text', side_effect=AssertionError('no checkout reads')), \
         patch('urllib.request.urlopen', side_effect=AssertionError('no API/model calls')), \
         patch('cluster.control_plane.local_karen.source_context.PinnedSourceContext.fetch',
               side_effect=AssertionError('no automatic source fetch')):
        result, reviews = r.run_review_chain(pr, KarenConfig())
    assert result.passed and len(reviews) == 4
    assert len(attempts['gemini']) == len(attempts['minimax']) == 3
    for calls in attempts.values():
        assert ''.join((calls[0].diff, calls[1].diff)) == pr.diff
        assert calls[1].diff == calls[2].diff
        assert all(len(part.diff) <= r.REVIEW_DIFF_LIMIT for part in calls)
    assert attempts['gemini'][0].description == attempts['minimax'][0].description == pr.description
    first = [review for review in reviews if review.scope['chunk'] == 1]
    assert all(review.scope['initial_context']['changed_files'] == 2 for review in first)
    assert all('context_attempts' not in review.scope for review in first)
    assert all(review.scope['head_sha'] == pr.head_sha for review in reviews)


def test_partial_file_chunks_defer_to_normal_requests_and_keep_original_plan():
    pr = pr_info([section('large.js', 20000, '\u2603'), section('tail.js')])
    # This full file exceeds the serialized budget; keep the original fallback.
    full = c.requested_file_diff(pr, 'large.js', pr.head_sha)
    assert c.context_size([dict(path='large.js', diff=full)])[1] > c.MAX_CONTEXT_SIZE
    chunks = r.split_review_diff(pr.diff)
    initial, code = c.automatic_complement(pr, chunks, 1)
    assert code == 'partial_file' and initial == []
    assert len(c.requested_file_diff(pr, 'large.js', pr.head_sha)) == 20000
    assert ''.join(chunks) == pr.diff and all(len(chunk) <= 12000 for chunk in chunks)


@pytest.mark.parametrize('kind', ['paths', 'raw', 'serialized', 'missing', 'unsafe'])
def test_unavailable_or_overbound_complement_falls_back_all_or_none(kind):
    current = section('current.js')
    others = [section('other.js')]
    if kind == 'paths': others = [section('other{}.js'.format(i)) for i in range(5)]
    if kind == 'raw': others = [section('other.js', 48001)]
    if kind == 'serialized': others = [section('other.js', 9000, '\u2603')]
    if kind == 'unsafe': others = [section('../outside.js')]
    pr = pr_info([current, *others])
    if kind == 'missing': pr.changed_files.append('missing.js')
    initial, code = c.automatic_complement(pr, [current, ''.join(others)], 1)
    assert initial == []
    assert code == ('changed_unavailable' if kind in ('missing', 'unsafe') else 'context_budget')


def test_repeated_file_sections_are_one_complete_context_entry_and_binary_never_fetches():
    current = section('current.js')
    binary = 'diff --git a/image.png b/image.png\nBinary files a/image.png and b/image.png differ\n'
    repeated = section('other.js') + section('other.js')
    pr = pr_info([current, binary, repeated])
    with patch.object(Path, 'read_text', side_effect=AssertionError('no file reads')):
        initial, code = c.automatic_complement(pr, [current, binary+repeated], 1)
    assert code == 'supplied' and len(initial) == 2
    assert initial[0] == dict(path='image.png', diff=binary)
    assert initial[1] == dict(path='other.js', diff=repeated)


@pytest.mark.parametrize('kind', ['head', 'plan', 'index'])
def test_automatic_context_requires_full_exact_identity_and_byte_complete_plan(kind):
    pr = pr_info([section('a.js'), section('b.js')]); chunks = [pr.diff]
    index = 1
    if kind == 'head': pr.head_sha = 'unknown'
    if kind == 'plan': chunks = [pr.diff[:-1]]
    if kind == 'index': index = 0
    with pytest.raises(ValueError, match='complete exact-head'):
        c.automatic_complement(pr, chunks, index)


def setup_direct(others=2, size=200):
    sections = [section('current.js')] + [section('extra{}.js'.format(i), size) for i in range(others)]
    pr = pr_info(sections)
    chunks = [sections[0], ''.join(sections[1:])]
    part = replace(pr, diff=chunks[0], review_scope=c.build_review_scope(pr, chunks, 1))
    return pr, part, c.automatic_complement(pr, chunks, 1)


@pytest.mark.parametrize('mismatch', ['head', 'owner', 'repo', 'number', 'diff'])
def test_initial_packet_cannot_cross_identity_or_supply_different_diff(mismatch):
    pr, part, initial = setup_direct()
    if mismatch == 'diff': initial[0][0]['diff'] += 'untrusted other revision'
    else: part = replace(part, **{mismatch if mismatch != 'head' else 'head_sha':
                                 'b'*40 if mismatch == 'head' else ('other' if mismatch != 'number' else 33)})
    invoke = Mock(return_value=vote(r.ReviewVerdict.BORDERLINE))
    result, _ = r._review_with_requested_context(invoke, part, pr, initial_context=initial)
    assert not packet(invoke.call_args.args[0], 'supplemental')
    assert invoke.call_args.args[0].description == pr.description
    assert not result.passed and result.scope['initial_context']['code'] == 'changed_unavailable'


def test_auto_and_explicit_context_share_unique_entry_limit_before_source_fetch():
    pr, part, initial = setup_direct(others=3)
    resolver = Mock()
    invoke = Mock(return_value=vote(r.ReviewVerdict.BORDERLINE, source=['src/a.js', 'src/b.js']))
    result, _ = r._review_with_requested_context(invoke, part, pr, resolver, initial)
    assert result.scope['context_resolution']['code'] == 'context_budget'
    assert not result.passed and invoke.call_count == 1
    resolver.fetch.assert_not_called()


def test_union_deduplicates_overlaps_and_replaces_initial_packet_for_one_followup():
    pr, part, initial = setup_direct()
    resolver = Mock(fetch=Mock(return_value=dict(path='src/runtime.js', head_sha=pr.head_sha,
                                                source='export const ready = true;')))
    request = vote(r.ReviewVerdict.BORDERLINE, changed=['extra0.js', 'extra0.js'],
                   source=['src/runtime.js', 'src/runtime.js'])
    invoke = Mock(side_effect=[request, vote()])
    result, _ = r._review_with_requested_context(invoke, part, pr, resolver, initial)
    assert result.passed and invoke.call_count == 2 and resolver.fetch.call_count == 1
    first, second = [call.args[0] for call in invoke.call_args_list]
    assert packet(first, 'supplemental') == initial[0]
    combined = packet(second, 'requested')
    assert len(combined) == 3 and [item['path'] for item in combined] == [
        'extra0.js', 'extra1.js', 'src/runtime.js']
    assert first.description == second.description == pr.description
    assert second.review_material.phase == 'requested'
    assert max(c.context_size(combined)) <= 48000
    assert all('source' not in item for item in result.scope['source_context'])


def test_two_automatic_and_two_explicit_source_entries_fit_exact_joint_limit():
    pr, part, initial = setup_direct()
    resolver = Mock(fetch=Mock(side_effect=lambda supplied, path:
        dict(path=path, head_sha=supplied.head_sha, source='export const ready = true;')))
    invoke = Mock(side_effect=[vote(r.ReviewVerdict.BORDERLINE, source=['src/a.js', 'src/b.js']), vote()])
    result, _ = r._review_with_requested_context(invoke, part, pr, resolver, initial)
    combined = packet(invoke.call_args.args[0], 'requested')
    assert result.passed and invoke.call_count == 2 and resolver.fetch.call_count == 2
    assert len(combined) == 4 and [item['path'] for item in combined] == [
        'extra0.js', 'extra1.js', 'src/a.js', 'src/b.js']


def test_automatic_evidence_never_substitutes_for_required_minimax_vote():
    pr = mobile_pr()
    primary = Mock(review=Mock(return_value=vote()))
    with patch.object(r, 'get_available_reviewers', return_value=[('gemini', primary)]), \
         patch.object(r.MiniMaxReviewer, 'is_available', return_value=(True, 'ok')), \
         patch.object(r.MiniMaxReviewer, 'review_as_second_judge',
                      return_value=replace(vote(r.ReviewVerdict.BORDERLINE), reviewer='minimax')) as second:
        result, reviews = r.run_review_chain(pr, KarenConfig())
    assert not result.passed and second.call_count == primary.review.call_count == 2
    assert packet(second.call_args_list[0].args[0], 'supplemental')
    assert any(review.reviewer == 'minimax' and not review.passed for review in reviews)


@pytest.mark.parametrize('kind', ['changed', 'source', 'serialized'])
def test_combined_raw_and_serialized_budget_never_gets_a_second_packet(kind):
    current = section('current.js', 6000)
    other = section('extra.js', 43000)
    pr = pr_info([current, other]); chunks = [current, other]
    part = replace(pr, diff=current)
    initial = c.automatic_complement(pr, chunks, 1)
    assert initial[1] == 'supplied'
    resolver = Mock(fetch=Mock(return_value=dict(path='src/runtime.js', head_sha=pr.head_sha,
        source=('y'*6000 if kind == 'source' else '\u2603'*2000))))
    request = (vote(r.ReviewVerdict.BORDERLINE, changed=['current.js']) if kind == 'changed' else
               vote(r.ReviewVerdict.BORDERLINE, source=['src/runtime.js']))
    invoke = Mock(return_value=request)
    result, _ = r._review_with_requested_context(invoke, part, pr, resolver, initial)
    assert not result.passed and invoke.call_count == 1
    assert result.scope['context_resolution']['code'] in ('context_budget', 'source_unavailable')


@pytest.mark.parametrize('verdict', [r.ReviewVerdict.BORDERLINE, r.ReviewVerdict.ERROR, r.ReviewVerdict.FAIL])
def test_initial_evidence_never_changes_a_rejecting_vote_or_infers_pass(verdict):
    pr, part, initial = setup_direct()
    rejected = vote(verdict)
    if verdict == r.ReviewVerdict.FAIL:
        rejected.findings.functional_issues = ['current.js:1 changes a required value incorrectly']
    invoke = Mock(return_value=rejected)
    result, _ = r._review_with_requested_context(invoke, part, pr, initial_context=initial)
    assert not result.passed and result.verdict == verdict and invoke.call_count == 1


def test_requested_context_again_cannot_start_a_third_call_and_logs_only_safe_shapes(caplog):
    caplog.set_level('INFO', logger=r.__name__)
    pr, part, initial = setup_direct()
    pr.description = 'private model body'
    part = replace(part, description=pr.description)
    invoke = Mock(return_value=vote(r.ReviewVerdict.BORDERLINE, changed=['extra0.js']))
    result, _ = r._review_with_requested_context(invoke, part, pr, initial_context=initial)
    assert not result.passed and invoke.call_count == 2
    assert result.scope['context_resolution']['code'] == 'followup_complete'
    assert 'Initial review context: code=supplied; changed_files=2' in caplog.text
    assert 'Review context: changed_requests=1; source_requests=0' in caplog.text
    assert 'extra0.js' not in caplog.text and 'private model body' not in caplog.text


@pytest.mark.parametrize('size', [12345, 20000])
def test_each_partial_fragment_gets_the_complete_file_not_a_fragment(size):
    pr = pr_info([section('large.js', size), section('tail.js')])
    chunks = r.split_review_diff(pr.diff)
    with patch.object(Path, 'read_text', side_effect=AssertionError('no checkout reads')), \
         patch('urllib.request.urlopen', side_effect=AssertionError('no network')):
        for index, chunk in enumerate(chunks, 1):
            initial, code = c.automatic_complement(pr, chunks, index)
            if 'large.js' in c.build_review_scope(pr, chunks, index).data['current_chunk']['files']:
                assert code == 'partial_file_supplied'
                assert initial == [dict(path='large.js', diff=pr.diff[:size])]
                assert len(initial) <= c.MAX_CONTEXT_FILES
                assert max(c.context_size(initial)) <= c.MAX_CONTEXT_SIZE
    assert ''.join(chunks) == pr.diff and all(len(chunk) <= r.REVIEW_DIFF_LIMIT for chunk in chunks)


def test_partial_seed_leaves_the_existing_followup_for_integration_context():
    pr = pr_info([section('large.js', 20000), section('tail.js')])
    chunks = r.split_review_diff(pr.diff)
    part = replace(pr, diff=chunks[0], review_scope=c.build_review_scope(pr, chunks, 1))
    initial = c.automatic_complement(pr, chunks, 1)
    calls = {'gemini': [], 'minimax': []}
    def review(supplied, name):
        calls[name].append(supplied)
        entries = packet(supplied, 'supplemental') or packet(supplied, 'requested')
        paths = [entry['path'] for entry in entries]
        for needed in ('large.js', 'tail.js'):
            if needed not in paths:
                return replace(vote(r.ReviewVerdict.BORDERLINE, changed=[needed]), reviewer=name)
        assert entries[0]['diff'] == c.requested_file_diff(pr, 'large.js', pr.head_sha)
        return replace(vote(), reviewer=name)
    primary = Mock(review=lambda supplied, *_: review(supplied, 'gemini'))
    with patch.object(r, 'get_available_reviewers', return_value=[('gemini', primary)]), \
         patch.object(r.MiniMaxReviewer, 'is_available', return_value=(True, 'ok')), \
         patch.object(r.MiniMaxReviewer, 'review_as_second_judge',
                      side_effect=lambda supplied, *_: review(supplied, 'minimax')):
        result, reviews = r._run_review_chain_single(part, KarenConfig(), context_pr=pr,
                                                     initial_context=initial)
    assert result.passed and len(reviews) == 2
    assert len(calls['gemini']) == len(calls['minimax']) == 2
    assert all(call.diff == part.diff and call.head_sha == pr.head_sha
               for attempts in calls.values() for call in attempts)


@pytest.mark.parametrize('kind', ['raw', 'serialized', 'unknown', 'unsafe'])
def test_partial_seed_keeps_existing_bounds_and_unknown_input_refusal(kind):
    pr = pr_info([section('../outside.js' if kind == 'unsafe' else 'large.js',
                          48001 if kind == 'raw' else 13000,
                          '\u2603' if kind == 'serialized' else 'x')])
    if kind == 'unknown': pr.changed_files.append('missing.js')
    chunks = r.split_review_diff(pr.diff)
    initial, code = c.automatic_complement(pr, chunks, 1)
    assert initial == []
    assert code == ('changed_unavailable' if kind in ('unknown', 'unsafe') else 'partial_file')


def test_partial_seed_aggregates_repeated_sections_for_one_pinned_path():
    first, repeated = section('large.js', 20000), section('large.js')
    pr = pr_info([first, section('tail.js'), repeated])
    initial, code = c.automatic_complement(pr, r.split_review_diff(pr.diff), 1)
    assert code == 'partial_file_supplied'
    assert initial == [dict(path='large.js', diff=first+repeated)]


def test_partial_seed_still_cannot_start_a_third_reviewer_call():
    pr = pr_info([section('large.js', 20000), section('tail.js')])
    chunks = r.split_review_diff(pr.diff); part = replace(pr, diff=chunks[0])
    initial = c.automatic_complement(pr, chunks, 1)
    invoke = Mock(return_value=vote(r.ReviewVerdict.BORDERLINE, changed=['tail.js']))
    result, _ = r._review_with_requested_context(invoke, part, pr, initial_context=initial)
    assert not result.passed and invoke.call_count == 2
    assert result.requested_files == ['tail.js'] and result.scope['context_incomplete'] is True
