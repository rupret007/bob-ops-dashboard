"""Workers must survive dispatch exit, and dead workers must release leases."""
import os
import plistlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from cluster.control_plane.dispatcher import _spawn_worker
from cluster.control_plane.job_ledger import Job, JobLedger


@pytest.mark.parametrize('platform', ['linux', 'darwin'])
def test_worker_uses_platform_lifecycle(platform, tmp_path):
    job_id = 'owner_repo_7_abcdef01_123'
    with patch('cluster.control_plane.job_ledger.JOBS_DIR', tmp_path), \
         patch('cluster.control_plane.dispatcher.sys.platform', platform), \
         patch('cluster.control_plane.dispatcher.subprocess.run') as run, \
         patch('cluster.control_plane.dispatcher.subprocess.Popen') as popen:
        popen.return_value.pid = 123
        pid = _spawn_worker('owner/repo', 7, 'abcdef01', 'codex', 'nucbox', job_id)
    if platform == 'linux':
        popen.assert_not_called()
        argv = run.call_args.args[0]
        assert argv[:2] == ['systemd-run', '--user']
        assert '--unit=storypool-job-' + job_id in argv
        assert '--collect' in argv
        assert '--property=Type=exec' in argv
        assert '--property=StandardOutput=append:' + str(tmp_path / (job_id + '.log')) in argv
        assert '--setenv=STORYPOOL_JOBS_DIR=' + str(tmp_path) in argv
        assert argv[-3:] == ['-m', 'cluster.control_plane.fix_worker', job_id]
        assert run.call_args.kwargs['check'] is True
        assert pid == 0  # The worker, not systemd-run, records its PID.
    else:
        run.assert_not_called()
        assert popen.call_args.kwargs['start_new_session'] is True
        assert pid == 123
        plist = Path(__file__).parents[1] / 'install/com.storypool.dispatch.plist'
        assert plistlib.loads(plist.read_bytes())['AbandonProcessGroup'] is True


def test_manager_failure_does_not_fall_back_to_child(tmp_path):
    with patch('cluster.control_plane.job_ledger.JOBS_DIR', tmp_path), \
         patch('cluster.control_plane.dispatcher.sys.platform', 'linux'), \
         patch('cluster.control_plane.dispatcher.subprocess.run',
               side_effect=subprocess.CalledProcessError(1, ['systemd-run'])), \
         patch('cluster.control_plane.dispatcher.subprocess.Popen') as popen:
        with pytest.raises(subprocess.CalledProcessError):
            _spawn_worker('owner/repo', 7, 'abc', 'codex', 'nucbox', 'owner_repo_7_abc_123')
        popen.assert_not_called()


@pytest.mark.parametrize('lease_expired', [False, True])
def test_dead_worker_is_failed_even_after_lease_expiry(tmp_path, lease_expired):
    ledger = JobLedger(tmp_path)
    job = ledger.create_job('owner/repo', 7, 'abcdef01', 'codex', 'nucbox')
    ledger.start_job(job.job_id, 12345)
    with patch.object(Job, 'is_process_running', return_value=False), \
         patch.object(Job, 'has_valid_lease', return_value=not lease_expired):
        changed = ledger.expire_stale_leases()
    assert len(changed) == 1
    assert changed[0].state == 'failed'
    assert changed[0].error == 'worker-died'
    assert changed[0].end_time
    assert not changed[0].outcome_verified
    assert ledger._load_jobs()[0].state == 'failed'
    assert ledger.expire_stale_leases() == []


def test_live_worker_keeps_lease(tmp_path):
    ledger = JobLedger(tmp_path)
    job = ledger.create_job('owner/repo', 7, 'abcdef01', 'codex', 'nucbox')
    ledger.start_job(job.job_id, os.getpid())
    assert ledger.expire_stale_leases() == []
    assert ledger._load_jobs()[0].state == 'running'
