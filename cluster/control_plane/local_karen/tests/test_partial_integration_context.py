"""A partial implementation must retain small cross-file integration evidence."""
from dataclasses import replace
from unittest.mock import Mock, patch

from cluster.control_plane.local_karen import chunk_context as c, reviewers as r
from cluster.control_plane.local_karen.tests.test_auto_complement import pr_info, section, vote


def packet():
    pr = pr_info([section('implementation.py', 20000), section('checks.py', 700),
                  section('bridge.py', 900), section('tests.py', 19000)])
    chunks = r.split_review_diff(pr.diff)
    part = replace(pr, diff=chunks[0], review_scope=c.build_review_scope(pr, chunks, 1))
    return pr, chunks, part


def test_partial_file_and_requested_checks_keep_the_actual_bridge_diff():
    pr, chunks, part = packet()
    supplied = []
    def review(value):
        entries = list(value.review_material.entries)
        supplied.append(entries)
        if len(supplied) == 1:
            return vote(r.ReviewVerdict.BORDERLINE, changed=['checks.py', 'tests.py'])
        paths = {item['path']: item['diff'] for item in entries}
        assert paths['bridge.py'] == c.requested_file_diff(pr, 'bridge.py', pr.head_sha)
        assert paths['implementation.py'] == c.requested_file_diff(pr, 'implementation.py', pr.head_sha)
        assert paths['tests.py'] == c.requested_file_diff(pr, 'tests.py', pr.head_sha)
        # Evidence packaging is not authority. This offline judge never votes
        # PASS, runs models, signs a verdict or performs another follow-up.
        return vote(r.ReviewVerdict.ERROR)
    result, _ = r._review_with_requested_context(review, part, pr,
        initial_context=c.automatic_complement(pr, chunks, 1))
    assert result.verdict == r.ReviewVerdict.ERROR
    assert len(supplied) == 2
    assert all(len(entries) <= c.MAX_CONTEXT_FILES and
               max(c.context_size(entries)) <= c.MAX_CONTEXT_SIZE for entries in supplied)


def test_automatic_partial_context_reserves_one_slot_without_source_or_network_reads():
    pr, chunks, _ = packet()
    with patch('cluster.control_plane.local_karen.source_context.PinnedSourceContext.fetch',
               side_effect=AssertionError('no automatic source reads')):
        entries, code = c.automatic_complement(pr, chunks, 1)
    assert code == 'partial_file_supplied'
    assert {item['path'] for item in entries} == {'implementation.py', 'checks.py', 'bridge.py'}
    assert len(entries) < c.MAX_CONTEXT_FILES
    assert max(c.context_size(entries)) <= c.MAX_CONTEXT_SIZE


def test_optional_integration_never_discards_the_required_complete_partial_file():
    pr = pr_info([section('implementation.py', 40000), section('bridge.py', 9000),
                  section('tests.py', 10000)])
    chunks = r.split_review_diff(pr.diff)
    entries, code = c.automatic_complement(pr, chunks, 1)
    assert code == 'partial_file_supplied'
    assert entries == [dict(path='implementation.py', diff=c.requested_file_diff(
        pr, 'implementation.py', pr.head_sha))]
    assert max(c.context_size(entries)) <= c.MAX_CONTEXT_SIZE
