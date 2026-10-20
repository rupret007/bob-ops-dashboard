"""A repeated visible patch must yield new, verified implementation evidence."""
from dataclasses import replace
from unittest.mock import Mock

import pytest

from cluster.control_plane.local_karen import reviewers as r, source_context as s
from cluster.control_plane.local_karen.chunk_context import requested_file_diff
from cluster.control_plane.local_karen.tests.test_source_context import Objects
from cluster.control_plane.local_karen.tests.test_chunk_evidence import pr_info, vote


PATH = 'src/runtime.py'
DIFF = ('diff --git a/src/runtime.py b/src/runtime.py\n'
        '--- a/src/runtime.py\n+++ b/src/runtime.py\n'
        '@@ -1,2 +1,2 @@\n def check(value):\n-    return value >= 0\n+    return value > 0\n')


def setup(monkeypatch, raw=None):
    objects = Objects() if raw is None else Objects(raw)
    monkeypatch.setattr(s, 'budgeted_gh', objects.read)
    pr = replace(pr_info(), diff=DIFF, changed_files=[PATH])
    request = replace(vote(r.ReviewVerdict.BORDERLINE), requested_files=[PATH])
    return objects, pr, request


@pytest.mark.parametrize('also_source', [False, True])
def test_visible_patch_request_receives_only_new_pinned_source(monkeypatch, also_source):
    objects, pr, request = setup(monkeypatch)
    if also_source:
        request = replace(request, requested_source_files=[PATH])
    invoke = Mock(side_effect=[request, vote()])
    result, _ = r._review_with_requested_context(invoke, pr, pr)
    assert result.passed and invoke.call_count == 2 and len(objects.calls) == 3
    supplied = invoke.call_args.args[0]
    assert supplied.diff == pr.diff and supplied.description == pr.description
    assert supplied.head_sha == pr.head_sha
    assert len(supplied.review_material.entries) == 1
    entry = supplied.review_material.entries[0]
    assert entry == dict(path=PATH, head_sha=pr.head_sha, tree_sha='b' * 40,
                         blob_sha=objects.sha, bytes=len(objects.raw), source=objects.raw.decode())
    assert 'diff' not in entry
    assert result.scope['context_attempts'][0]['requested_files'] == [PATH]
    assert result.scope['context_attempts'][0]['requested_source_files'] == ([PATH] if also_source else [])
    assert result.scope['changed_requests_answered_with_source'] == 1
    assert result.scope['source_context'][0]['blob_sha'] == objects.sha
    assert 'source' not in result.scope['source_context'][0]


@pytest.mark.parametrize('kind', ['private', 'oversize', 'missing', 'bad-blob'])
def test_unavailable_full_source_never_falls_back_to_duplicate_diff(monkeypatch, kind):
    raw = b'{"password": "tiny"}' if kind == 'private' else b'x' * 16001 if kind == 'oversize' else None
    objects, pr, request = setup(monkeypatch, raw)
    if kind == 'missing':
        objects.tree['tree'] = []
    elif kind == 'bad-blob':
        objects.blob['sha'] = 'c' * 40
    invoke = Mock(side_effect=[request, vote()])
    result, _ = r._review_with_requested_context(invoke, pr, pr)
    assert not result.passed and invoke.call_count == 1
    assert result.scope['context_incomplete']
    assert result.scope['context_resolution']['code'] == 'source_unavailable'


def test_partial_patch_still_receives_the_missing_changed_file_without_fetch(monkeypatch):
    objects, pr, request = setup(monkeypatch)
    part = replace(pr, diff=pr.diff[:80])
    invoke = Mock(side_effect=[request, vote()])
    result, _ = r._review_with_requested_context(invoke, part, pr)
    assert result.passed and invoke.call_count == 2 and objects.calls == []
    assert invoke.call_args.args[0].review_material.entries == ({'path': PATH, 'diff': DIFF},)
    assert 'changed_requests_answered_with_source' not in result.scope


@pytest.mark.parametrize('final', ['request-again', 'defect'])
def test_new_source_requires_a_fresh_vote_and_never_starts_a_third_call(monkeypatch, final):
    objects, pr, request = setup(monkeypatch)
    second = request if final == 'request-again' else vote(
        r.ReviewVerdict.FAIL, 'src/runtime.py:2 changes the boundary condition incorrectly')
    invoke = Mock(side_effect=[request, second])
    result, _ = r._review_with_requested_context(invoke, pr, pr)
    assert not result.passed and invoke.call_count == 2 and len(objects.calls) == 3
    assert result.verdict == (r.ReviewVerdict.BORDERLINE if final == 'request-again' else r.ReviewVerdict.FAIL)
    if final == 'request-again':
        assert result.scope['context_incomplete']


@pytest.mark.parametrize('limit', ['slots', 'raw-bytes', 'serialized-bytes'])
def test_automatic_context_and_upgraded_source_keep_the_same_total_budget(monkeypatch, limit):
    objects, pr, request = setup(monkeypatch, b'x' * 14000)
    filler = '' if limit == 'slots' else 'x' * 12500 if limit == 'raw-bytes' else '\u2603' * 2000
    sections = [('src/extra%d.py' % n, 'diff --git a/src/extra{0}.py b/src/extra{0}.py\n'
                 '--- a/src/extra{0}.py\n+++ b/src/extra{0}.py\n@@ -0,0 +1,2 @@\n+ready = True\n+#'.format(n) + filler + '\n')
                for n in range(4 if limit == 'slots' else 3)]
    pr = replace(pr, diff=pr.diff + ''.join(text for _, text in sections),
                 changed_files=pr.changed_files + [path for path, _ in sections])
    initial = ([{'path': path, 'diff': requested_file_diff(pr, path, pr.head_sha)}
                for path, _ in sections], 'supplied')
    invoke = Mock(return_value=request)
    result, _ = r._review_with_requested_context(invoke, pr, pr, initial_context=initial)
    assert not result.passed and invoke.call_count == 1
    assert len(objects.calls) == (0 if limit == 'slots' else 3)
    assert result.scope['context_resolution']['code'] == ('context_budget' if limit == 'slots' else 'source_unavailable')


@pytest.mark.parametrize('field', ['owner', 'repo', 'number', 'head_sha', 'diff'])
def test_another_identity_or_unpinned_diff_cannot_supply_context(monkeypatch, field):
    objects, pr, request = setup(monkeypatch)
    value = 999 if field == 'number' else 'c' * 40 if field == 'head_sha' else 'unrelated'
    part = replace(pr, **{field: value})
    invoke = Mock(side_effect=[request, vote()])
    result, _ = r._review_with_requested_context(invoke, part, pr)
    assert not result.passed and invoke.call_count == 1 and objects.calls == []
