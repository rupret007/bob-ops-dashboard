"""A refused source response must not signal a stale PID or reset its budget."""
import os
import signal
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cluster.control_plane.local_karen import git_context as transport


def exited_pipe(data):
    reader, writer = os.pipe()
    os.write(writer, data)
    os.close(writer)
    return os.fdopen(reader, 'rb')


def owned_process(monkeypatch, poll):
    process = SimpleNamespace(pid=999991, stdout=exited_pipe(b'01234567890'),
                              stderr=exited_pipe(b''), poll=poll,
                              wait=Mock(return_value=0))
    monkeypatch.setattr(transport.subprocess, 'Popen', Mock(return_value=process))
    monkeypatch.setattr(transport.os, 'getpgid', Mock(return_value=process.pid))
    signal_group = Mock(side_effect=PermissionError('synthetic exited-group race'))
    monkeypatch.setattr(transport.os, 'killpg', signal_group)
    shared = SimpleNamespace(seconds=30)
    return process, signal_group, shared


def test_already_exited_child_preserves_output_limit_without_signaling(monkeypatch, tmp_path):
    process, signal_group, shared = owned_process(monkeypatch, Mock(return_value=0))
    with pytest.raises(transport.ContextUnavailable, match='response_size'):
        transport.BoundedGit(tmp_path, shared).run('cat-file', 'blob', 'a' * 40, maximum=10)
    signal_group.assert_not_called()
    assert process.stdout.closed and process.stderr.closed and shared.seconds < 30


def test_exit_between_poll_and_signal_preserves_original_refusal(monkeypatch, tmp_path):
    process, signal_group, shared = owned_process(monkeypatch, Mock(side_effect=[None, 0]))
    with pytest.raises(transport.ContextUnavailable, match='response_size'):
        transport.BoundedGit(tmp_path, shared).run('cat-file', 'blob', 'a' * 40, maximum=10)
    signal_group.assert_called_once_with(process.pid, signal.SIGKILL)
    assert process.stdout.closed and process.stderr.closed and shared.seconds < 30


def test_permission_failure_for_live_child_stays_failed_and_charges_time(monkeypatch, tmp_path):
    process, signal_group, shared = owned_process(monkeypatch, Mock(return_value=None))
    with pytest.raises(PermissionError):
        transport.BoundedGit(tmp_path, shared).run('cat-file', 'blob', 'a' * 40, maximum=10)
    signal_group.assert_called_once()
    assert process.stdout.closed and process.stderr.closed and shared.seconds < 30


def test_reaping_timeout_is_bounded_and_cannot_reset_shared_budget(monkeypatch, tmp_path):
    process, signal_group, shared = owned_process(monkeypatch, Mock(return_value=0))
    process.wait.side_effect = subprocess.TimeoutExpired('synthetic-child', 1)
    with pytest.raises(transport.ContextUnavailable, match='time_budget'):
        transport.BoundedGit(tmp_path, shared).run('cat-file', 'blob', 'a' * 40, maximum=10)
    signal_group.assert_not_called()
    assert process.wait.call_args.kwargs['timeout'] <= 1
    assert process.stdout.closed and process.stderr.closed and shared.seconds < 30
