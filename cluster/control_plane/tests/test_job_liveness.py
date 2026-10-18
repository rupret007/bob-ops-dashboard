from unittest.mock import patch
from cluster.control_plane import job_ledger
from cluster.control_plane.job_ledger import Job


def _job(pid=999999):
    job = Job.__new__(Job)
    job.pid = pid
    job.job_id = 'owner_repo_1_abcdef12_1'
    return job


def test_invisible_pid_but_active_systemd_unit_is_running():
    with patch('os.kill', side_effect=ProcessLookupError), \
         patch.object(job_ledger, '_systemd_job_unit_state', return_value='active'):
        assert _job().is_process_running()


def test_invisible_pid_and_inactive_unit_is_dead():
    with patch('os.kill', side_effect=ProcessLookupError), \
         patch.object(job_ledger, '_systemd_job_unit_state', return_value='failed'):
        assert not _job().is_process_running()


def test_permission_error_means_process_exists():
    with patch('os.kill', side_effect=PermissionError):
        assert _job().is_process_running()


def test_no_pid_is_not_running():
    assert not _job(pid=0).is_process_running()
