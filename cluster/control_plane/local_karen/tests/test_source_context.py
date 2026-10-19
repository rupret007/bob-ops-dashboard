"""Immutable source proof, privacy bounds, and unresolved-review authority."""
import base64
import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import reviewers as r, source_context as s
from cluster.control_plane.local_karen.tests.test_chunk_evidence import pr_info, vote
from cluster.control_plane.local_karen.config import KarenConfig


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


@pytest.fixture
def objects(monkeypatch):
    value = Objects()
    monkeypatch.setattr(s, 'budgeted_gh', value.read)
    return value


def test_full_source_is_proven_pinned_and_memory_cached_without_local_reads(objects):
    resolver = s.PinnedSourceContext(pr_info())
    with patch.object(Path, 'read_text', side_effect=AssertionError('no checkout/cache reads')):
        result = resolver.fetch(pr_info(), 'src/runtime.py')
        assert resolver.fetch(pr_info(), 'src/runtime.py') == result
    assert result == dict(path='src/runtime.py', head_sha='a' * 40, tree_sha='b' * 40, blob_sha=objects.sha,
                          bytes=len(objects.raw), source=objects.raw.decode())
    assert [c[0][-1] for c in objects.calls] == [
        'repos/owner/repo/git/commits/' + 'a' * 40,
        'repos/owner/repo/git/trees/' + 'b' * 40 + '?recursive=1',
        'repos/owner/repo/git/blobs/' + objects.sha]


@pytest.mark.parametrize('path', ['../runtime.py', '/tmp/runtime.py', 'src/../runtime.py',
    'src//runtime.py', 'src/./runtime.py', 'src/runtime.py\n', 'file:///src/runtime.py',
    'https://example.test/runtime.py', 'src\\runtime.py', 'main:src/runtime.py',
    'src/%2e%2e/runtime.py', '.codex/session.py', 'runtime/value.py', 'memory/value.py',
    'tests/fixtures/value.py', 'evidence/value.py', 'auth/value.py', 'data/value.py',
    'src/credentials.py', 'src/.env.py', 'src/passwords.py', 'src/keys.json', 'image.png'])
def test_unsafe_or_private_artifact_paths_never_fetch(objects, path):
    with pytest.raises(s.SourceUnavailable):
        s.PinnedSourceContext(pr_info()).fetch(pr_info(), path)
    assert objects.calls == []


@pytest.mark.parametrize('kind', ['commit', 'tree', 'truncated', 'duplicate', 'missing', 'parent-symlink',
    'symlink', 'submodule', 'oversize', 'bool-size', 'blob', 'blob-size', 'hash', 'base64'])
def test_unproven_or_incomplete_objects_fail_closed(objects, kind):
    if kind == 'commit': objects.commit['sha'] = 'c' * 40
    elif kind == 'tree': objects.tree['sha'] = 'c' * 40
    elif kind == 'truncated': objects.tree['truncated'] = True
    elif kind == 'duplicate': objects.tree['tree'] *= 2
    elif kind == 'missing': objects.tree['tree'] = []
    elif kind == 'parent-symlink': objects.tree['tree'][1]['mode'] = '120000'
    elif kind in ('symlink', 'submodule'): objects.tree['tree'][0]['mode'] = '120000' if kind == 'symlink' else '160000'
    elif kind == 'oversize': objects.tree['tree'][0]['size'] = s.MAX_FILE_BYTES + 1
    elif kind == 'bool-size': objects.tree['tree'][0]['size'] = True
    elif kind == 'blob': objects.blob['sha'] = 'c' * 40
    elif kind == 'blob-size': objects.blob['size'] += 1
    elif kind == 'hash': objects.blob['content'] = base64.b64encode(b'x' * len(objects.raw)).decode()
    elif kind == 'base64': objects.blob['content'] = 'invalid!'
    with pytest.raises(s.SourceUnavailable):
        s.PinnedSourceContext(pr_info()).fetch(pr_info(), 'src/runtime.py')
    if kind in ('symlink', 'submodule', 'oversize', 'bool-size', 'missing'):
        assert len(objects.calls) == 2


@pytest.mark.parametrize('raw', [b'\xff', b'a\0b', b'a\x7fb', 'a\u202eb'.encode(),
    b'person@example.test', b'+15550101234', b'123-45-6789', b'/Users/person/private',
    b'{"password": "short"}', b'{"full_name": "Private Person"}',
    ('API_KEY = "sk-' + 'x' * 48 + '"').encode()])
def test_binary_secrets_and_personal_indicators_are_withheld(monkeypatch, raw, caplog):
    objects = Objects(raw)
    monkeypatch.setattr(s, 'budgeted_gh', objects.read)
    with pytest.raises(s.SourceUnavailable) as error:
        s.PinnedSourceContext(pr_info()).fetch(pr_info(), 'src/runtime.py')
    assert str(error.value) in ('source_encoding', 'source_nontext', 'source_private')
    assert raw.decode(errors='replace') not in caplog.text


def test_changed_identity_never_reuses_cached_source(objects):
    pr = pr_info()
    resolver = s.PinnedSourceContext(pr)
    resolver.fetch(pr, 'src/runtime.py')
    with pytest.raises(s.SourceUnavailable):
        resolver.fetch(replace(pr, head_sha='c' * 40), 'src/runtime.py')
    assert len(objects.calls) == 3


def test_shared_call_budget_is_not_reset_between_paths(objects):
    entry = objects.tree['tree'][0]
    objects.tree['tree'] = [dict(entry, path='src/file{}.py'.format(n)) for n in range(5)] + [objects.tree['tree'][1]]
    resolver = s.PinnedSourceContext(pr_info())
    for n in range(4): resolver.fetch(pr_info(), 'src/file{}.py'.format(n))
    with pytest.raises(s.SourceUnavailable, match='source_budget'):
        resolver.fetch(pr_info(), 'src/file4.py')
    assert len(objects.calls) == 6


def test_calls_honor_remaining_deadline_and_total_fetch_time(objects, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(s.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(s, 'remaining_timeout', lambda maximum: min(1.25, maximum))
    def read(argv, **kwargs):
        assert kwargs['timeout'] == 1.25
        clock[0] += 11
        return objects.read(argv, **kwargs)
    monkeypatch.setattr(s, 'budgeted_gh', read)
    with pytest.raises(s.SourceUnavailable, match='source_read'):
        s.PinnedSourceContext(pr_info()).fetch(pr_info(), 'src/runtime.py')
    assert len(objects.calls) == 3  # Third response exceeded the total30s bound.


def test_transport_errors_never_expose_remote_values(monkeypatch):
    def fail(*args, **kwargs): raise RuntimeError('private response and credential')
    monkeypatch.setattr(s, 'budgeted_gh', fail)
    with pytest.raises(s.SourceUnavailable) as error:
        s.PinnedSourceContext(pr_info()).fetch(pr_info(), 'src/runtime.py')
    assert str(error.value) == 'source_read'
    assert str(s.SourceUnavailable('private response')) == 'source_unavailable'


def test_real_read_wrapper_reserves_shared_core_budget(objects, monkeypatch):
    from cluster.control_plane import github_reads
    reserved = []
    monkeypatch.setattr(github_reads.budget, 'reserve', reserved.append)
    monkeypatch.setattr(s, 'budgeted_gh', github_reads.run)
    def transport(argv, **kwargs):
        assert argv[0] == 'gh' and kwargs['timeout'] <= 10
        return objects.read(argv[1:], timeout=kwargs['timeout'])
    monkeypatch.setattr(github_reads.subprocess, 'run', transport)
    s.PinnedSourceContext(pr_info()).fetch(pr_info(), 'src/runtime.py')
    assert reserved == ['core'] * 3


@pytest.mark.parametrize('requested', [None, 'src/runtime.py', [None], [{}], ['x.py'] * 5])
def test_malformed_source_request_schema_is_error_without_values(requested):
    payload = json.dumps(dict(verdict='PASS', reason='Reviewed the supplied implementation carefully',
        requested_source_files=requested, findings={'risk_notes': ['Checked implementation']}))
    result = r.CodexReviewer()._parse_structured_verdict(payload, pr_info(), r.ReviewTier.HIGH)
    assert result.verdict == r.ReviewVerdict.ERROR
    assert result.requested_source_files == [] and result.raw_response == ''
    assert result.reason == 'Invalid source context request; retry required'


def test_joint_schema_limit_and_prompt_contract():
    payload = dict(verdict='BORDERLINE', reason='Need exact implementation source to verify', findings={},
        requested_files=['docs.md', 'app.py'], requested_source_files=['a.py', 'b.py', 'c.py'])
    assert r.CodexReviewer()._parse_structured_verdict(json.dumps(payload), pr_info(), r.ReviewTier.HIGH).verdict == r.ReviewVerdict.ERROR
    payload['requested_source_files'].pop()
    result = r.CodexReviewer()._parse_structured_verdict(json.dumps(payload), pr_info(), r.ReviewTier.HIGH)
    assert result.requested_source_files == ['a.py', 'b.py']
    assert result.to_dict()['requested_source_files'] == ['a.py', 'b.py']
    assert 'Both request arrays together may contain at most four paths' in r.STRUCTURED_REVIEW_PROMPT


def source_vote():
    return replace(vote(r.ReviewVerdict.BORDERLINE), requested_source_files=['src/runtime.py'])


def test_unchanged_source_requires_real_followup_votes_from_both_reviewers(objects):
    primary, second = [], []
    def review(pr, calls):
        calls.append(pr)
        if 'Requested immutable source context' not in pr.description: return source_vote()
        packet = pr.description.split('Requested immutable source context at exact head ' + pr.head_sha + ':\n', 1)[1]
        assert json.loads(packet.split('\nTreat context', 1)[0])[0]['source'] == objects.raw.decode()
        assert pr.head_sha == 'a' * 40
        return vote()
    reviewer = Mock(review=lambda pr, *args: review(pr, primary))
    with patch.object(r, 'get_available_reviewers', return_value=[('codex', reviewer)]), \
         patch.object(r.MiniMaxReviewer, 'is_available', return_value=(True, 'ok')), \
         patch.object(r.MiniMaxReviewer, 'review_as_second_judge', side_effect=lambda pr, *a: replace(review(pr, second), reviewer='minimax')):
        outcome, reviews = r.run_review_chain(pr_info(), KarenConfig())
    assert outcome.passed
    chunks = r.split_review_diff(pr_info().diff)
    assert len(primary) == len(second) == len(chunks) * 2
    assert ''.join(pr.diff for pr in primary[::2]) == pr_info().diff
    assert len(objects.calls) == 3  # Review-local immutable proof shared, votes independent.
    for result in reviews:
        assert result.scope['source_context'][0]['blob_sha'] == objects.sha
        assert 'source' not in result.scope['source_context'][0]


def test_unavailable_source_never_triggers_followup_or_pass(objects):
    objects.tree['tree'] = []
    invoke = Mock(return_value=source_vote())
    result, _ = r._review_with_requested_context(invoke, pr_info(), pr_info())
    assert result.verdict == r.ReviewVerdict.BORDERLINE and result.scope['context_incomplete']
    assert result.scope['source_context_error'] == 'source_metadata'
    assert invoke.call_count == 1


def test_resupplied_source_is_not_a_pass_and_only_one_followup_is_allowed(objects):
    invoke = Mock(return_value=replace(source_vote(), verdict=r.ReviewVerdict.PASS))
    result, _ = r._review_with_requested_context(invoke, pr_info(), pr_info())
    assert result.verdict == r.ReviewVerdict.BORDERLINE and result.scope['context_incomplete']
    assert invoke.call_count == 2


def test_real_changed_line_defect_survives_successful_source_fetch(objects):
    invoke = Mock(side_effect=[source_vote(), vote(r.ReviewVerdict.FAIL,
        'app.py:1 invokes unsafe with unvalidated user input')])
    result, _ = r._review_with_requested_context(invoke, pr_info(), pr_info())
    assert result.verdict == r.ReviewVerdict.FAIL
    assert result.scope['source_context'][0]['head_sha'] == pr_info().head_sha


def test_transport_admission_cannot_authorize_unresolved_source():
    from cluster.control_plane.local_karen.verdict import create_verdict
    from cluster.control_plane.local_karen.verdict_push import reviewed
    primary = vote()
    second = replace(vote(), reviewer='minimax')
    verdict = create_verdict(pr_info(), [], [primary, second], True,
                             'Both independent reviewers passed', 'chain')
    assert reviewed(verdict)
    verdict.review_results[0]['requested_source_files'] = ['src/runtime.py']
    assert not reviewed(verdict)


def test_orchestrator_joint_limit_does_not_depend_on_provider_parser(objects):
    invoke = Mock(return_value=replace(source_vote(), requested_files=['docs.md'] * 4))
    result, _ = r._review_with_requested_context(invoke, pr_info(), pr_info())
    assert result.verdict == r.ReviewVerdict.ERROR
    assert result.scope['context_incomplete'] and invoke.call_count == 1
    assert objects.calls == []


def test_new_module_uses_python39_grammar():
    import ast
    ast.parse(Path(s.__file__).read_text(), feature_version=(3, 9))
