"""Required partial-file and requested evidence share the unchanged packet limits."""
from dataclasses import replace
from unittest.mock import Mock

import pytest

from cluster.control_plane.local_karen import chunk_context as c, reviewers as r
from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.source_context import SourceUnavailable


def _section(path, size):
    header = 'diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -0,0 +1 @@\n+'.format(path)
    return header + 'x' * (size - len(header) - 1) + '\n'


def _fixture(size=20000, other_size=200, partial=True):
    current = _section('current.py', size if partial else 200)
    others = [_section('other{0}.py'.format(i), other_size + i) for i in range(3)]
    pr = PRInfo('owner', 'project', 1, 'a' * 40, 'Bounded dependency context', 'author', False,
                current + ''.join(others), {}, ['current.py'] + ['other{0}.py'.format(i) for i in range(3)], [],
                description='Synthetic exact-head context fixture')
    cut = 5000 if partial else len(current)
    chunks = [current[:cut], current[cut:] + ''.join(others)]
    part = replace(pr, diff=chunks[0], review_scope=c.build_review_scope(pr, chunks, 1))
    return pr, part, c.automatic_complement(pr, chunks, 1)


def _vote(verdict=r.ReviewVerdict.PASS, source=None):
    return r.ReviewResult('gemini', verdict, 'Checked bounded supplied implementation',
        findings=r.ReviewFindings(risk_notes=['Checked exact supplied files']),
        requested_source_files=source or [])


def _resolver(text='ready = True\n'):
    return Mock(fetch=Mock(side_effect=lambda part, path: dict(path=path, head_sha=part.head_sha,
        blob_sha='b' * 40, source=text)))


@pytest.mark.parametrize('requested_count', [2, 3])
def test_required_diff_and_requested_dependencies_replace_only_optional_seeds(requested_count):
    pr, part, initial = _fixture()
    assert initial[1] == 'partial_file_supplied' and len(initial[0]) == 3
    requests = ['runtime/{0}.py'.format(i) for i in range(requested_count)]
    resolver = _resolver()
    invoke = Mock(side_effect=[_vote(r.ReviewVerdict.BORDERLINE, requests), _vote()])
    result, _ = r._review_with_requested_context(invoke, part, pr, resolver, initial)
    assert result.passed and invoke.call_count == 2
    assert resolver.fetch.call_count == requested_count
    before, after = [call.args[0] for call in invoke.call_args_list]
    assert list(before.review_material.entries) == initial[0]
    packet = list(after.review_material.entries)
    assert packet[0] == initial[0][0] == dict(path='current.py', diff=c.requested_file_diff(pr, 'current.py', pr.head_sha))
    assert len(packet) == c.MAX_CONTEXT_FILES == 4
    assert max(c.context_size(packet)) <= c.MAX_CONTEXT_SIZE == 48000
    retained = [entry for entry in packet if 'diff' in entry]
    assert retained == initial[0][:4 - requested_count]
    assert [entry['path'] for entry in packet if 'source' in entry] == requests
    assert result.scope['followup_omitted_optional_count'] == requested_count - 1
    assert before.diff == after.diff == part.diff
    assert before.head_sha == after.head_sha == pr.head_sha
    assert before.description == after.description == pr.description


def test_irreducible_current_and_four_dependencies_still_refuse_before_source_fetch():
    pr, part, initial = _fixture()
    resolver = _resolver()
    invoke = Mock(return_value=_vote(r.ReviewVerdict.BORDERLINE,
                                    ['runtime/{0}.py'.format(i) for i in range(4)]))
    result, _ = r._review_with_requested_context(invoke, part, pr, resolver, initial)
    assert not result.passed and invoke.call_count == 1
    assert result.scope['context_resolution']['code'] == 'context_budget'
    resolver.fetch.assert_not_called()


@pytest.mark.parametrize('size,text', [(34000, 'x' * 8000), (10000, '\u2603' * 5000)])
def test_required_raw_and_serialized_overflows_remain_unresolved(size, text):
    pr, part, initial = _fixture(size)
    assert len(initial[0]) == 3 and max(c.context_size(initial[0])) < 48000
    invoke = Mock(return_value=_vote(r.ReviewVerdict.BORDERLINE, ['runtime/a.py', 'runtime/b.py']))
    result, _ = r._review_with_requested_context(invoke, part, pr, _resolver(text), initial)
    assert not result.passed and invoke.call_count == 1
    assert result.scope['context_resolution']['code'] == 'source_unavailable'
    assert result.scope['source_context_error'] == 'source_budget'


def test_optional_seed_is_removed_for_byte_pressure_without_expanding_the_limit():
    pr, part, initial = _fixture(size=35000, other_size=1000)
    resolver = _resolver('x' * 11000)
    invoke = Mock(side_effect=[_vote(r.ReviewVerdict.BORDERLINE, ['runtime/a.py']), _vote()])
    result, _ = r._review_with_requested_context(invoke, part, pr, resolver, initial)
    assert result.passed and invoke.call_count == 2
    packet = list(invoke.call_args.args[0].review_material.entries)
    assert packet[:2] == initial[0][:2]
    assert len(packet) == 3 and packet[-1]['path'] == 'runtime/a.py'
    assert result.scope['followup_omitted_optional_count'] == 1
    assert max(c.context_size(packet)) <= 48000


def test_whole_integration_packet_is_not_trimmed_to_create_a_second_budget():
    pr, part, initial = _fixture(partial=False)
    assert initial[1] == 'supplied' and len(initial[0]) == 3
    resolver = _resolver()
    invoke = Mock(return_value=_vote(r.ReviewVerdict.BORDERLINE, ['runtime/a.py', 'runtime/b.py']))
    result, _ = r._review_with_requested_context(invoke, part, pr, resolver, initial)
    assert not result.passed and invoke.call_count == 1
    assert result.scope['context_resolution']['code'] == 'context_budget'
    resolver.fetch.assert_not_called()


def test_existing_bridge_evidence_is_retained_whenever_the_packet_fits():
    pr, part, initial = _fixture()
    invoke = Mock(side_effect=[_vote(r.ReviewVerdict.BORDERLINE, ['runtime/a.py']), _vote()])
    result, _ = r._review_with_requested_context(invoke, part, pr, _resolver(), initial)
    assert result.passed
    assert list(invoke.call_args.args[0].review_material.entries)[:3] == initial[0]
    assert 'followup_omitted_optional_count' not in result.scope


def test_repacking_does_not_permit_a_third_reviewer_call():
    pr, part, initial = _fixture()
    request = _vote(r.ReviewVerdict.BORDERLINE, ['runtime/a.py', 'runtime/b.py'])
    invoke = Mock(return_value=request)
    result, _ = r._review_with_requested_context(invoke, part, pr, _resolver(), initial)
    assert invoke.call_count == 2
    assert not result.passed and result.requested_source_files == request.requested_source_files


def test_repacking_never_overrides_a_grounded_rejecting_vote():
    pr, part, initial = _fixture()
    rejection = _vote(r.ReviewVerdict.FAIL)
    rejection.findings.functional_issues.append('current.py:1 references an undefined name')
    invoke = Mock(side_effect=[_vote(r.ReviewVerdict.BORDERLINE, ['runtime/a.py', 'runtime/b.py']), rejection])
    result, _ = r._review_with_requested_context(invoke, part, pr, _resolver(), initial)
    assert not result.passed and invoke.call_count == 2
    assert result.verdict == r.ReviewVerdict.FAIL


def test_unavailable_source_keeps_privacy_and_incomplete_context_guards():
    pr, part, initial = _fixture()
    resolver = Mock(fetch=Mock(side_effect=SourceUnavailable('source_private')))
    invoke = Mock(return_value=_vote(r.ReviewVerdict.BORDERLINE, ['runtime/a.py', 'runtime/b.py']))
    result, _ = r._review_with_requested_context(invoke, part, pr, resolver, initial)
    assert not result.passed and invoke.call_count == 1
    assert result.scope['context_resolution']['code'] == 'source_unavailable'
    assert result.scope['source_context_error'] == 'source_private'
