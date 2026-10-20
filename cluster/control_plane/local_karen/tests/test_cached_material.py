"""Previously fetched bytes provide context; votes and mutable entries do not."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import base64
import hashlib
import json

import pytest

from cluster.control_plane.local_karen import reviewers as r, source_context as s
from cluster.control_plane.local_karen.cached_material import initial_cached_material
from cluster.control_plane.local_karen.chunk_context import build_review_scope, context_size
from cluster.control_plane.local_karen.checks import PRInfo, ReviewScope


def section(path):
    return 'diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -0,0 +1 @@\n+checked = True\n'.format(path)


@pytest.fixture
def cached(monkeypatch, request):
    sources = getattr(request, 'param', None) or {
        'worker.py': '# implementation\n' * 60,
        'formatter.py': '# formatter\n' * 40, 'retry.py': '# retry\n' * 20}
    chunks = [section('test_case.py'), ''.join(section(p) for p in sources)]
    pr = PRInfo('owner', 'repo', 31, 'a' * 40, 'Cache', 'author', False,
                ''.join(chunks), {}, ['test_case.py', *sources], [])
    part = replace(pr, diff=chunks[0], review_scope=build_review_scope(pr, chunks, 1))
    blobs = {}
    entries = []
    for path, text in sources.items():
        raw = text.encode()
        sha = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        entries.append(dict(path=path, type='blob', mode='100644', sha=sha, size=len(raw)))
        blobs[sha] = dict(sha=sha, size=len(raw), encoding='base64', content=base64.b64encode(raw).decode())
    def transport(args, **kwargs):
        endpoint = args[-1].split('/git/', 1)[1]
        if endpoint == 'commits/' + pr.head_sha:
            value = dict(sha=pr.head_sha, tree=dict(sha='b' * 40))
        elif endpoint == 'trees/' + 'b' * 40 + '?recursive=1':
            value = dict(sha='b' * 40, truncated=False, tree=entries)
        else:
            assert endpoint.startswith('blobs/') and endpoint[6:] in blobs
            value = blobs[endpoint[6:]]
        return SimpleNamespace(returncode=0, stdout=json.dumps(value))
    io = Mock(side_effect=transport)
    monkeypatch.setattr(s, 'budgeted_gh', io)
    monkeypatch.setattr('cluster.control_plane.local_karen.gate.review_checkpoint', lambda: None)
    resolver = s.PinnedSourceContext(pr)
    for path in sources:
        resolver.fetch(pr, path)
    assert resolver.calls == io.call_count == 5
    return pr, part, resolver, io, sources


def test_two_full_sources_keep_two_slots_without_new_io_or_votes(cached):
    pr, part, resolver, io, sources = cached
    before = resolver.calls, resolver.bytes, io.call_count
    material = initial_cached_material(part, pr, [], resolver)
    assert [x['path'] for x in material] == ['worker.py', 'formatter.py']
    assert all(x['source'] == sources[x['path']] and 'diff' not in x for x in material)
    assert len(material) == 2 and max(context_size(material)) <= 48000
    assert (resolver.calls, resolver.bytes, io.call_count) == before
    assert all(set(x) == {'path', 'head_sha', 'tree_sha', 'blob_sha', 'bytes', 'source'} for x in material)


@pytest.mark.parametrize('field,value', [('owner', 'other'), ('repo', 'other'),
                                       ('number', 32), ('head_sha', 'c' * 40)])
def test_cache_cannot_cross_pr_repository_or_head(cached, field, value):
    pr, part, resolver, io, _ = cached
    assert resolver.previously_verified(replace(pr, **{field: value})) == []
    assert initial_cached_material(replace(part, **{field: value}), pr, [], resolver) == []
    assert io.call_count == 5


def test_mutable_public_cache_and_return_values_cannot_change_verified_snapshot(cached):
    pr, part, resolver, _, sources = cached
    resolver.sources['worker.py']['source'] = 'caller mutation'
    observed = resolver.previously_verified(pr)
    observed[0]['source'] = 'second mutation'
    material = initial_cached_material(part, pr, [], resolver)
    assert material[0]['source'] == sources['worker.py']
    assert resolver.previously_verified(pr)[0]['source'] == sources['worker.py']


def test_unverified_injected_cache_never_seeds_material(cached):
    pr, part, resolver, _, _ = cached
    empty = s.PinnedSourceContext(pr)
    empty.sources.update(resolver.sources)
    assert initial_cached_material(part, pr, [], empty) == []


def test_existing_packet_partial_primary_and_mismatched_scope_are_preserved(cached):
    pr, part, resolver, io, _ = cached
    original = [dict(path='worker.py', diff=section('worker.py'))]
    assert initial_cached_material(part, pr, original, resolver) is original
    assert initial_cached_material(replace(part, diff=part.diff[:25]), pr, [], resolver) == []
    wrong = dict(part.review_scope.data, head_sha='d' * 40)
    assert initial_cached_material(replace(part, review_scope=ReviewScope(wrong)), pr, [], resolver) == []
    assert initial_cached_material(replace(part, review_scope=None), pr, [], resolver) == []
    assert io.call_count == 5


@pytest.mark.parametrize('cached', [{p: '#' + '\t' * 15000 for p in
                                   ('worker.py', 'formatter.py', 'retry.py')}], indirect=True)
def test_serialized_budget_is_not_replaced_by_raw_byte_size(cached):
    pr, part, resolver, io, _ = cached
    material = initial_cached_material(part, pr, [], resolver)
    assert len(material) == 1
    assert max(context_size(material)) <= 48000
    assert context_size(material)[1] > context_size(material)[0]
    assert resolver.calls == io.call_count == 5 and resolver.bytes < 48000


def test_failed_hash_verification_cannot_create_reusable_context(cached):
    pr, part, _, io, _ = cached
    transport = io.side_effect
    def corrupt(args, **kwargs):
        value = transport(args, **kwargs)
        if '/blobs/' in args[-1]:
            body = json.loads(value.stdout)
            body['content'] = base64.b64encode(b'changed').decode()
            value.stdout = json.dumps(body)
        return value
    io.side_effect = corrupt
    resolver = s.PinnedSourceContext(pr)
    with pytest.raises(s.SourceUnavailable, match='blob_hash'):
        resolver.fetch(pr, 'worker.py')
    assert resolver.previously_verified(pr) == []
    assert initial_cached_material(part, pr, [], resolver) == []


def test_full_source_and_requested_diff_remain_distinct_in_one_bounded_followup(cached):
    pr, part, resolver, io, sources = cached
    initial = r.ReviewResult('codex', r.ReviewVerdict.BORDERLINE, 'Need the complete formatter diff',
        requested_files=['formatter.py'], requested_source_files=['worker.py'])
    final = r.ReviewResult('codex', r.ReviewVerdict.ERROR, 'Synthetic evidence probe grants no approval')
    invoke = Mock(side_effect=[initial, final])
    result, _ = r._review_with_requested_context(invoke, part, pr, resolver)
    assert invoke.call_args_list[0].args[0].review_material.phase == 'supplemental'
    assert invoke.call_args_list[1].args[0].review_material.phase == 'requested'
    first, second = [call.args[0].review_material.entries for call in invoke.call_args_list]
    assert len(first) == 2 and len(second) == 3
    assert {(x['path'], 'source' in x) for x in second} == {
        ('worker.py', True), ('formatter.py', True), ('formatter.py', False)}
    assert next(x['source'] for x in second if x['path'] == 'formatter.py' and 'source' in x) == sources['formatter.py']
    assert all(max(context_size(list(packet))) <= 48000 for packet in (first, second))
    assert result.verdict == r.ReviewVerdict.ERROR and invoke.call_count == 2
    assert resolver.calls == io.call_count == 5
    assert result.scope['initial_context']['cached_source_files'] == 2


def test_partial_primary_keeps_required_diff_before_any_optional_replanning(cached):
    pr, _, resolver, io, sources = cached
    whole = section('test_case.py')
    chunks = [whole[:len(whole)//2], pr.diff[len(whole)//2:]]
    part = replace(pr, diff=chunks[0], review_scope=build_review_scope(pr, chunks, 1))
    required = dict(path='test_case.py', diff=whole)
    initial = [required, dict(path='worker.py', diff=section('worker.py')),
               dict(path='retry.py', diff=section('retry.py'))]
    assert initial_cached_material(part, pr, initial, resolver) is initial
    packet = initial_cached_material(part, pr, initial, resolver, 'partial_file_supplied')
    assert packet[0] is required
    assert [x['path'] for x in packet[1:]] == ['worker.py', 'formatter.py']
    assert all(x['source'] == sources[x['path']] for x in packet[1:])
    assert len(packet) == 3 and max(context_size(packet)) <= 48000
    assert io.call_count == 5


def test_partial_file_without_verified_cache_keeps_all_integration_diffs(cached):
    pr, _, _, _, _ = cached
    whole = section('test_case.py')
    chunks = [whole[:len(whole)//2], pr.diff[len(whole)//2:]]
    part = replace(pr, diff=chunks[0], review_scope=build_review_scope(pr, chunks, 1))
    initial = [dict(path='test_case.py', diff=whole), dict(path='retry.py', diff=section('retry.py'))]
    assert initial_cached_material(part, pr, initial, s.PinnedSourceContext(pr), 'partial_file_supplied') is initial
