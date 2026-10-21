"""A later changed-file request must retain already-proven unchanged source."""
import base64
import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import chunk_context as c, reviewers as r, source_context as s
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.checks import PRInfo

class Objects:
    def __init__(self, raw=b'def check(value):\n    return value > 0\n'):
        self.raw = raw
        self.sha = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        self.commit = {'sha': 'a' * 40, 'tree': {'sha': 'b' * 40}}
        self.tree = {'sha': 'b' * 40, 'truncated': False, 'tree': [
            dict(path='src/runtime.py', type='blob', mode='100644', size=len(raw), sha=self.sha),
            dict(path='src', type='tree', mode='040000', sha='d' * 40)]}
        self.blob = dict(sha=self.sha, size=len(raw), encoding='base64',
                         content=base64.b64encode(raw).decode())
        self.calls = []

    def read(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        assert argv[:3] == ['api', '--method', 'GET'] and len(argv) == 4
        endpoint = argv[-1]
        value = (self.commit if '/commits/' in endpoint else self.tree if '/trees/' in endpoint else self.blob)
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), 'private stderr')


def pr_info():
    diff = ('diff --git a/docs.md b/docs.md\n--- a/docs.md\n+++ b/docs.md\n'
            '@@ -1 +1,1800 @@\n-old\n' + '+context\n' * 1800 +
            'diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n'
            '@@ -1 +1 @@\n-old\n+return unsafe(user_input)\n')
    return PRInfo('owner', 'repo', 1, 'a' * 40, 'Implement safe app', 'author',
                  False, diff, {'tests': 'success'}, ['docs.md', 'app.py'], [],
                  description='The implementation and its tests span this whole PR.')


def vote(verdict=r.ReviewVerdict.PASS, defect=None, requests=None):
    result = r.ReviewResult('codex', verdict, 'Reviewed the supplied pinned implementation',
                            confidence=.8,
                            findings=r.ReviewFindings(functional_issues=[defect] if defect else [],
                                                     risk_notes=['Checked complete supplied scope']))
    result.requested_files = requests or []
    return result



PATH = 'src/runtime.py'
RAW = b'import sys\nVALUE = 2\n'


def changed(path, before, after, line=2):
    return ('diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n'
            '@@ -{1} +{1} @@\n-{2}\n+{3}\n').format(path, line, before, after)


def packet(raw=RAW, extras=()):
    source = changed(PATH, 'VALUE = 1', raw.decode().splitlines()[1])
    helper = changed('src/helper.py', 'value = 1', 'value = 2')
    extra = ''.join(changed(path, 'old', text) for path, text in extras)
    pr = replace(pr_info(), diff=source + extra + helper,
                 changed_files=[PATH, *(path for path, _ in extras), 'src/helper.py'])
    part = replace(pr, diff=helper, review_scope=c.build_review_scope(pr, [source + extra, helper], 2))
    return pr, part


def prime(monkeypatch, raw=RAW):
    objects = Objects(raw)
    monkeypatch.setattr(s, 'budgeted_gh', objects.read)
    pr, part = packet(raw)
    resolver = s.PinnedSourceContext(pr)
    resolver.fetch(pr, PATH)
    return objects, pr, part, resolver


def needs_imports(calls, final=None):
    def invoke(part):
        calls.append(part)
        if len(calls) == 1:
            return vote(r.ReviewVerdict.BORDERLINE, requests=[PATH])
        sources = [entry for entry in part.review_material.entries
                   if entry.get('path') == PATH and 'source' in entry]
        if not sources:
            return replace(vote(r.ReviewVerdict.BORDERLINE),
                           reason='Cannot verify unchanged imports from a changed-file diff')
        assert len(sources) == 1 and sources[0]['source'].startswith('import sys\n')
        return final or vote()
    return invoke


def test_later_changed_request_receives_cached_full_source_without_another_read(monkeypatch):
    objects, pr, part, resolver = prime(monkeypatch)
    calls = []
    with patch.object(Path, 'read_text', side_effect=AssertionError('No checkout reads')):
        result, _ = r._review_with_requested_context(needs_imports(calls), part, pr, resolver)
    assert result.passed
    assert len(calls) == 2 and len(objects.calls) == resolver.calls == 3
    entries = calls[-1].review_material.entries
    assert any(entry.get('path') == PATH and 'diff' in entry for entry in entries)
    assert any(entry.get('path') == PATH and entry.get('source') == RAW.decode() for entry in entries)
    assert result.scope['source_context'][0]['blob_sha'] == objects.sha
    assert result.scope['reused_source_files'] == 1
    assert all('source' not in entry for entry in result.scope['source_context'])


def test_source_reuse_still_requires_both_independent_review_votes(monkeypatch):
    objects = Objects(RAW)
    monkeypatch.setattr(s, 'budgeted_gh', objects.read)
    pr, _ = packet()
    primary, second = [], []
    def review(part, *args):
        primary.append(part)
        if len(primary) == 1:
            return replace(vote(r.ReviewVerdict.BORDERLINE), requested_source_files=[PATH])
        assert any(entry.get('source') == RAW.decode() for entry in part.review_material.entries)
        return vote()
    second_invoke = needs_imports(second)
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
    assert all(row.scope['source_context'][0]['blob_sha'] == objects.sha for row in reviews)


def test_cold_cache_does_not_fetch_unchanged_source_or_pretend_diff_is_complete(monkeypatch):
    objects = Objects(RAW)
    monkeypatch.setattr(s, 'budgeted_gh', objects.read)
    pr, part = packet()
    resolver = s.PinnedSourceContext(pr)
    calls = []
    result, _ = r._review_with_requested_context(needs_imports(calls), part, pr, resolver)
    assert not result.passed and result.scope['context_incomplete']
    assert len(calls) == 2 and objects.calls == []
    assert all('source' not in entry for entry in calls[-1].review_material.entries)


@pytest.mark.parametrize('field,value', [('owner', 'other'), ('repo', 'other'), ('head_sha', 'c' * 40)])
def test_cached_source_cannot_cross_repository_or_head(monkeypatch, field, value):
    objects, pr, part, resolver = prime(monkeypatch)
    other = replace(pr, **{field: value})
    other_part = replace(part, **{field: value})
    calls = []
    result, _ = r._review_with_requested_context(needs_imports(calls), other_part, other, resolver)
    assert not result.passed and result.scope['source_context_error'] == 'source_identity_or_path'
    assert len(calls) == 1 and len(objects.calls) == 3


@pytest.mark.parametrize('field,value', [('source', 'changed content'), ('head_sha', 'c' * 40),
    ('tree_sha', 'c' * 40), ('blob_sha', 'c' * 40), ('bytes', 1), ('path', 'src/other.py')])
def test_modified_cached_evidence_is_never_supplied(monkeypatch, field, value):
    objects, pr, part, resolver = prime(monkeypatch)
    resolver.sources[PATH][field] = value
    calls = []
    result, _ = r._review_with_requested_context(needs_imports(calls), part, pr, resolver)
    assert not result.passed and result.scope['source_context_error'] == 'blob_identity'
    assert len(calls) == 1 and len(objects.calls) == 3


def test_unproven_cache_insertion_does_not_become_immutable_source(monkeypatch):
    _, pr, part, proven = prime(monkeypatch)
    resolver = s.PinnedSourceContext(pr)
    resolver.sources[PATH] = dict(proven.sources[PATH])
    calls = []
    result, _ = r._review_with_requested_context(needs_imports(calls), part, pr, resolver)
    assert not result.passed and result.scope['source_context_error'] == 'blob_identity'
    assert len(calls) == 1


def test_source_fetch_does_not_expose_a_mutable_alias_to_cached_proof(monkeypatch):
    _, pr, _, resolver = prime(monkeypatch)
    value = resolver.fetch(pr, PATH)
    value['source'] = 'Caller mutation'
    assert resolver.fetch(pr, PATH)['source'] == RAW.decode()


def test_optional_cached_source_never_displaces_requested_material_or_raises_entry_cap(monkeypatch):
    objects, _, _, resolver = prime(monkeypatch)
    extras = [(f'src/extra{n}.py', f'value = {n}') for n in range(3)]
    pr, part = packet(extras=extras)
    initial = ([dict(path=path, diff=c.requested_file_diff(pr, path, pr.head_sha)) for path, _ in extras], 'supplied')
    calls = []
    result, _ = r._review_with_requested_context(needs_imports(calls), part, pr, resolver, initial)
    assert not result.passed and len(calls) == 2 and len(objects.calls) == 3
    assert len(calls[-1].review_material.entries) == c.MAX_CONTEXT_FILES == 4
    assert all('source' not in entry for entry in calls[-1].review_material.entries)


def test_cached_source_never_raises_raw_or_serialized_packet_byte_cap(monkeypatch):
    prefix = b'import sys\nVALUE = 2\n#'
    raw = prefix + b'V' * (s.MAX_FILE_BYTES - len(prefix) - 1) + b'\n'
    objects, _, _, resolver = prime(monkeypatch, raw)
    pr, part = packet(raw, extras=[('src/extra.py', 'x' * 35000)])
    initial = ([dict(path='src/extra.py', diff=c.requested_file_diff(pr, 'src/extra.py', pr.head_sha))], 'supplied')
    calls = []
    result, _ = r._review_with_requested_context(needs_imports(calls), part, pr, resolver, initial)
    assert not result.passed and len(calls) == 2 and len(objects.calls) == 3
    assert max(c.context_size(calls[-1].review_material.entries)) <= c.MAX_CONTEXT_SIZE == 48000
    assert all('source' not in entry for entry in calls[-1].review_material.entries)


def test_cached_source_does_not_approve_repeated_context_requests(monkeypatch):
    objects, pr, part, resolver = prime(monkeypatch)
    invoke = Mock(side_effect=lambda supplied: vote(r.ReviewVerdict.PASS, requests=[PATH]))
    result, _ = r._review_with_requested_context(invoke, part, pr, resolver)
    assert not result.passed and result.scope['context_incomplete'] and invoke.call_count == 2
    assert len(objects.calls) == 3


def test_real_changed_line_defect_survives_cached_source_delivery(monkeypatch):
    raw = b'import sys\nreturn unsafe(user_input)\n'
    objects, pr, part, resolver = prime(monkeypatch, raw)
    calls = []
    failure = vote(r.ReviewVerdict.FAIL, 'src/runtime.py:2 invokes unsafe with unvalidated user input')
    result, _ = r._review_with_requested_context(needs_imports(calls, failure), part, pr, resolver)
    assert result.verdict == r.ReviewVerdict.FAIL and not result.passed
    assert 'src/runtime.py:2' in result.findings.functional_issues[0]
    assert len(calls) == 2 and len(objects.calls) == 3
