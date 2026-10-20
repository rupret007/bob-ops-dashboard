"""Causal worker diagnostics retain frames without leaking exception payloads."""
import json
import subprocess
from unittest.mock import Mock

import pytest

from cluster.control_plane import fix_worker
from cluster.control_plane.coordination import ClaimLost, CoordinationError
from cluster.control_plane.retry_policy import PreflightFailure, WorkerFailure, classify_failure
from cluster.control_plane.service_logging import bind_context, reset_context


@pytest.mark.parametrize('stage,kind,code', [
    ('tests', 'command', 'tests-failed'),
    ('provider', 'runtime', 'provider-failed'),
    ('provider', 'coordination', 'coordination-failed'),
])
def test_original_cause_is_logged_before_wrapping_without_payload(tmp_path, monkeypatch, stage, kind, code):
    marker = 'SyntheticConfidentialPayload987'
    monkeypatch.setenv('STORYPOOL_LOG_DIR', str(tmp_path))

    def fail_inside_operation(*args):
        fix_worker._stage(stage)
        if kind == 'command':
            raise subprocess.CalledProcessError(7, ['fixture-provider', marker],
                                                output=marker, stderr=marker)
        if kind == 'coordination':
            raise CoordinationError(marker)
        raise RuntimeError(marker)

    monkeypatch.setattr(fix_worker, '_fix_pr', fail_inside_operation)
    token = bind_context(job_id='causal-fixture', correlation_id='causal-fixture')
    original_stage = fix_worker._failure_stage.get()
    try:
        with pytest.raises(WorkerFailure) as caught:
            fix_worker.fix_pr('fixture/repo', 1, 'fixture-head', ['synthetic finding'])
    finally:
        reset_context(token)
    assert (caught.value.code, caught.value.stage) == (code, stage)
    assert fix_worker._failure_stage.get() == original_stage
    raw = (tmp_path / 'fix-worker-causal-fixture.jsonl').read_text()
    assert marker not in raw
    records = [json.loads(line) for line in raw.splitlines()]
    assert len(records) == 1
    row = records[0]
    assert row['event'] == 'operation_failed'
    assert row['failure_code'] == code
    assert row['failure_stage'] == stage
    assert row['job_id'] == 'causal-fixture'
    assert any(frame['function'] == 'fail_inside_operation' for frame in row['traceback'])
    assert row['exception_type'] == {'command': 'CalledProcessError',
                                     'runtime': 'RuntimeError',
                                     'coordination': 'CoordinationError'}[kind]
    assert 'argv' not in row and 'stdout' not in row and 'stderr' not in row


@pytest.mark.parametrize('error', [PreflightFailure('head-changed'),
                                  WorkerFailure('no-change', 'verification')])
def test_already_classified_failures_keep_identity_without_duplicate_wrapper_event(monkeypatch, error):
    def fail(*args):
        raise error
    event = Mock()
    monkeypatch.setattr(fix_worker, '_fix_pr', fail)
    monkeypatch.setattr(fix_worker, 'event', event)
    with pytest.raises(type(error)) as caught:
        fix_worker.fix_pr('fixture/repo', 1, 'fixture-head', ['synthetic finding'])
    assert caught.value is error
    event.assert_not_called()


def test_ownership_loss_still_has_priority_over_general_coordination_failure():
    assert classify_failure(ClaimLost('synthetic'), 'provider') == ('ownership-lost', 'provider')
    assert classify_failure(CoordinationError('synthetic'), 'tests') == ('coordination-failed', 'tests')


def test_diagnostic_failure_preserves_original_controlled_job_failure(monkeypatch):
    def fail(*args):
        fix_worker._stage('tests')
        raise subprocess.CalledProcessError(7, ['synthetic-test-command'])
    monkeypatch.setattr(fix_worker, '_fix_pr', fail)
    monkeypatch.setattr(fix_worker, 'event', Mock(side_effect=OSError('synthetic log unavailable')))
    with pytest.raises(WorkerFailure) as caught:
        fix_worker.fix_pr('fixture/repo', 1, 'fixture-head', ['synthetic finding'])
    assert (caught.value.code, caught.value.stage) == ('tests-failed', 'tests')
    assert caught.value.cause_diagnostic_unavailable is True
