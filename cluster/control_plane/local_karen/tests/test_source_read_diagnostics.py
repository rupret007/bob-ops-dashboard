"""Transport diagnostics identify the blocker without granting review authority."""
import json
import subprocess
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.github_budget import GitHubDeferred
from cluster.control_plane.local_karen import source_context as s, reviewers as r, gate
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.context_diagnostics import (
    collect_context_diagnostics, sanitize_context_diagnostics,
)
from cluster.control_plane.local_karen.tests.test_chunk_evidence import pr_info, vote

PRIVATE = 'synthetic-private-transport-data'


@pytest.mark.parametrize('failure, detail', [
    (GitHubDeferred(PRIVATE, code='minute-exhausted'), 'minute_exhausted'),
    (GitHubDeferred(PRIVATE, code='background-exhausted'), 'background_exhausted'),
    (GitHubDeferred(PRIVATE), 'github_deferred'),
    (subprocess.TimeoutExpired(PRIVATE, 10, output=PRIVATE, stderr=PRIVATE), 'timeout'),
    (RuntimeError(PRIVATE), 'unexpected_error'),
    (SimpleNamespace(returncode=1, stdout=PRIVATE, stderr=PRIVATE), 'cli_failed'),
    (SimpleNamespace(returncode=0, stdout=PRIVATE, stderr=PRIVATE), 'invalid_json'),
])
def test_actual_transport_failure_survives_review_and_persistence_without_a_pass(monkeypatch, caplog, tmp_path, failure, detail):
    reader = Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=failure)
    monkeypatch.setattr(s, 'budgeted_gh', reader)
    resolver = s.PinnedSourceContext(pr_info())
    initial = replace(vote(), requested_source_files=['src/runtime.py'],
                      scope={'source_read_reason': PRIVATE})
    invoke = Mock(return_value=initial)
    result, _ = r._review_with_requested_context(invoke, pr_info(), pr_info(), resolver)
    assert invoke.call_count == reader.call_count == 1
    assert result.scope['source_context_error'] == 'source_read'
    assert result.scope['source_read_reason'] == detail
    assert result.scope['context_incomplete'] and not result.passed
    safe = collect_context_diagnostics([result])
    assert safe == [{'reviewer': 'codex', 'source_context_error': 'source_read',
                     'source_read_reason': detail, 'context_code': 'source_unavailable',
                     'changed_requests': 0, 'source_requests': 1}]
    assert sanitize_context_diagnostics(json.loads(json.dumps(safe))) == safe
    assert PRIVATE not in json.dumps(safe) and PRIVATE not in caplog.text
    assert resolver.sources == {} and resolver.bytes == 0
    config = KarenConfig(state_dir=str(tmp_path), verdict_dir=str(tmp_path))
    with patch.object(gate, 'run_all_checks', return_value=[]), \
         patch.object(gate, 'run_review_chain', return_value=(result, [result])), \
         patch.object(gate, '_metrics'), patch.object(gate, 'save_verdict') as save, \
         patch.object(gate, 'verify_sha_current') as verify:
        outcome = gate.GateRunner(config)._run_claimed(pr_info(), False, 0)
    assert outcome.verdict.status == 'ERROR' and not outcome.verdict.passed
    assert outcome.verdict_path is None and not outcome.sha_verified
    save.assert_not_called()
    verify.assert_not_called()


def test_total_fetch_deadline_and_legacy_exception_contract_are_preserved(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(s.time, 'monotonic', lambda: clock[0])
    def slow(*args, **kwargs):
        clock[0] = 31.0
        return SimpleNamespace(returncode=0, stdout='{}')
    monkeypatch.setattr(s, 'budgeted_gh', slow)
    resolver = s.PinnedSourceContext(pr_info())
    with pytest.raises(s.SourceUnavailable) as error:
        resolver.fetch(pr_info(), 'src/runtime.py')
    assert error.value.args == ('source_read',) and str(error.value) == 'source_read'
    assert error.value.read_reason == 'fetch_deadline'
    assert resolver.seconds <= 0 and resolver.calls == 1


@pytest.mark.parametrize('detail', [None, PRIVATE, {}, [], True, 1])
def test_untrusted_or_uncoupled_detail_cannot_survive_boundaries(detail):
    error = s.SourceUnavailable('source_read', read_reason=detail)
    assert error.read_reason is None and str(error) == 'source_read'
    record = {'reviewer': 'codex', 'source_context_error': 'source_read', 'source_read_reason': detail}
    assert sanitize_context_diagnostics([record]) == [{'reviewer': 'codex', 'source_context_error': 'source_read'}]
    for primary in ['source_budget', 'source_metadata', 'blob_identity']:
        assert s.SourceUnavailable(primary, read_reason='timeout').read_reason is None
        record.update(source_context_error=primary, source_read_reason='timeout')
        assert 'source_read_reason' not in sanitize_context_diagnostics([record])[0]


def test_mutated_exception_cannot_emit_or_inherit_remote_text():
    error = s.SourceUnavailable('source_read')
    error.read_reason = PRIVATE
    initial = replace(vote(), requested_source_files=['src/runtime.py'], scope={'source_read_reason': PRIVATE})
    resolver = Mock(fetch=Mock(side_effect=error))
    result, _ = r._review_with_requested_context(Mock(return_value=initial), pr_info(), pr_info(), resolver)
    assert 'source_read_reason' not in result.scope
    assert PRIVATE not in json.dumps(collect_context_diagnostics([result]))
