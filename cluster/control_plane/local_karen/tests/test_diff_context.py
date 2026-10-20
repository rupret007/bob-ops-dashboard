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

def test_expanded_partial_seed_is_unique_and_keeps_other_integration_file(packet):
    pr, base, candidate, info, _, _ = packet
    path = 'bridge.py'
    old, new = b'old\n', b'new\n'
    section = ('diff --git a/{0} b/{0}\nindex {1}..{2} 100644\n'
               '--- a/{0}\n+++ b/{0}\n@@ -1 +1 @@\n-old\n+new\n').format(path, oid(old), oid(new))
    pr = replace(pr, diff=pr.diff + section, changed_files=pr.changed_files + [path])
    candidate = replace(candidate, diff=candidate.diff + section, blobs=dict(candidate.blobs,
        **{path: (d.Blob(oid(old), '100644', old), d.Blob(oid(new), '100644', new))}))
    info = dict(info, **{path: dict(blob_sha=oid(new), bytes=len(new))})
    proven = d.validate_expansion(pr, candidate, base, info)
    result = d.ContextualPRInfo(**vars(pr), pinned_diff_context=proven)
    boundary = pr.diff.index('\n@@ ') + 1
    entries, code = c.automatic_complement(result, [pr.diff[:boundary], pr.diff[boundary:]], 1)
    paths = [item['path'] for item in entries]
    assert code == 'partial_file_supplied'
    assert len(paths) == len(set(paths)) == 3
    assert set(paths) == set(pr.changed_files) and paths[0] == 'src/runtime.py'
    assert max(c.context_size(entries)) <= c.MAX_CONTEXT_SIZE
