"""Whole-job deadlines fence subsequent commands and stop the current group."""
import subprocess
import sys
import time
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane import fix_worker as worker


def test_expired_job_cannot_start_another_command():
    token = worker._job_deadline.set(time.monotonic() - 1)
    try:
        with patch.object(worker.subprocess, 'Popen') as spawn:
            with pytest.raises(subprocess.TimeoutExpired):
                worker._run_claimed(['git', 'push'], None, Mock(lost=False))
            spawn.assert_not_called()
    finally:
        worker._job_deadline.reset(token)


def test_job_deadline_stops_inflight_child_before_late_side_effect(tmp_path):
    marker = tmp_path / 'late-write'
    token = worker._job_deadline.set(time.monotonic() + .1)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            worker._run_claimed([sys.executable, '-c',
                'import time,pathlib;time.sleep(2);pathlib.Path(' + repr(str(marker)) + ').touch()'],
                None, Mock(lost=False))
    finally:
        worker._job_deadline.reset(token)
    time.sleep(1.2)
    assert not marker.exists()
