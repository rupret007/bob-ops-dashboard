"""Refusal tests run everywhere; real descendant tests require delegated cgroupfs."""
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane import fix_worker
from cluster.control_plane.coordination import CoordinationError
from cluster.control_plane.owner_process_containment import OwnerProcessContainment


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
def test_unsupported_platform_refuses_before_spawn(platform):
    token = fix_worker._execution_guard.set(lambda: None)
    try:
        with patch('cluster.control_plane.owner_process_containment.sys.platform', platform), patch.object(subprocess, 'Popen') as spawn:
            with pytest.raises(CoordinationError, match='unsupported'):
                fix_worker._run_claimed([sys.executable, '-c', 'pass'], None, Mock(lost=False))
            spawn.assert_not_called()
    finally:
        fix_worker._execution_guard.reset(token)


def test_absent_delegation_refuses_before_spawn(monkeypatch):
    monkeypatch.delenv('STORYPOOL_OWNER_CGROUP_ROOT', raising=False)
    token = fix_worker._execution_guard.set(lambda: None)
    try:
        with patch.object(subprocess, 'Popen') as spawn:
            with pytest.raises(CoordinationError, match='delegated|unsupported'):
                fix_worker._run_claimed([sys.executable, '-c', 'pass'], None, Mock(lost=False))
            spawn.assert_not_called()
    finally:
        fix_worker._execution_guard.reset(token)


@pytest.mark.parametrize('root', ['/tmp/fake-cgroup', '/sys/fs/cgroup', 'relative'])
def test_invalid_roots_are_refused(monkeypatch, root):
    monkeypatch.setenv('STORYPOOL_OWNER_CGROUP_ROOT', root)
    with pytest.raises(CoordinationError):
        OwnerProcessContainment()



def test_owner_guard_without_lease_never_uses_uncontained_fallback():
    token = fix_worker._execution_guard.set(lambda: None)
    try:
        with patch.object(subprocess, 'run') as spawn:
            with pytest.raises(CoordinationError, match='cluster lease'):
                fix_worker.run([sys.executable, '-c', 'pass'])
            spawn.assert_not_called()
    finally:
        fix_worker._execution_guard.reset(token)


def test_pause_during_containment_setup_refuses_spawn_and_closes_group():
    paused = False
    containment = Mock()
    def setup():
        nonlocal paused
        paused = True
        return containment
    def guard():
        if paused:
            raise CoordinationError('paused during containment setup')
    token = fix_worker._execution_guard.set(guard)
    try:
        with patch('cluster.control_plane.owner_process_containment.OwnerProcessContainment', side_effect=setup), patch.object(subprocess, 'Popen') as spawn:
            with pytest.raises(CoordinationError, match='paused during containment setup'):
                fix_worker._run_claimed([sys.executable, '-c', 'pass'], None, Mock(lost=False))
            spawn.assert_not_called()
            containment.close.assert_called_once_with()
    finally:
        fix_worker._execution_guard.reset(token)


def test_teardown_error_reaps_leader_without_claiming_descendants_stopped():
    # Lifecycle control-flow test only; mock cgroup proves no kernel isolation.
    containment = Mock()
    containment.close.side_effect = CoordinationError('descendant teardown unconfirmed')
    process = Mock(returncode=0)
    process.communicate.return_value = ('', '')
    token = fix_worker._execution_guard.set(lambda: None)
    try:
        with patch('cluster.control_plane.owner_process_containment.OwnerProcessContainment', return_value=containment), patch.object(subprocess, 'Popen', return_value=process):
            with pytest.raises(CoordinationError, match='teardown unconfirmed'):
                fix_worker._run_claimed([sys.executable, '-c', 'pass'], None, Mock(lost=False))
            process.kill.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=2)
    finally:
        fix_worker._execution_guard.reset(token)
