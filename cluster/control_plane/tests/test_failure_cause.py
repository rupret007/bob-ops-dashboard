"""Causal worker diagnostics retain frames without leaking exception payloads."""
import json
import logging
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


@pytest.mark.parametrize('failure', ['file-write', 'rollover', 'stderr-write'])
@pytest.mark.parametrize('logging_debug', [True, False])
def test_real_handler_failure_never_prints_exception_payload(
        tmp_path, monkeypatch, capsys, failure, logging_debug):
    from cluster.control_plane.service_logging import event, RetainedRotatingHandler

    marker = 'SyntheticConfidentialHandlerPayload987'
    monkeypatch.setenv('STORYPOOL_LOG_DIR', str(tmp_path))
    monkeypatch.setattr(logging, 'raiseExceptions', logging_debug)
    event('fix-worker', 'fixture_initialized')
    path = tmp_path / 'fix-worker.jsonl'
    logger = logging.getLogger('storypool.structured.' + str(path))
    file_handler = next(h for h in logger.handlers if isinstance(h, RetainedRotatingHandler))

    class BrokenStream:
        def write(self, text):
            raise OSError(marker)

        def flush(self):
            pass

    if failure == 'rollover':
        monkeypatch.setattr(file_handler, 'maxBytes', 1)

        def rollover():
            raise OSError(marker)

        monkeypatch.setattr(file_handler, 'doRollover', rollover)
    else:
        handler = file_handler if failure == 'file-write' else next(
            h for h in logger.handlers if not isinstance(h, RetainedRotatingHandler))
        monkeypatch.setattr(handler, 'stream', BrokenStream())

    def fail(*args):
        fix_worker._stage('tests')
        raise subprocess.CalledProcessError(7, ['fixture-command', marker], output=marker)

    monkeypatch.setattr(fix_worker, '_fix_pr', fail)
    with pytest.raises(WorkerFailure) as caught:
        fix_worker.fix_pr('fixture/repo', 1, 'fixture-head', ['synthetic finding'])
    observed = capsys.readouterr()
    assert marker not in observed.err + observed.out + path.read_text()
    assert '--- Logging error ---' not in observed.err
    assert (caught.value.code, caught.value.stage) == ('tests-failed', 'tests')
    assert caught.value.cause_diagnostic_unavailable is True
