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


def oid(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


@pytest.fixture
def packet(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args],
                                       cwd=tmp_path).decode()
    git('init', '-q')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@invalid')
    path = 'src/runtime.py'
    (tmp_path / 'src').mkdir()
    raw = ''.join('def check_{}(value):\n    # bounded implementation context {}\n'
                  '    normalized = value.strip()\n    return normalized\n\n'.format(i, 'x' * 80)
                  for i in range(140)).encode()
    (tmp_path / path).write_bytes(raw)
    git('add', path)
    git('commit', '-qm', 'Before')
    base = git('rev-parse', 'HEAD').strip()
    changed = raw.replace(b'def check_70(value):', b'def check_70(value, strict=False):')
    (tmp_path / path).write_bytes(changed)
    git('commit', '-qam', 'After')
    head = git('rev-parse', 'HEAD').strip()
    original = git('diff', '--no-ext-diff', '--no-textconv', '--full-index', base, head)
    expanded = git('diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--full-index',
                   '--function-context', '--unified=16', base, head)
    # A second file makes this a genuinely chunked review. Its original bytes
    # remain in static checks and in the unmodified chunk plan.
    document = ('diff --git a/guide.md b/guide.md\nnew file mode 100644\n'
                '--- /dev/null\n+++ b/guide.md\n@@ -0,0 +1 @@\n+' + 'guidance ' * 1550 + '\n')
    pr = PRInfo('owner', 'repo', 17, head, 'Fix runtime', 'author', False,
                original + document, {'tests': 'success'}, [path, 'guide.md'], [])
    blobs = {path: (d.Blob(oid(raw), '100644', raw), d.Blob(oid(changed), '100644', changed))}
    info = {path: dict(path=path, head_sha=head, blob_sha=oid(changed), bytes=len(changed),
                       mode='100644', tree_sha=git('rev-parse', head + '^{tree}').strip())}
    candidate = d.GitContext((pr.owner, pr.repo, pr.number, head), base, expanded, blobs)
    return pr, base, candidate, info, git, tmp_path


def enrich(packet, loader=None):
    pr, base, candidate, info, _, _ = packet
    listing = {'sha': next(iter(info.values()))['tree_sha'], 'truncated': False, 'tree': [
        dict(path='src', type='tree', mode='040000', sha='d' * 40),
        dict(path='src/runtime.py', type='blob', mode='100644',
             sha=info['src/runtime.py']['blob_sha'], size=info['src/runtime.py']['bytes'])]}
    reads = []
    def read(argv, **kwargs):
        reads.append(argv)
        response = ({'sha': pr.head_sha, 'tree': {'sha': listing['sha']}}
                    if '/commits/' in argv[-1] else listing)
        return subprocess.CompletedProcess(argv, 0, json.dumps(response), '')
    with patch.object(s, 'budgeted_gh', side_effect=read):
        result = d.prepare_context(pr, base, loader or (lambda *args: candidate))
    return result, reads


def test_large_file_surroundings_are_available_without_complete_source(packet):
    pr, _, candidate, _, _, _ = packet
    result, reads = enrich(packet)
    assert len(candidate.blobs['src/runtime.py'][1].data) > s.MAX_FILE_BYTES
    assert result.diff == pr.diff and len(reads) == 2
    assert result.pinned_diff_context.source_bytes == result.pinned_source_context.bytes
    assert result.pinned_source_context.bytes < s.MAX_SOURCE_BYTES
    supplied = c.requested_file_diff(result, 'src/runtime.py', pr.head_sha)
    assert 'normalized = value.strip()' in supplied
    assert c.requested_file_diff(result, 'guide.md') == c.requested_file_diff(pr, 'guide.md')
    with pytest.raises(s.SourceUnavailable) as error:
        s.PinnedSourceContext(result).fetch(result, 'src/runtime.py')
    assert error.value.metadata_reason == 'source_file_limit'
    assert s.PinnedSourceContext(result) is result.pinned_source_context
    assert result.pinned_source_context.calls == 2


def test_verified_context_reaches_each_normal_chunk_and_renderer(packet):
    pr, _, _, _, _, _ = packet
    result, _ = enrich(packet)
    chunks = r.split_review_diff(pr.diff)
    assert len(chunks) > 1 and ''.join(chunks) == result.diff
    for index, chunk in enumerate(chunks, 1):
        part = replace(result, diff=chunk, review_scope=c.build_review_scope(result, chunks, index))
        initial = c.automatic_complement(result, chunks, index)
        seen = []
        def invoke(value):
            seen.append(value)
            assert 'normalized = value.strip()' in str(value.review_material.entries)
            prompt = r.build_review_prompt(value)
            assert 'normalized = value.strip()' in prompt
            assert 'pinned_source_context' not in prompt
            # Offline delivery checks never manufacture a passing reviewer.
            return r.ReviewResult('codex', r.ReviewVerdict.ERROR, 'Offline delivery probe')
        outcome, votes = r._review_with_requested_context(invoke, part, result,
            source_context=s.PinnedSourceContext(result), initial_context=initial)
        assert outcome.verdict == r.ReviewVerdict.ERROR and len(seen) == len(votes) == 1
        assert seen[0].review_material.phase == 'supplemental'
        assert max(c.context_size(list(seen[0].review_material.entries))) <= c.MAX_CONTEXT_SIZE


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


def test_failed_enrichment_cannot_reset_budget_or_replace_original_diff(packet):
    result, _ = enrich(packet)
    shared = result.pinned_source_context
    shared.calls, shared.bytes, shared.seconds = 6, 47999, .5
    assert s.PinnedSourceContext(result) is shared
    assert (shared.calls, shared.bytes, shared.seconds) == (6, 47999, .5)
    with pytest.raises(s.SourceUnavailable):
        shared._json('blobs/' + 'a' * 40, 16000)
    changed = replace(result, head_sha='f' * 40)
    assert c.requested_file_diff(changed, 'src/runtime.py') == c.requested_file_diff(
        replace(packet[0], head_sha='f' * 40), 'src/runtime.py')
    assert s.PinnedSourceContext(changed) is not shared


def test_explicit_full_source_request_still_fails_in_normal_followup(packet):
    result, _ = enrich(packet)
    chunks = r.split_review_diff(result.diff)
    part = replace(result, diff=chunks[0], review_scope=c.build_review_scope(result, chunks, 1))
    def invoke(value):
        return r.ReviewResult('codex', r.ReviewVerdict.BORDERLINE,
                             'Need complete source', requested_source_files=['src/runtime.py'])
    outcome, votes = r._review_with_requested_context(invoke, part, result,
        source_context=s.PinnedSourceContext(result), initial_context=c.automatic_complement(result, chunks, 1))
    assert outcome.verdict == r.ReviewVerdict.BORDERLINE
    assert outcome.requested_source_files == ['src/runtime.py']
    assert outcome.scope['context_incomplete']
    assert outcome.scope['context_resolution']['code'] == 'source_unavailable'
    assert len(votes) == 1


def test_no_newline_changes_are_not_silently_normalized():
    section = ('diff --git a/a.py b/a.py\nindex ' + 'a' * 40 + '..' + 'b' * 40 +
               ' 100644\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n'
               '\\ No newline at end of file\n+new\n')
    events, views = d.parse_section(section)
    assert events == [('-', 1, b'old'), ('+', 1, b'new\n')]
    assert views == ({1: b'old'}, {1: b'new\n'})
    with pytest.raises(d.ContextUnavailable):
        d.parse_section(section.replace('@@ -1 +1 @@', '@@ -1,2 +1 @@'))


def test_context_identity_cannot_be_supplied_by_pr_prose(packet):
    pr, _, candidate, _, _, _ = packet
    result, _ = enrich(packet)
    assert d.lookup_context(replace(pr, description=repr(candidate)), 'src/runtime.py') is None
    assert d.lookup_context(replace(result, diff=pr.diff + '\n'), 'src/runtime.py') is None
    assert c.requested_file_diff(result, '../runtime.py') is None
    assert c.requested_file_diff(result, 'src/runtime.py', 'f' * 40) is None


def test_transport_failure_retains_original_material_and_consumed_budget(packet, caplog):
    def unavailable(pr, base, info, shared):
        shared.seconds -= 3
        raise OSError('remote-output-must-remain-private')
    with caplog.at_level('INFO'):
        result, reads = enrich(packet, unavailable)
    assert result.diff == packet[0].diff and result.pinned_diff_context is None
    shared = s.PinnedSourceContext(result)
    assert len(reads) == shared.calls == 2 and shared.seconds < 28
    assert 'error_type=OSError' in caplog.text
    assert 'remote-output-must-remain-private' not in caplog.text


def test_head_move_during_context_preparation_is_refused(packet):
    from cluster.control_plane.local_karen import github_client as gh
    pr = packet[0]
    result, _ = enrich(packet)
    view = dict(headRefOid=pr.head_sha, baseRefOid=packet[1],
                files=[{'path': path} for path in pr.changed_files])
    with patch.object(gh, '_run_gh', side_effect=[json.dumps(view), pr.diff, '[]']), \
         patch.object(gh, 'verify_sha_current', side_effect=[True, False]) as verify, \
         patch.object(d, 'prepare_context', return_value=result):
        with pytest.raises(gh.GitHubError, match='head changed while fetching context'):
            gh.fetch_pr_info(pr.owner, pr.repo, pr.number)
    assert verify.call_count == 2


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
