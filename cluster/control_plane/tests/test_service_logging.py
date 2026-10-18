"""Diagnostics must identify failures without persisting provider output or secrets."""
import json
import logging
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


def test_worker_records_test_names_and_timing_without_captured_output(tmp_path, monkeypatch):
    from cluster.control_plane import fix_worker
    monkeypatch.setenv('STORYPOOL_LOG_DIR', str(tmp_path))
    output = 'FAILED cluster/tests/test_gate.py::test_exact_sha - AssertionError: private diff\napi_key=never-persist-this\n'
    error = subprocess.CalledProcessError(2, ['python', '-m', 'pytest', '-q'], output=output)
    with patch.object(fix_worker.subprocess, 'run', side_effect=error):
        with pytest.raises(subprocess.CalledProcessError):
            fix_worker.run(['python', '-m', 'pytest', '-q'])
    rows = [json.loads(x) for x in (tmp_path / 'fix-worker.jsonl').read_text().splitlines()]
    last = rows[-1]
    assert last['level'] == 'ERROR' and last['event'] == 'command_failed'
    assert last['failed_tests'] == ['cluster/tests/test_gate.py::test_exact_sha']
    assert last['returncode'] == 2 and last['duration_ms'] >= 0
    assert 'private diff' not in json.dumps(rows) and 'never-persist-this' not in json.dumps(rows)


def test_formatter_redacts_nested_credentials_and_exception_source():
    from cluster.control_plane.service_logging import JsonFormatter
    record = logging.LogRecord('worker', logging.ERROR, __file__, 1, 'api_key=hidden', (), None)
    record.fields = {'repo': 'owner/repo', 'token': 'private-value',
                     'nested': {'authorization': 'Bearer private-value'}}
    formatted = JsonFormatter('fix-worker').format(record)
    assert 'hidden' not in formatted and 'private-value' not in formatted
    data = json.loads(formatted)
    assert data['timestamp'].endswith(('-05:00', '-06:00'))
    assert data['service'] == 'fix-worker' and data['repo'] == 'owner/repo'
    assert all(key in data for key in ['machine', 'job_id', 'pr_number', 'sha', 'correlation_id'])


def test_rotation_preserves_json_and_bounds_backups(tmp_path):
    from cluster.control_plane.service_logging import RetainedRotatingHandler, JsonFormatter
    handler = RetainedRotatingHandler(tmp_path / 'worker.jsonl', maxBytes=300, backupCount=2)
    handler.setFormatter(JsonFormatter('fix-worker'))
    for i in range(12):
        record = logging.LogRecord('worker', logging.INFO, __file__, 1, 'transition', (), None)
        record.fields = {'sequence': i}
        handler.emit(record)
    handler.close()
    files = list(tmp_path.glob('worker.jsonl*'))
    assert len(files) == 3
    for path in files:
        for line in path.read_text().splitlines():
            assert json.loads(line)['event'] == 'transition'


def test_failure_names_exclude_assertions_and_parameter_values():
    from cluster.control_plane.service_logging import failing_tests
    output = 'ERROR tests/test_env.py - api_key=secret\nFAILED tests/test_gate.py::test_gate[secret-param] - private detail\n+secret-diff\n'
    assert failing_tests(output) == ['tests/test_env.py', 'tests/test_gate.py::test_gate']


def test_worker_failure_has_job_correlation_without_exception_arguments(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from cluster.control_plane import fix_worker
    monkeypatch.setenv('STORYPOOL_LOG_DIR', str(tmp_path))
    ledger = MagicMock()
    ledger.get_recent_jobs.return_value = [SimpleNamespace(
        job_id='job-7', repo='owner/repo', pr_number=7, head_sha='a' * 40,
        machine='nucbox', provider='codex')]
    with patch.object(fix_worker, 'get_ledger', return_value=ledger), \
         patch('cluster.control_plane.node_control.is_draining', return_value=False), \
         patch.object(fix_worker, 'findings_for_pr', return_value=['repair']), \
         patch.object(fix_worker, 'fix_pr', side_effect=subprocess.CalledProcessError(
             2, ['codex', 'api_key=do-not-log-this'])), \
         patch.object(fix_worker, 'model_label', return_value='default xhigh'):
        assert fix_worker.execute('job-7') == 1
    rows = [json.loads(x) for x in (tmp_path / 'fix-worker-job-7.jsonl').read_text().splitlines()]
    assert rows[-1]['event'] == 'job_failed'
    assert rows[-1]['correlation_id'] == 'job-7' and rows[-1]['sha'] == 'a' * 40
    assert rows[-1]['traceback'] and rows[-1]['repo'] == 'owner/repo'
    assert 'do-not-log-this' not in json.dumps(rows)
    assert 'do-not-log-this' not in str(ledger.fail_job.call_args)
    ledger.complete_job.assert_not_called()


def test_logging_expires_old_job_files_and_honors_level(tmp_path, monkeypatch):
    import os
    import time
    from cluster.control_plane.service_logging import event
    monkeypatch.setenv('STORYPOOL_LOG_DIR', str(tmp_path))
    monkeypatch.setenv('STORYPOOL_LOG_LEVEL', 'ERROR')
    old = tmp_path / 'fix-worker-old-job.jsonl'
    old.write_text('{}\n')
    os.utime(old, (time.time() - 8 * 86400,) * 2)
    event('fix-worker', 'filtered')
    assert not old.exists()
    assert (tmp_path / 'fix-worker.jsonl').read_text() == ''
    event('fix-worker', 'visible', level=logging.ERROR)
    assert json.loads((tmp_path / 'fix-worker.jsonl').read_text())['event'] == 'visible'


def test_timeout_bytes_keep_original_error_and_extract_names(tmp_path, monkeypatch):
    from cluster.control_plane import fix_worker
    monkeypatch.setenv('STORYPOOL_LOG_DIR', str(tmp_path))
    error = subprocess.TimeoutExpired(['python', '-m', 'pytest'], 1,
                                      output=b'ERROR tests/test_wait.py::test_wait - private\n')
    with patch.object(fix_worker.subprocess, 'run', side_effect=error):
        with pytest.raises(subprocess.TimeoutExpired):
            fix_worker.run(['python', '-m', 'pytest'])
    row = json.loads((tmp_path / 'fix-worker.jsonl').read_text().splitlines()[-1])
    assert row['failed_tests'] == ['tests/test_wait.py::test_wait']
    assert row['error_type'] == 'TimeoutExpired'


def test_missing_npm_has_actionable_summary_without_argv():
    from cluster.control_plane.service_logging import failure_reason
    assert failure_reason(FileNotFoundError(2, 'missing', 'npm')) == 'Required executable or file is missing: npm'
    error = subprocess.CalledProcessError(2, ['codex', 'api_key=private'])
    assert failure_reason(error) == 'codex exited with code 2; see command_failed event'
