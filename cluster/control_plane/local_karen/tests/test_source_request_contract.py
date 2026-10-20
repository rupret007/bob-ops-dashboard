"""Full-source requests must not silently receive only a changed-file diff."""
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import reviewers as r, chunk_context as c, source_context as s
from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.tests.test_source_context import Objects


SOURCE = 'src/runtime.py'
OTHER = 'src/caller.py'


def case():
    def diff(path, old, new):
        return ('diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n'
                '@@ -2 +2 @@\n-{1}\n+{2}\n').format(path, old, new)
    implementation = diff(SOURCE, '    return value >= 0', '    return value > 0')
    caller = diff(OTHER, 'result = check(0)', 'result = check(1)')
    pr = PRInfo('owner', 'repo', 32, 'a'*40, 'Validate positive values', 'author',
                False, implementation+caller, {}, [SOURCE, OTHER], [], 'Validate the boundary')
    part = replace(pr, diff=caller, review_scope=c.build_review_scope(pr, [implementation, caller], 2))
    return pr, part, implementation


def vote(verdict=r.ReviewVerdict.PASS, source=(), changed=()):
    return r.ReviewResult('codex', verdict, 'Independently checked the boundary implementation',
        requested_source_files=list(source), requested_files=list(changed),
        findings=r.ReviewFindings(risk_notes=['Checked the caller and implementation']))


@pytest.mark.parametrize('visible', ['unseen', 'current', 'initial'])
def test_changed_source_request_receives_full_proven_implementation(visible):
    pr, part, implementation = case()
    initial = None
    if visible == 'current':
        part = replace(pr, diff=implementation, review_scope=c.build_review_scope(pr, [implementation, part.diff], 1))
    elif visible == 'initial':
        initial = ([dict(path=SOURCE, diff=implementation)], 'supplied')
    objects = Objects()
    invoke = Mock(side_effect=[vote(r.ReviewVerdict.BORDERLINE, source=[SOURCE]), vote()])
    with patch.object(s, 'budgeted_gh', side_effect=objects.read):
        final, _ = r._review_with_requested_context(invoke, part, pr, initial_context=initial)
    assert final.passed and invoke.call_count == 2 and len(objects.calls) == 3
    entries = invoke.call_args.args[0].review_material.entries
    full = next(item for item in entries if 'source' in item)
    assert full == dict(path=SOURCE, source=objects.raw.decode(), bytes=len(objects.raw),
                       head_sha=pr.head_sha, tree_sha='b'*40, blob_sha=objects.sha)
    assert 'diff' not in full and 'source_requests_answered_with_diff' not in final.scope
    assert final.scope['source_context'][0]['blob_sha'] == objects.sha
    assert invoke.call_args.args[0].review_material.phase == 'requested'


@pytest.mark.parametrize('kind', ['oversize', 'hash', 'private', 'symlink'])
def test_rejected_changed_source_never_falls_back_to_diff_or_approval(kind):
    pr, part, _ = case()
    objects = Objects(b'person@example.test' if kind == 'private' else b'def check(value):\n    return value > 0\n')
    if kind == 'oversize': objects.tree['tree'][0]['size'] = s.MAX_FILE_BYTES + 1
    elif kind == 'hash': objects.sha = 'c'*40; objects.tree['tree'][0]['sha'] = objects.sha; objects.blob['sha'] = objects.sha
    elif kind == 'symlink': objects.tree['tree'][0]['mode'] = '120000'
    invoke = Mock(return_value=vote(r.ReviewVerdict.BORDERLINE, source=[SOURCE]))
    with patch.object(s, 'budgeted_gh', side_effect=objects.read):
        final, _ = r._review_with_requested_context(invoke, part, pr)
    assert not final.passed and invoke.call_count == 1
    assert final.scope['context_resolution']['code'] == 'source_unavailable'
    assert final.scope['source_context_error'] in ('source_metadata', 'blob_hash', 'source_private')
    assert 'source_requests_answered_with_diff' not in final.scope


def test_explicit_diff_and_source_requests_keep_both_evidence_kinds():
    pr, part, implementation = case()
    objects = Objects()
    invoke = Mock(side_effect=[vote(r.ReviewVerdict.BORDERLINE, source=[SOURCE], changed=[SOURCE]), vote()])
    with patch.object(s, 'budgeted_gh', side_effect=objects.read):
        final, _ = r._review_with_requested_context(invoke, part, pr)
    entries = invoke.call_args.args[0].review_material.entries
    assert final.passed and len(entries) == 2 and len(objects.calls) == 3
    assert entries[0] == dict(path=SOURCE, diff=implementation)
    assert entries[1]['source'] == objects.raw.decode()


def test_full_source_does_not_share_a_diff_slot_or_reset_the_packet_cap():
    pr, part, implementation = case()
    extras = []
    for n in range(3):
        path = 'src/extra{}.py'.format(n)
        extra = implementation.replace(SOURCE, path)
        extras.append(dict(path=path, diff=extra))
        pr.diff += extra
        pr.changed_files.append(path)
    initial = ([dict(path=SOURCE, diff=implementation), *extras], 'supplied')
    invoke = Mock(return_value=vote(r.ReviewVerdict.BORDERLINE, source=[SOURCE]))
    with patch.object(s, 'budgeted_gh', side_effect=AssertionError('cap must prevent fetch')):
        final, _ = r._review_with_requested_context(invoke, part, pr, initial_context=initial)
    assert not final.passed and invoke.call_count == 1
    assert final.scope['context_resolution']['code'] == 'context_budget'


@pytest.mark.parametrize('remaining', [r.ReviewVerdict.FAIL, r.ReviewVerdict.BORDERLINE])
def test_source_delivery_does_not_override_the_independent_followup(remaining):
    pr, part, _ = case()
    objects = Objects()
    followup = vote(remaining, source=[SOURCE] if remaining == r.ReviewVerdict.BORDERLINE else [])
    if remaining == r.ReviewVerdict.FAIL:
        followup.findings.functional_issues = [OTHER+':2 still calls the wrong boundary']
    invoke = Mock(side_effect=[vote(r.ReviewVerdict.BORDERLINE, source=[SOURCE]), followup])
    with patch.object(s, 'budgeted_gh', side_effect=objects.read):
        final, _ = r._review_with_requested_context(invoke, part, pr)
    assert invoke.call_count == 2 and final.verdict == remaining and not final.passed
    if remaining == r.ReviewVerdict.BORDERLINE: assert final.scope['context_incomplete']


def test_reviewer_contract_describes_source_choice_and_one_followup():
    pr, _, _ = case()
    for prompt in [r.build_review_prompt(pr), r.MINIMAX_SYSTEM_CONTRACT]:
        assert 'at most one context follow-up' in prompt
        assert 'supplemental_material.phase is requested' in prompt
        assert 'unresolved evidence must remain BORDERLINE' in prompt
    assert 'requested_source_files for existing' in r.build_review_prompt(pr)
