"""Previously requested, proven dependencies remain available to independent judges."""
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import chunk_context as c, reviewers as r, source_context as s
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.tests.test_cached_review_source import (
    Objects, PATH, RAW, packet, prime, vote, changed)


HELPER = 'src/helper.py'


def needs_dependency(calls):
    def invoke(part):
        calls.append(part)
        if len(calls) == 1:
            return vote(r.ReviewVerdict.BORDERLINE, requests=[HELPER])
        entries = part.review_material.entries
        if not any(entry.get('source') == RAW.decode() for entry in entries):
            return replace(vote(r.ReviewVerdict.BORDERLINE),
                           reason='Cannot verify the related module bindings without source')
        assert any(entry.get('path') == HELPER and 'diff' in entry for entry in entries)
        assert all('reviewer' not in entry and 'reason' not in entry for entry in entries)
        return vote()
    return invoke


def test_later_helper_request_retains_previously_proven_dependency(monkeypatch):
    objects, pr, part, resolver = prime(monkeypatch)
    calls = []
    result, _ = r._review_with_requested_context(needs_dependency(calls), part, pr, resolver)
    assert result.passed and len(calls) == 2
    assert len(objects.calls) == resolver.calls == 3
    assert result.scope['reused_source_files'] == 1
    assert result.scope['source_context'][0]['blob_sha'] == objects.sha


def test_independent_second_judge_receives_source_facts_without_new_fetch(monkeypatch):
    objects = Objects(RAW)
    monkeypatch.setattr(s, 'budgeted_gh', objects.read)
    pr, _ = packet()
    primary, second = [], []
    def review(part, *args):
        primary.append(part)
        if len(primary) == 1:
            return replace(vote(r.ReviewVerdict.BORDERLINE), requested_source_files=[PATH])
        return vote()
    second_invoke = needs_dependency(second)
    with patch.object(r, 'get_available_reviewers', return_value=[('codex', Mock(review=review))]), \
         patch.object(r.MiniMaxReviewer, 'is_available', return_value=(True, 'ok')), \
         patch.object(r.MiniMaxReviewer, 'review_as_second_judge',
                      side_effect=lambda part, *args: replace(second_invoke(part), reviewer='minimax')):
        result, reviews = r.run_review_chain(pr, KarenConfig())
    assert result.passed and len(primary) == len(second) == 2
    assert len(objects.calls) == 3
    from cluster.control_plane.local_karen.verdict import create_verdict
    from cluster.control_plane.local_karen.verdict_push import reviewed
    assert reviewed(create_verdict(pr, [], reviews, True, result.reason, result.reviewer))
    assert {row.reviewer for row in reviews} == {'codex', 'minimax'}


@pytest.mark.parametrize('field,value', [('head_sha', 'c' * 40), ('source', 'altered'), ('blob_sha', 'c' * 40)])
def test_dependency_handoff_refuses_mutated_cached_proof(monkeypatch, field, value):
    objects, pr, part, resolver = prime(monkeypatch)
    resolver.sources[PATH][field] = value
    calls = []
    result, _ = r._review_with_requested_context(needs_dependency(calls), part, pr, resolver)
    assert not result.passed and result.scope['source_context_error'] == 'blob_identity'
    assert len(calls) == 1 and len(objects.calls) == 3


def test_unproven_unrequested_cache_entry_is_not_distributed(monkeypatch):
    objects, pr, part, resolver = prime(monkeypatch)
    resolver.sources['src/unrequested.py'] = dict(resolver.sources[PATH], path='src/unrequested.py')
    calls = []
    result, _ = r._review_with_requested_context(needs_dependency(calls), part, pr, resolver)
    assert result.passed
    assert all(entry.get('path') != 'src/unrequested.py' for entry in calls[-1].review_material.entries)
    assert len(objects.calls) == 3


def test_requested_material_keeps_all_four_entries_before_optional_dependencies(monkeypatch):
    objects, pr, part, resolver = prime(monkeypatch)
    extras = [(f'src/extra{n}.py', f'value = {n}') for n in range(3)]
    pr, part = packet(extras=extras)
    calls = []
    invoke = needs_dependency(calls)
    def ask_all(part):
        result = invoke(part)
        return replace(result, requested_files=[HELPER] + [path for path, _ in extras]) if len(calls) == 1 else result
    result, _ = r._review_with_requested_context(ask_all, part, pr, resolver)
    assert not result.passed and len(calls) == 2
    entries = calls[-1].review_material.entries
    assert len(entries) == c.MAX_CONTEXT_FILES == 4
    assert all('source' not in entry for entry in entries)
    assert {entry['path'] for entry in entries} == {HELPER, *(path for path, _ in extras)}
    assert len(objects.calls) == 3


def test_unmodified_dependency_is_not_broadcast_without_an_explicit_request(monkeypatch):
    objects, pr, part, resolver = prime(monkeypatch)
    pr = replace(pr, changed_files=[HELPER], diff=part.diff)
    part = replace(part, changed_files=[HELPER])
    calls = []
    result, _ = r._review_with_requested_context(needs_dependency(calls), part, pr, resolver)
    assert not result.passed and len(calls) == 2
    assert all(entry.get('path') != PATH for entry in calls[-1].review_material.entries)
    assert len(objects.calls) == 3
