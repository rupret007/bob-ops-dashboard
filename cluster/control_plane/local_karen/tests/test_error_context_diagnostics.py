"""Operational context evidence never grants approval or retains model text."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import gate, poller
from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.reviewers import ReviewResult, ReviewVerdict

UNTRUSTED_TEXT = 'synthetic-private-value-never-retain'
EXPECTED = [{'reviewer': 'minimax', 'chunk': 2, 'source_context_error': 'source_metadata',
             'context_code': 'source_unavailable', 'changed_requests': 0, 'source_requests': 1}]


def incomplete(tmp_path, verdict=ReviewVerdict.ERROR):
    pr = PRInfo(owner='owner', repo='repo', number=7, head_sha='a' * 40,
                title='fixture', author='fixture', is_draft=False, diff='+fixture',
                ci_status={}, changed_files=[], deleted_files=[])
    review = ReviewResult('minimax', ReviewVerdict.BORDERLINE, UNTRUSTED_TEXT,
                          raw_response=UNTRUSTED_TEXT, requested_source_files=[UNTRUSTED_TEXT],
                          findings={'security_issues': [UNTRUSTED_TEXT]},
                          scope={'chunk': 2, 'source_context_error': 'source_metadata',
                                 'context_resolution': {'code': 'source_unavailable',
                                                        'changed_requests': 0, 'source_requests': 1},
                                 'file_header': UNTRUSTED_TEXT, 'arbitrary': UNTRUSTED_TEXT})
    combined = ReviewResult('chain', verdict, UNTRUSTED_TEXT, scope={'context_incomplete': True})
    config = KarenConfig(state_dir=str(tmp_path), verdict_dir=str(tmp_path))
    with patch.object(gate, 'run_all_checks', return_value=[]), \
         patch.object(gate, 'run_review_chain', return_value=(combined, [review])), \
         patch.object(gate, '_metrics') as metrics, \
         patch.object(gate, 'save_verdict') as save, \
         patch.object(gate, 'verify_sha_current') as verify:
        result = gate.GateRunner(config)._run_claimed(pr, False, 0)
    metrics.record_karen_review.assert_not_called()
    save.assert_not_called()
    verify.assert_not_called()
    assert not list(tmp_path.glob('*.json'))
    return result, config


@pytest.mark.parametrize('verdict', [ReviewVerdict.ERROR, ReviewVerdict.BORDERLINE])
def test_incomplete_gate_retains_fixed_code_without_artifact_or_approval(tmp_path, verdict):
    result, _ = incomplete(tmp_path, verdict)
    assert result.verdict.status == 'ERROR' and not result.verdict.passed
    assert result.verdict_path is None and not result.sha_verified
    assert result.verdict.review_results == []
    assert getattr(result, 'context_diagnostics', None) == EXPECTED
    assert UNTRUSTED_TEXT not in json.dumps(result.context_diagnostics)


def test_poller_persists_safe_diagnostics_without_model_reason(tmp_path, caplog):
    result, config = incomplete(tmp_path)
    config.repos = 'owner/repo'
    with patch.object(poller, 'BACKLOG_AVAILABLE', False), \
         patch.object(poller, 'list_prs_with_label', return_value=[
             {'number': 7, 'headRefOid': 'a' * 40, 'isDraft': False}]), \
         patch.object(poller, 'enrich'), patch.object(poller, 'run_gate', return_value=result), \
         patch('cluster.control_plane.node_control.is_draining', return_value=False), \
         patch('cluster.control_plane.github_reads.refresh_quota'), \
         patch('cluster.control_plane.local_karen.verdict_push.push'), \
         patch.object(poller, 'remove_label') as remove, patch.object(poller, 'add_label') as add:
        records = poller._run_poll_once(config, auto_discover=False, auto_label=False, auto_update=False)
    remove.assert_not_called()
    add.assert_not_called()
    assert len(records) == 1 and 'error' in records[0] and 'passed' not in records[0]
    assert records[0].get('context_diagnostics') == EXPECTED
    state = (tmp_path / 'poll_state.json').read_text()
    assert json.loads(state)['prs_checked'] == records
    assert UNTRUSTED_TEXT not in state and UNTRUSTED_TEXT not in caplog.text


def test_sanitizer_rejects_untrusted_values_and_bounds_records():
    from cluster.control_plane.local_karen.context_diagnostics import sanitize_context_diagnostics
    hostile = dict(EXPECTED[0], source_context_error=UNTRUSTED_TEXT, context_code=[UNTRUSTED_TEXT],
                   changed_requests=True, source_requests=5, chunk=-1,
                   path=UNTRUSTED_TEXT, reason=UNTRUSTED_TEXT, raw_response=UNTRUSTED_TEXT, findings=UNTRUSTED_TEXT)
    safe = sanitize_context_diagnostics([hostile, {'reviewer': UNTRUSTED_TEXT, 'context_code': UNTRUSTED_TEXT}])
    assert safe == [{'reviewer': 'minimax', 'source_context_error': 'unknown', 'context_code': 'unknown'}]
    assert UNTRUSTED_TEXT not in json.dumps(safe)
    assert len(sanitize_context_diagnostics(EXPECTED * 100)) == 32
    for malformed in (None, UNTRUSTED_TEXT, {}, 1, True):
        assert sanitize_context_diagnostics(malformed) == []
    assert sanitize_context_diagnostics([None, [], UNTRUSTED_TEXT, {'reviewer': 'minimax'}]) == []


@pytest.mark.parametrize('chunk', [0, -1, True, 4097, 1.5, UNTRUSTED_TEXT])
def test_malformed_chunk_is_omitted(chunk):
    from cluster.control_plane.local_karen.context_diagnostics import sanitize_context_diagnostics
    assert 'chunk' not in sanitize_context_diagnostics([dict(EXPECTED[0], chunk=chunk)])[0]


def test_poller_resanitizes_gate_boundary(tmp_path):
    from cluster.control_plane.local_karen.context_diagnostics import sanitize_context_diagnostics
    untrusted = dict(EXPECTED[0], arbitrary=UNTRUSTED_TEXT, changed_requests=999, reviewer='minimax')
    result = SimpleNamespace(error='incomplete', sha_verified=False, verdict_path=None,
                             context_diagnostics=[untrusted])
    with patch.object(poller, 'list_prs_with_label', return_value=[{'number': 7, 'headRefOid': 'a'*40}]), \
         patch.object(poller, 'enrich'), patch.object(poller, 'run_gate', return_value=result), \
         patch('cluster.control_plane.node_control.is_draining', return_value=False):
        records = poller.poll_and_run(['owner/repo'], KarenConfig(state_dir=str(tmp_path)))
    assert records[0]['context_diagnostics'] == sanitize_context_diagnostics([untrusted])
    assert UNTRUSTED_TEXT not in json.dumps(records)


def test_collector_omits_malformed_scopes_and_unknown_reviewers():
    from cluster.control_plane.local_karen.context_diagnostics import collect_context_diagnostics
    reviews = [SimpleNamespace(reviewer='minimax', scope=UNTRUSTED_TEXT),
               SimpleNamespace(reviewer=UNTRUSTED_TEXT, scope={'source_context_error': 'source_metadata'}),
               SimpleNamespace(reviewer='codex', scope={'chunk': 4096,
                   'source_context_error': 'source_identity_or_path',
                   'context_resolution': {'code': 'context_budget', 'changed_requests': 4,
                                          'source_requests': 0}, 'source': UNTRUSTED_TEXT})]
    assert collect_context_diagnostics(reviews) == [{
        'reviewer': 'codex', 'chunk': 4096, 'source_context_error': 'source_identity_or_path',
        'context_code': 'context_budget', 'changed_requests': 4, 'source_requests': 0}]


def test_context_diagnostics_cannot_inherit_a_verdict_or_authority():
    from cluster.control_plane.local_karen.context_diagnostics import sanitize_context_diagnostics
    record = dict(EXPECTED[0], passed=True, verdict='PASS', sha_verified=True,
                  verdict_path=UNTRUSTED_TEXT, head_sha='a' * 40, status='COMPLETE')
    assert sanitize_context_diagnostics([record]) == EXPECTED


@pytest.mark.parametrize('error_first', [False, True])
def test_source_error_survives_more_than_32_ordinary_component_contexts(error_first):
    from cluster.control_plane.local_karen.context_diagnostics import collect_context_diagnostics
    normal = SimpleNamespace(reviewer='gemini', scope={
        'chunk': 1, 'context_resolution': {'code': 'not_requested',
                                         'changed_requests': 0, 'source_requests': 0}})
    failure = SimpleNamespace(reviewer='minimax', scope={
        'chunk': 33, 'source_context_error': 'source_metadata',
        'context_resolution': {'code': 'source_unavailable',
                               'changed_requests': 0, 'source_requests': 1}})
    reviews = [failure] + [normal] * 40 if error_first else [normal] * 40 + [failure]
    diagnostics = collect_context_diagnostics(reviews)
    assert len(diagnostics) == 32
    assert diagnostics[0] == dict(EXPECTED[0], chunk=33)
    assert sum(item.get('source_context_error') == 'source_metadata' for item in diagnostics) == 1
