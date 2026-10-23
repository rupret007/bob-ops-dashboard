"""Review errors retain candidate identity without becoming review authority."""
import hashlib
import json
import subprocess
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.local_karen import poller
from cluster.control_plane.local_karen.config import KarenConfig

PRIVATE = 'synthetic-private-exception-and-model-data'
HEAD = 'a' * 40


def run_error(tmp_path, outcome, head=HEAD, persist=False):
    config = KarenConfig(state_dir=str(tmp_path), verdict_dir=str(tmp_path))
    invoke = Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=outcome)
    with patch.object(poller, 'BACKLOG_AVAILABLE', False), \
         patch.object(poller, 'list_prs_with_label', return_value=[
             {'number': 7, 'headRefOid': head, 'isDraft': False}]), \
         patch.object(poller, 'enrich'), patch.object(poller, 'run_gate', invoke), \
         patch('cluster.control_plane.guards.MachineGuards.check_all', return_value=SimpleNamespace(passed=True)), \
         patch('cluster.control_plane.node_control.is_draining', return_value=False), \
         patch('cluster.control_plane.github_reads.refresh_quota'), \
         patch('cluster.control_plane.local_karen.verdict_push.push'), \
         patch.object(poller, 'remove_label') as remove, patch.object(poller, 'add_label') as add:
        if persist:
            config.repos = 'owner/repo'
            records = poller._run_poll_once(config, auto_discover=False, auto_label=False, auto_update=False)
        else:
            records = poller.poll_and_run(['owner/repo'], config)
    assert invoke.call_count == 1
    remove.assert_not_called()
    add.assert_not_called()
    assert len(records) == 1 and 'sha' not in records[0] and 'passed' not in records[0]
    assert not list(tmp_path.glob('*KAREN*.json'))
    return records


@pytest.mark.parametrize('outcome, code', [
    (RuntimeError(PRIVATE), 'review_execution_error'),
    (subprocess.TimeoutExpired(PRIVATE, 3, output=PRIVATE, stderr=PRIVATE), 'review_timeout'),
    (TimeoutError(PRIVATE), 'review_timeout'),
    (poller.GitHubError(PRIVATE), 'github_error'),
    (TypeError(PRIVATE), 'invalid_review_result'),
    (SimpleNamespace(error=PRIVATE, sha_verified=False, verdict_path=None), 'unverified_artifact'),
])
def test_real_poller_error_persists_expected_head_and_fixed_code_not_remote_text(tmp_path, caplog, outcome, code):
    records = run_error(tmp_path, outcome, persist=True)
    record = records[0]
    assert record['expected_head_sha'] == HEAD
    assert record['error_code'] == code
    assert record['review_source_sha256'] == hashlib.sha256(json.dumps(
        poller.LOADED_HASHES, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert datetime.fromisoformat(record['attempted_at']).tzinfo is not None
    persisted = (tmp_path / 'poll_state.json').read_text()
    assert json.loads(persisted)['prs_checked'] == records
    assert PRIVATE not in persisted and PRIVATE not in caplog.text
    assert code in caplog.text


@pytest.mark.parametrize('head', [None, True, 7, [], 'a' * 12, 'A' * 40, 'g' * 40])
def test_unvalidated_head_is_not_retained_as_expected_or_verified_identity(tmp_path, head):
    records = run_error(tmp_path, RuntimeError(PRIVATE), head=head)
    assert 'expected_head_sha' not in records[0]


def test_attribution_keeps_bounded_context_diagnostics_without_private_fields(tmp_path):
    result = SimpleNamespace(error=PRIVATE, sha_verified=False, verdict_path=None,
        context_diagnostics=[{'reviewer': 'codex', 'source_context_error': 'source_read',
                             'source_read_reason': 'minute_exhausted', 'context_code': 'source_unavailable',
                             'reason': PRIVATE, 'source': PRIVATE, 'passed': True}])
    record = run_error(tmp_path, result)[0]
    assert record['context_diagnostics'] == [{'reviewer': 'codex', 'source_context_error': 'source_read',
                                             'source_read_reason': 'minute_exhausted',
                                             'context_code': 'source_unavailable'}]
    assert PRIVATE not in json.dumps(record)
