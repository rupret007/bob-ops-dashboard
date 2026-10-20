"""Causal worker diagnostics retain frames without leaking exception payloads."""
import json
import logging
import subprocess
from unittest.mock import MagicMock, Mock

import pytest

from cluster.control_plane import fix_worker
from cluster.control_plane.coordination import ClaimLost, CoordinationError
from cluster.control_plane.retry_policy import PreflightFailure, WorkerFailure, classify_failure
from cluster.control_plane.service_logging import bind_context, reset_context


@pytest.fixture
def handler_fault(tmp_path, monkeypatch):
    """Fail a real handler only for the selected event, leaving other events usable."""
    from cluster.control_plane.service_logging import event, RetainedRotatingHandler

    marker = 'SyntheticConfidentialHandlerPayload987'
    monkeypatch.setenv('STORYPOOL_LOG_DIR', str(tmp_path))

    def install(failure, target, job_id=''):
        token = bind_context(job_id=job_id)
        try:
            event('fix-worker', 'fixture_initialized')
        finally:
            reset_context(token)
        path = tmp_path / ('fix-worker' + ('-' + job_id if job_id else '') + '.jsonl')
        logger = logging.getLogger('storypool.structured.' + str(path))
        file_handler = next(h for h in logger.handlers if isinstance(h, RetainedRotatingHandler))
        attempts = []

        def fail():
            attempts.append(target)
            raise OSError(marker)

        if failure == 'rollover':
            should_rollover = file_handler.shouldRollover
            monkeypatch.setattr(file_handler, 'shouldRollover',
                                lambda record: record.getMessage() == target or should_rollover(record))
            monkeypatch.setattr(file_handler, 'doRollover', fail)
        else:
            handler = file_handler if failure == 'file-write' else next(
                h for h in logger.handlers if not isinstance(h, RetainedRotatingHandler))
            stream = handler.stream

            class SelectivelyBrokenStream:
                def write(self, text):
                    if json.loads(text)['event'] == target:
                        fail()
                    return stream.write(text)

                def __getattr__(self, name):
                    return getattr(stream, name)

            monkeypatch.setattr(handler, 'stream', SelectivelyBrokenStream())
        return path, attempts, marker

    return install


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


@pytest.mark.parametrize('failure', ['file-write', 'rollover', 'stderr-write'])
@pytest.mark.parametrize('kind', ['timeout', 'command'])
def test_command_failed_handler_error_preserves_original_exception(
        monkeypatch, capsys, handler_fault, failure, kind):
    path, attempts, marker = handler_fault(failure, 'command_failed')
    args = ['fixture-command', marker]
    error = (subprocess.TimeoutExpired(args, 1800, output=marker) if kind == 'timeout' else
             subprocess.CalledProcessError(7, args, output=marker, stderr=marker))
    monkeypatch.setattr(fix_worker.subprocess, 'run', Mock(side_effect=error))

    with pytest.raises(type(error)) as caught:
        fix_worker.run(args)

    assert caught.value is error
    assert attempts == ['command_failed']
    observed = capsys.readouterr()
    assert marker not in observed.err + observed.out + path.read_text()
    assert '--- Logging error ---' not in observed.err


@pytest.mark.parametrize('failure', ['file-write', 'rollover', 'stderr-write'])
def test_command_failed_handler_error_preserves_deadline_and_causal_traceback(
        monkeypatch, capsys, handler_fault, failure):
    path, attempts, marker = handler_fault(failure, 'command_failed')
    monkeypatch.setattr(fix_worker, '_find_codex', lambda: 'fixture-provider')

    def command_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(['fixture-command', marker], 1800, output=marker)

    monkeypatch.setattr(fix_worker.subprocess, 'run', command_timeout)
    original_stage = fix_worker._failure_stage.get()
    with pytest.raises(WorkerFailure) as caught:
        fix_worker.fix_pr('fixture/repo', 1, 'fixture-head', ['synthetic finding'])

    assert (caught.value.code, caught.value.stage) == ('deadline', 'git')
    assert fix_worker._failure_stage.get() == original_stage
    assert attempts == ['command_failed']
    raw = path.read_text()
    row = next(json.loads(line) for line in raw.splitlines()
               if json.loads(line)['event'] == 'operation_failed')
    assert row['exception_type'] == 'TimeoutExpired'
    assert row['failure_code'] == 'deadline' and row['failure_stage'] == 'git'
    functions = {frame['function'] for frame in row['traceback']}
    assert {'_fix_pr', 'run', 'command_timeout'} <= functions
    observed = capsys.readouterr()
    assert marker not in observed.err + observed.out + raw
    assert '--- Logging error ---' not in observed.err


@pytest.mark.parametrize('failure', ['file-write', 'rollover', 'stderr-write'])
@pytest.mark.parametrize('target', ['job_pushed', 'retry_state_unavailable'])
def test_handler_error_after_verified_completion_keeps_job_successful(
        tmp_path, monkeypatch, capsys, handler_fault, failure, target):
    from cluster.control_plane import coordination, node_control
    from cluster.control_plane.job_ledger import JobLedger

    ledger = JobLedger(tmp_path / 'jobs')
    job = ledger.create_job('fixture/repo', 1, 'a' * 40, 'codex', 'nucbox')
    pushed = 'b' * 40
    path, attempts, marker = handler_fault(failure, target, job.job_id)
    lease = MagicMock()
    if target == 'retry_state_unavailable':
        lease.client.retry_state.side_effect = OSError(marker)
    else:
        lease.client.retry_state.return_value = dict(attempts=1, code='transient')
    monkeypatch.setattr(fix_worker, 'get_ledger', lambda: ledger)
    monkeypatch.setattr(node_control, 'is_draining', lambda machine: False)
    monkeypatch.setattr(coordination.ClaimClient, 'from_env', Mock())
    monkeypatch.setattr(coordination, 'Lease', Mock(return_value=lease))
    monkeypatch.setattr(fix_worker, 'model_label', lambda: 'fixture-model')
    monkeypatch.setattr(fix_worker, 'findings_for_pr', lambda *args: ['synthetic finding'])
    monkeypatch.setattr(fix_worker, 'fix_pr', lambda *args: pushed)
    complete = Mock(wraps=ledger.complete_job)
    fail = Mock(wraps=ledger.fail_job)
    monkeypatch.setattr(ledger, 'complete_job', complete)
    monkeypatch.setattr(ledger, 'fail_job', fail)

    assert fix_worker.execute(job.job_id) == 0

    complete.assert_called_once_with(job.job_id, 'commit_pushed', pushed, verified=True)
    fail.assert_not_called()
    saved = ledger.get_recent_jobs(hours=24)[0]
    assert saved.state == 'done' and saved.outcome_verified is True
    assert saved.outcome == 'commit_pushed' and saved.outcome_sha == pushed
    assert saved.error == saved.failure_code == saved.failure_stage == ''
    assert attempts == [target]
    lease.__exit__.assert_called_once_with(None, None, None)
    raw = path.read_text()
    assert all(json.loads(line)['event'] != 'job_failed' for line in raw.splitlines())
    observed = capsys.readouterr()
    assert marker not in observed.err + observed.out + raw
    assert '--- Logging error ---' not in observed.err
