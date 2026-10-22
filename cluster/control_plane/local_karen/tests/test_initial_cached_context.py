"""A later judge must see proven changed-source facts on its first decision."""
import base64
import hashlib
import json
import subprocess
from dataclasses import replace

import pytest

from cluster.control_plane.local_karen import reviewers as r, source_context as s
from cluster.control_plane.local_karen.checks import PRInfo


@pytest.fixture
def proven(monkeypatch):
    raw = b'def public(config):\n    return implementation(config)\n'
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + bytes([0]) + raw).hexdigest()
    head, tree = 'a' * 40, 'b' * 40
    pr = PRInfo('owner', 'project', 1, head, 'Changed caller and implementation',
                'author', False, '', {}, ['src/caller.py', 'src/helper.py'], [])
    objects = {
        'commits/' + head: dict(sha=head, tree=dict(sha=tree)),
        'trees/' + tree + '?recursive=1': dict(sha=tree, truncated=False, tree=[
            dict(path='src', type='tree', mode='040000', sha='c' * 40),
            dict(path='src/caller.py', type='blob', mode='100644', size=len(raw), sha=blob)]),
        'blobs/' + blob: dict(sha=blob, size=len(raw), encoding='base64',
                             content=base64.b64encode(raw).decode()),
    }
    calls = []
    def read(argv, **kwargs):
        suffix = argv[-1].split('/git/', 1)[1]
        calls.append(suffix)
        return subprocess.CompletedProcess(argv, 0, json.dumps(objects[suffix]), '')
    monkeypatch.setattr(s, 'budgeted_gh', read)
    resolver = s.PinnedSourceContext(pr)
    source = resolver.fetch(pr, 'src/caller.py')
    monkeypatch.setattr(r, '_validate_chunk_result', lambda result, original: result)
    return pr, resolver, source, calls


def answer():
    return r.ReviewResult('packet-test', r.ReviewVerdict.BORDERLINE,
                          'Packet assertions confer no model review authority')


def test_first_decision_contains_proven_caller_without_extra_reads(proven):
    pr, resolver, source, calls = proven
    seen = []
    def invoke(part):
        seen.append(part)
        assert part.review_material is not None
        assert source in part.review_material.entries
        return answer()
    result, _ = r._review_with_requested_context(invoke, pr, pr, resolver)
    assert len(seen) == 1 and len(calls) == 3 and not result.passed
    assert result.scope['initial_context']['cached_source_files'] == 1


def test_repeated_source_request_does_not_duplicate_initial_fact(proven):
    pr, resolver, source, calls = proven
    seen = []
    def invoke(part):
        seen.append(part)
        entries = list(part.review_material.entries) if part.review_material else []
        assert entries == [source]
        result = answer()
        return replace(result, requested_source_files=['src/caller.py']) if len(seen) == 1 else result
    result, _ = r._review_with_requested_context(invoke, pr, pr, resolver)
    assert len(seen) == 2 and len(calls) == 3 and not result.passed
    assert result.scope['context_resolution']['code'] == 'followup_complete'


@pytest.mark.parametrize('damage', ['source', 'head', 'pr'])
def test_unproven_or_wrong_identity_never_reaches_judge(proven, damage):
    pr, resolver, source, calls = proven
    part = pr
    if damage == 'source':
        resolver.sources['src/caller.py']['source'] += 'changed'
    elif damage == 'head':
        part = replace(pr, head_sha='d' * 40)
    else:
        part = replace(pr, number=2)
    seen = []
    with pytest.raises(s.SourceUnavailable):
        r._review_with_requested_context(lambda value: seen.append(value), part, pr, resolver)
    assert seen == [] and len(calls) == 3


@pytest.mark.parametrize('budget', ['entries', 'bytes'])
def test_optional_facts_leave_a_request_slot_and_preserve_limits(proven, budget):
    from cluster.control_plane.local_karen import chunk_context as c
    pr, resolver, source, calls = proven
    paths = ['src/one.py', 'src/two.py', 'src/three.py'] if budget == 'entries' else ['src/one.py']
    diff = ''.join('diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -1 +1 @@\n-old\n+new\n'.format(path)
                   for path in paths)
    if budget == 'bytes':
        diff += '+' + 'x' * 33000 + '\n'
    pr = replace(pr, diff=diff, changed_files=pr.changed_files + paths)
    initial = ([dict(path=path, diff=c.requested_file_diff(pr, path)) for path in paths], 'supplied')
    seen = []
    def invoke(part):
        seen.append(part)
        assert len(part.review_material.entries) == len(paths)
        assert all('source' not in item for item in part.review_material.entries)
        return answer()
    r._review_with_requested_context(invoke, pr, pr, resolver, initial)
    assert len(seen) == 1 and len(calls) == 3
    assert (c.MAX_CONTEXT_FILES, c.MAX_CONTEXT_SIZE, s.MAX_FILE_BYTES, s.MAX_API_CALLS) == (4, 48000, 16000, 6)


def test_changed_diff_and_cached_source_remain_distinct_evidence(proven):
    from cluster.control_plane.local_karen import chunk_context as c
    pr, resolver, source, calls = proven
    path = 'src/caller.py'
    diff = 'diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -1 +1 @@\n-old\n+new\n'.format(path)
    pr = replace(pr, diff=diff)
    initial = ([dict(path=path, diff=diff)], 'supplied')
    def invoke(part):
        assert len(part.review_material.entries) == 2
        assert source in part.review_material.entries
        assert dict(path=path, diff=diff) in part.review_material.entries
        assert max(c.context_size(part.review_material.entries)) <= 48000
        return answer()
    r._review_with_requested_context(invoke, pr, pr, resolver, initial)
    assert len(calls) == 3


def test_an_untrusted_cache_implementation_is_never_consulted(proven):
    pr, _, _, calls = proven
    class Untrusted:
        def cached_changed_paths(self, value):
            raise AssertionError('Only the proven resolver may supply facts')
    result, _ = r._review_with_requested_context(lambda part: answer(), pr, pr, Untrusted())
    assert not result.passed and len(calls) == 3
