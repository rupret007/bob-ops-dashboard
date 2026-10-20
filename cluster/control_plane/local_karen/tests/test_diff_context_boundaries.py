"""Real Git hunks must remain bounded evidence, never review authority."""
from dataclasses import replace
import hashlib
import json
import subprocess
from unittest.mock import patch

import pytest

from cluster.control_plane.local_karen import chunk_context as c, diff_context as d
from cluster.control_plane.local_karen import reviewers as r, source_context as s
from cluster.control_plane.local_karen.checks import PRInfo

from .diff_context_cases import oid, packet, enrich


@pytest.mark.parametrize('kind', ['context', 'addition', 'coordinate', 'head', 'base', 'blob',
                                 'mode', 'manifest', 'index', 'header'])
def test_forged_or_misbound_expansions_are_refused(packet, kind):
    pr, base, candidate, info, _, _ = packet
    if kind == 'context':
        # Same changed events, fabricated unchanged source: equivalence alone
        # would incorrectly accept this patch.
        candidate = replace(candidate, diff=candidate.diff.replace(
            '     normalized = value.strip()', '     normalized = unsafe(value)', 1))
    elif kind == 'addition': candidate = replace(candidate, diff=candidate.diff.replace('strict=False', 'strict=True'))
    elif kind == 'coordinate': candidate = replace(candidate, diff=candidate.diff.replace('@@ -', '@@ -9', 1))
    elif kind == 'head': candidate = replace(candidate, identity=('owner', 'repo', 17, 'e' * 40))
    elif kind == 'base': candidate = replace(candidate, base='f' * 40)
    elif kind == 'blob':
        old, new = candidate.blobs['src/runtime.py']
        candidate = replace(candidate, blobs={'src/runtime.py': (old, replace(new, data=new.data + b' '))})
    elif kind == 'mode': candidate = replace(candidate, diff=candidate.diff.replace('100644', '100755', 1))
    elif kind == 'manifest': candidate = replace(candidate, blobs={})
    elif kind == 'index': candidate = replace(candidate, diff=candidate.diff.replace(info['src/runtime.py']['blob_sha'], 'a' * 40))
    elif kind == 'header': candidate = replace(candidate, diff=candidate.diff.replace('--- a/src/runtime.py', '--- a/elsewhere.py'))
    with pytest.raises(d.ContextUnavailable):
        d.validate_expansion(pr, candidate, base, info)

@pytest.mark.parametrize('value', ['person' + '@' + 'example' + '.test', '+1' + '5550101234'])
def test_actual_private_source_in_extra_context_is_withheld(packet, value):
    pr, base, candidate, info, _, _ = packet
    old, new = candidate.blobs['src/runtime.py']
    target = b'    normalized = value.strip()'
    raw = ('    normalized = "' + value + '"').encode()
    old = replace(old, data=old.data.replace(target, raw))
    new = replace(new, data=new.data.replace(target, raw))
    old, new = replace(old, sha=oid(old.data)), replace(new, sha=oid(new.data))
    original_index = candidate.diff.splitlines()[1]
    candidate = replace(candidate, diff=candidate.diff.replace(target.decode(), raw.decode()).replace(
        original_index, 'index {}..{} 100644'.format(old.sha, new.sha)),
        blobs={'src/runtime.py': (old, new)})
    # Rebind the original GitHub index as it would be for this pinned fixture.
    pr = replace(pr, diff=pr.diff.replace(original_index, 'index {}..{} 100644'.format(old.sha, new.sha)))
    info['src/runtime.py'].update(blob_sha=new.sha, bytes=len(new.data))
    with pytest.raises(d.ContextUnavailable, match='source_private'):
        d.validate_expansion(pr, candidate, base, info)

def test_no_newline_changes_are_not_silently_normalized():
    section = ('diff --git a/a.py b/a.py\nindex ' + 'a' * 40 + '..' + 'b' * 40 +
               ' 100644\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n'
               '\\ No newline at end of file\n+new\n')
    events, views = d.parse_section(section)
    assert events == [('-', 1, b'old'), ('+', 1, b'new\n')]
    assert views == ({1: b'old'}, {1: b'new\n'})
    with pytest.raises(d.ContextUnavailable):
        d.parse_section(section.replace('@@ -1 +1 @@', '@@ -1,2 +1 @@'))

@pytest.mark.parametrize('size,accepted', [(16000, True), (16001, False)])
def test_exact_per_revision_source_view_bound(size, accepted):
    raw = b'#' + b'x' * (size - 2) + b'\n'
    blob = d.Blob(oid(raw), '100644', raw)
    if accepted:
        assert d._validate_blob(blob, blob.sha, {1: raw}) == size
    else:
        with pytest.raises(d.ContextUnavailable, match='file_budget'):
            d._validate_blob(blob, blob.sha, {1: raw})

@pytest.mark.parametrize('extra,accepted', [(0, True), (1, False)])
def test_four_file_combined_source_view_bound(extra, accepted):
    original, expanded, blobs, info = [], [], {}, {}
    for i in range(4):
        path = 'source{}.py'.format(i)
        context = '#' + 'x' * (5994 + (extra if i == 0 else 0)) + '\n'
        before, after = (context + 'old\n').encode(), (context + 'new\n').encode()
        old, new = oid(before), oid(after)
        header = ('diff --git a/{0} b/{0}\nindex {1}..{2} 100644\n'
                  '--- a/{0}\n+++ b/{0}\n').format(path, old, new)
        original.append(header + '@@ -2 +2 @@\n-old\n+new\n')
        expanded.append(header + '@@ -1,2 +1,2 @@\n ' + context + '-old\n+new\n')
        blobs[path] = d.Blob(old, '100644', before), d.Blob(new, '100644', after)
        info[path] = dict(blob_sha=new, bytes=len(after))
    pr = PRInfo('owner', 'repo', 1, 'a' * 40, 'Bound', 'author', False, ''.join(original), {}, list(blobs), [])
    candidate = d.GitContext(('owner', 'repo', 1, 'a' * 40), 'b' * 40, ''.join(expanded), blobs)
    if accepted:
        assert d.validate_expansion(pr, candidate, 'b' * 40, info).source_bytes == 48000
    else:
        with pytest.raises(d.ContextUnavailable, match='context_budget'):
            d.validate_expansion(pr, candidate, 'b' * 40, info)
