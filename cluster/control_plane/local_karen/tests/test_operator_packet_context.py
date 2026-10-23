"""Complete synthetic evidence packets; scope changes no source or vote guard."""
import base64
from dataclasses import replace
import hashlib
import json
import subprocess
from unittest.mock import Mock

import pytest

from cluster.control_plane.local_karen import chunk_context as c, packet_scope as p
from cluster.control_plane.local_karen import reviewers as r, source_context as s
from cluster.control_plane.local_karen.checks import PRInfo

PATHS = ['src/dependency%d.py' % i for i in range(4)]


def fixture(count=1, size=200):
    def section(name, length):
        return ('diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -0,0 +1 @@\n+'.format(name)
                + 'x' * length + '\n')
    current = section('current.py', 200)
    others = ''.join(section('other%d.py' % i, size) for i in range(count))
    pr = PRInfo('example', 'project', 1, 'b' * 40, 'Synthetic review', 'author', False,
                current + others, {}, ['current.py'] + ['other%d.py' % i for i in range(count)], [])
    chunks = [current, others]
    return pr, replace(pr, diff=current, review_scope=c.build_review_scope(pr, chunks, 1)), chunks


def activate(pr, tmp_path, monkeypatch):
    monkeypatch.setattr(p.time, 'time', lambda: 1001)
    path = tmp_path / 'scope.json'
    value = dict(repo=pr.owner + '/' + pr.repo, pr=pr.number, head=pr.head_sha,
                 issued_at=1000, expires_at=4600, entries=8, packet_bytes=96000,
                 authorization_sha256='a' * 64)
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return p.operator_packet_scope(pr, path, intent_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                   authorization_sha256='a' * 64)


def resolver(pr, monkeypatch, raw=b'value = 1\n' * 900):
    sha = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    values = [dict(sha=pr.head_sha, tree=dict(sha='c' * 40)),
              dict(sha='c' * 40, truncated=False, tree=[
                  dict(path=path, type='blob', mode='100644', size=len(raw), sha=sha) for path in PATHS]
                  + [dict(path='src', type='tree', mode='040000', sha='d' * 40)]),
              dict(sha=sha, size=len(raw), encoding='base64', content=base64.b64encode(raw).decode())]
    def read(argv, **kwargs):
        index = 0 if '/commits/' in argv[-1] else 1 if '/trees/' in argv[-1] else 2
        return subprocess.CompletedProcess(argv, 0, json.dumps(values[index]), '')
    reader = Mock(side_effect=read)
    monkeypatch.setattr(s, 'budgeted_gh', reader)
    return s.PinnedSourceContext(pr), reader


def vote(verdict=r.ReviewVerdict.PASS, paths=()):
    return r.ReviewResult('gemini', verdict, 'Checked complete synthetic evidence',
                         findings=r.ReviewFindings(risk_notes=['Checked exact supplied files']),
                         requested_source_files=list(paths))


@pytest.mark.parametrize('count,size', [(1, 200), (4, 10000)])
def test_recorded_five_and_eight_entry_shapes_keep_every_required_file(tmp_path, monkeypatch, count, size):
    pr, part, chunks = fixture(count, size)
    source, reader = resolver(pr, monkeypatch)
    original = c.automatic_complement(pr, chunks, 1)
    default = Mock(return_value=vote(r.ReviewVerdict.BORDERLINE, PATHS))
    result, _ = r._review_with_requested_context(default, part, pr, source, original)
    assert not result.passed and result.scope['context_resolution']['code'] == 'context_budget'
    assert default.call_count == 1 and reader.call_count == 0
    with activate(pr, tmp_path, monkeypatch):
        initial = c.automatic_complement(pr, chunks, 1)
        invoke = Mock(side_effect=[vote(r.ReviewVerdict.BORDERLINE, PATHS), vote()])
        result, _ = r._review_with_requested_context(invoke, part, pr, source, initial)
        assert result.passed and invoke.call_count == 2 and reader.call_count == 6
        packet = list(invoke.call_args.args[0].review_material.entries)
        assert packet[:count] == original[0] == initial[0]
        assert len(packet) == count + 4 and {x['path'] for x in packet[count:]} == set(PATHS)
        assert max(c.context_size(packet)) <= 96000
        assert sum(x['bytes'] for x in packet[count:]) == 36000
        assert invoke.call_args.args[0].head_sha == pr.head_sha
    assert p.packet_limits(pr) == (4, 48000)
    assert (s.MAX_FILE_BYTES, s.MAX_SOURCE_BYTES, s.MAX_API_CALLS) == (16000, 48000, 6)


@pytest.mark.parametrize('kind', ['entries', 'packet_bytes', 'file', 'source_total', 'private', 'requests'])
def test_scoped_review_still_refuses_other_limits_and_private_source(tmp_path, monkeypatch, kind):
    pr, part, chunks = fixture(5 if kind == 'entries' else 4, 17000 if kind == 'packet_bytes' else 200)
    raw = b'x' * (16001 if kind == 'file' else 12001 if kind == 'source_total' else 9000)
    if kind == 'private':
        raw = b'{"password": "synthetic"}'
    source, reader = resolver(pr, monkeypatch, raw)
    invoke = Mock(return_value=vote(r.ReviewVerdict.BORDERLINE,
                                  PATHS + ['src/extra.py'] if kind == 'requests' else PATHS))
    with activate(pr, tmp_path, monkeypatch):
        result, _ = r._review_with_requested_context(invoke, part, pr, source,
                                                    c.automatic_complement(pr, chunks, 1))
    assert not result.passed and invoke.call_count == 1 and reader.call_count <= 6
    if kind in ('entries', 'requests'):
        assert reader.call_count == 0
    elif kind == 'private':
        assert result.scope['source_context_error'] == 'source_private'
    else:
        assert result.scope['source_context_error'] == 'source_budget'


def test_expiry_during_provider_call_stops_before_a_followup(tmp_path, monkeypatch):
    pr, part, chunks = fixture()
    source, reader = resolver(pr, monkeypatch)
    def expired(part):
        monkeypatch.setattr(p.time, 'time', lambda: 4600)
        return vote(r.ReviewVerdict.BORDERLINE, PATHS)
    invoke = Mock(side_effect=expired)
    with activate(pr, tmp_path, monkeypatch):
        with pytest.raises(ValueError):
            r._review_with_requested_context(invoke, part, pr, source,
                                             c.automatic_complement(pr, chunks, 1))
    assert invoke.call_count == 1 and reader.call_count == 0


@pytest.mark.parametrize('reject', [False, True])
def test_single_followup_and_grounded_fail_remain_authoritative(tmp_path, monkeypatch, reject):
    pr, part, chunks = fixture()
    source, reader = resolver(pr, monkeypatch)
    final = vote(r.ReviewVerdict.FAIL if reject else r.ReviewVerdict.BORDERLINE, () if reject else PATHS)
    if reject:
        final.findings.functional_issues.append('current.py:1 references an undefined name')
    invoke = Mock(side_effect=[vote(r.ReviewVerdict.BORDERLINE, PATHS), final])
    with activate(pr, tmp_path, monkeypatch):
        result, _ = r._review_with_requested_context(invoke, part, pr, source,
                                                    c.automatic_complement(pr, chunks, 1))
    assert not result.passed and invoke.call_count == 2 and reader.call_count == 6
    assert result.verdict == (r.ReviewVerdict.FAIL if reject else r.ReviewVerdict.BORDERLINE)
