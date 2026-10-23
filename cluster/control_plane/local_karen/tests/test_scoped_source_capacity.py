"""Bounded synthetic Git objects; no real model, network, credential or vault."""
import base64
from contextvars import copy_context
from dataclasses import replace
import hashlib
import json
import subprocess
from unittest.mock import Mock

import pytest

from cluster.control_plane.local_karen import packet_scope as p, source_context as s
from cluster.control_plane.local_karen import reviewers as r
from cluster.control_plane.local_karen.tests.test_chunk_evidence import pr_info, vote

AUTH = 'd' * 64
PATH = 'src/runtime.py'
RAW = b'value = 1\n' * 4724


class GitObjects:
    def __init__(self, files):
        self.files, self.calls, self.blobs = files, [], {}
        tree = [dict(path='src', type='tree', mode='040000', sha='c' * 40)]
        for path, raw in files.items():
            sha = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            tree.append(dict(path=path, type='blob', mode='100644', size=len(raw), sha=sha))
            self.blobs[sha] = dict(sha=sha, size=len(raw), encoding='base64',
                                  content=base64.b64encode(raw).decode())
        self.tree = dict(sha='b' * 40, truncated=False, tree=tree)

    def read(self, argv, **kwargs):
        self.calls.append(argv[-1])
        endpoint = argv[-1]
        value = (dict(sha='a' * 40, tree=dict(sha='b' * 40)) if '/commits/' in endpoint
                 else self.tree if '/trees/' in endpoint else self.blobs[endpoint.rsplit('/', 1)[1]])
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), '')

    def pin(self, path):
        raw = self.files[path]
        entry = next(item for item in self.tree['tree'] if item['path'] == path)
        return dict(path=path, bytes=len(raw), blob_sha=entry['sha'],
                    sha256=hashlib.sha256(raw).hexdigest())


def intent(tmp_path, objects, *, legacy=False, **changes):
    value = dict(repo='owner/repo', pr=pr_info().number, head='a' * 40,
                 issued_at=1000, expires_at=4600, entries=4, packet_bytes=96000,
                 authorization_sha256=AUTH, file_bytes=48000, source_bytes=48000,
                 sources=[objects.pin(PATH)])
    if legacy:
        for name in ('file_bytes', 'source_bytes', 'sources'):
            del value[name]
        value['entries'] = 8
    value.update(changes)
    path = tmp_path / 'intent.json'
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return p.operator_packet_scope(pr_info(), path,
        intent_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), authorization_sha256=AUTH)


@pytest.fixture
def objects(monkeypatch):
    objects = GitObjects({PATH: RAW})
    monkeypatch.setattr(s, 'budgeted_gh', objects.read)
    monkeypatch.setattr(p.time, 'time', lambda: 1001)
    return objects


def test_full_large_source_requires_exact_scope_and_cannot_escape_cache(objects, tmp_path):
    source = s.PinnedSourceContext(pr_info())
    with pytest.raises(s.SourceUnavailable) as error:
        source.fetch(pr_info(), PATH)
    assert error.value.metadata_reason == 'source_file_limit' and len(objects.calls) == 2
    with intent(tmp_path, objects):
        assert p.packet_limits(pr_info()) == (4, 96000)
        result = source.fetch(pr_info(), PATH)
        assert result['source'].encode() == RAW and result['bytes'] == 47240
        assert source.cached(pr_info(), PATH) == result and len(objects.calls) == 3
        copied = copy_context()
    with pytest.raises(s.SourceUnavailable):
        source.cached(pr_info(), PATH)
    with pytest.raises(s.SourceUnavailable):
        copied.run(source.cached, pr_info(), PATH)
    assert p.packet_limits(pr_info()) == (4, 48000)
    assert (s.MAX_FILE_BYTES, s.MAX_SOURCE_BYTES, s.MAX_API_CALLS) == (16000, 48000, 6)


def test_legacy_packet_scope_never_expands_source(objects, tmp_path):
    with intent(tmp_path, objects, legacy=True):
        with pytest.raises(s.SourceUnavailable) as error:
            s.PinnedSourceContext(pr_info()).fetch(pr_info(), PATH)
    assert error.value.metadata_reason == 'source_file_limit' and len(objects.calls) == 2


@pytest.mark.parametrize('field', ['bytes', 'blob_sha', 'sha256'])
def test_pin_mismatch_never_shares_or_caches_source(objects, tmp_path, field):
    pin = objects.pin(PATH)
    pin[field] = len(RAW) - 1 if field == 'bytes' else 'e' * (40 if field == 'blob_sha' else 64)
    source = s.PinnedSourceContext(pr_info())
    with intent(tmp_path, objects, sources=[pin]):
        with pytest.raises(s.SourceUnavailable):
            source.fetch(pr_info(), PATH)
    assert not source.sources and source.bytes == 0
    assert len(objects.calls) == (3 if field == 'sha256' else 2)


def test_expiry_during_remote_read_refuses_before_caching(objects, tmp_path, monkeypatch):
    def read(argv, **kwargs):
        result = objects.read(argv, **kwargs)
        if '/blobs/' in argv[-1]:
            monkeypatch.setattr(p.time, 'time', lambda: 4600)
        return result
    monkeypatch.setattr(s, 'budgeted_gh', read)
    source = s.PinnedSourceContext(pr_info())
    with intent(tmp_path, objects):
        with pytest.raises(s.SourceUnavailable):
            source.fetch(pr_info(), PATH)
    assert len(objects.calls) == 3 and not source.sources and source.bytes == 0


def test_expired_or_wrong_review_refuses_before_object_read(objects, tmp_path, monkeypatch):
    source = s.PinnedSourceContext(pr_info())
    with intent(tmp_path, objects):
        with pytest.raises(s.SourceUnavailable):
            source.fetch(replace(pr_info(), number=pr_info().number + 1), PATH)
        monkeypatch.setattr(p.time, 'time', lambda: 4600)
        with pytest.raises(s.SourceUnavailable):
            source.fetch(pr_info(), PATH)
    assert objects.calls == []


def test_unlisted_large_source_keeps_ordinary_file_limit(objects, tmp_path):
    pin = dict(objects.pin(PATH), path='src/other.py')
    with intent(tmp_path, objects, sources=[pin]):
        with pytest.raises(s.SourceUnavailable) as error:
            s.PinnedSourceContext(pr_info()).fetch(pr_info(), PATH)
    assert error.value.metadata_reason == 'source_file_limit' and len(objects.calls) == 2


def test_all_paths_share_cumulative_source_budget(tmp_path, monkeypatch):
    objects = GitObjects({PATH: b'x = 1\n' * 3333, 'src/second.py': b'x = 1\n' * 3333,
                          'src/ordinary.py': b'x = 2\n' * 1667})
    monkeypatch.setattr(s, 'budgeted_gh', objects.read)
    monkeypatch.setattr(p.time, 'time', lambda: 1001)
    source = s.PinnedSourceContext(pr_info())
    with intent(tmp_path, objects, sources=[objects.pin(PATH), objects.pin('src/second.py')]):
        source.fetch(pr_info(), PATH)
        source.fetch(pr_info(), 'src/second.py')
        with pytest.raises(s.SourceUnavailable) as error:
            source.fetch(pr_info(), 'src/ordinary.py')
    assert error.value.metadata_reason == 'source_total_limit' and len(objects.calls) == 4
    assert source.bytes == 39996


@pytest.mark.parametrize('private', [b'person@example.test', b'{"password": "short"}'])
def test_approved_large_source_still_requires_complete_privacy_check(tmp_path, monkeypatch, private):
    objects = GitObjects({PATH: RAW + private})
    monkeypatch.setattr(s, 'budgeted_gh', objects.read)
    monkeypatch.setattr(p.time, 'time', lambda: 1001)
    source = s.PinnedSourceContext(pr_info())
    with intent(tmp_path, objects):
        with pytest.raises(s.SourceUnavailable, match='source_private'):
            source.fetch(pr_info(), PATH)
    assert not source.sources and source.bytes == 0


@pytest.mark.parametrize('changes', [dict(entries=9), dict(entries=True), dict(entries=3),
    dict(packet_bytes=768001), dict(packet_bytes=47999), dict(file_bytes=256001),
    dict(file_bytes=True), dict(file_bytes=15999), dict(source_bytes=512001),
    dict(source_bytes=47999), dict(source_bytes=True), dict(sources=[]),
    dict(sources=[None]), dict(sources='untrusted')])
def test_extended_intents_cannot_exceed_authorized_bounds(objects, tmp_path, changes):
    with pytest.raises(ValueError):
        with intent(tmp_path, objects, **changes):
            pytest.fail('Invalid capacity activated')
    assert p.packet_limits(pr_info()) == (4, 48000) and objects.calls == []


@pytest.mark.parametrize('kind', ['duplicate', 'too-many', 'sum', 'path', 'traversal',
                                 'bool-bytes', 'unknown', 'bad-blob', 'bad-digest'])
def test_malformed_source_pins_never_activate(objects, tmp_path, kind):
    pin = objects.pin(PATH)
    sources = [pin]
    if kind == 'duplicate': sources = [pin, pin]
    elif kind == 'too-many': sources = [dict(pin, path='src/p%d.py' % i) for i in range(5)]
    elif kind == 'sum': sources = [pin, dict(pin, path='src/second.py')]
    elif kind == 'path': pin['path'] = '/tmp/value.py'
    elif kind == 'traversal': pin['path'] = 'src/../value.py'
    elif kind == 'bool-bytes': pin['bytes'] = True
    elif kind == 'unknown': pin['extra'] = 'untrusted'
    elif kind == 'bad-blob': pin['blob_sha'] = 'short'
    elif kind == 'bad-digest': pin['sha256'] = 'short'
    with pytest.raises(ValueError):
        with intent(tmp_path, objects, sources=sources):
            pytest.fail('Invalid source pin activated')


def test_complete_large_source_gets_one_real_followup_not_an_automatic_pass(objects, tmp_path):
    requested = replace(vote(r.ReviewVerdict.BORDERLINE), requested_source_files=[PATH])
    invoke = Mock(side_effect=[requested, vote()])
    with intent(tmp_path, objects):
        result, _ = r._review_with_requested_context(invoke, pr_info(), pr_info())
    assert result.passed and invoke.call_count == 2 and len(objects.calls) == 3
    packet = invoke.call_args.args[0].review_material.entries
    assert packet[0]['source'].encode() == RAW
    assert result.scope['source_context'][0]['blob_sha'] == objects.pin(PATH)['blob_sha']
    assert 'source' not in result.scope['source_context'][0]
