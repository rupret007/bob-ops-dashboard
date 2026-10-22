#!/usr/bin/env python3
"""Tests for job ledger (OUTCOME-VERIFIED)."""

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from cluster.control_plane.job_ledger import (
    Job,
    JobLedger,
    JobState,
    LEASE_EXPIRY_MINUTES,
    _generate_job_id,
)


class TestJobModel(unittest.TestCase):
    """Test Job data model."""
    
    def test_job_to_dict(self):
        """Job should serialize to dict."""
        job = Job(
            job_id="test_1",
            repo="owner/repo",
            pr_number=1,
            head_sha="abc123",
            provider="codex",
            machine="nucbox",
        )
        d = job.to_dict()
        self.assertEqual(d["job_id"], "test_1")
        self.assertEqual(d["repo"], "owner/repo")
        self.assertEqual(d["pr_number"], 1)
    
    def test_job_from_dict(self):
        """Job should deserialize from dict."""
        d = {
            "job_id": "test_2",
            "repo": "owner/repo",
            "pr_number": 2,
            "head_sha": "def456",
            "provider": "cursor",
            "machine": "mini",
            "state": "running",
        }
        job = Job.from_dict(d)
        self.assertEqual(job.job_id, "test_2")
        self.assertEqual(job.state, "running")
    
    def test_has_valid_lease(self):
        """has_valid_lease should check expiry."""
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        
        job = Job(
            job_id="test_3",
            repo="owner/repo",
            pr_number=3,
            head_sha="ghi789",
            provider="codex",
            machine="nucbox",
            lease_expires=future,
        )
        self.assertTrue(job.has_valid_lease())
        
        job.lease_expires = past
        self.assertFalse(job.has_valid_lease())
    
    def test_is_process_running(self):
        """is_process_running should check if PID exists."""
        job = Job(
            job_id="test_4",
            repo="owner/repo",
            pr_number=4,
            head_sha="jkl012",
            provider="codex",
            machine="nucbox",
            pid=os.getpid(),
        )
        self.assertTrue(job.is_process_running())
        
        job.pid = 999999999
        # A sandbox may not see host PIDs or its user manager. Establish the
        # supervisor's answer rather than depending on the test host's bus.
        with patch('cluster.control_plane.job_ledger._systemd_job_unit_state', return_value='inactive') as supervisor:
            self.assertFalse(job.is_process_running())
            supervisor.assert_called_once_with('test_4')


class TestJobLedger(unittest.TestCase):
    """Test JobLedger operations."""
    
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ledger = JobLedger(jobs_dir=Path(self.tmpdir))
    
    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
    
    def test_create_job(self):
        """Should create a job with lease."""
        job = self.ledger.create_job(
            repo="owner/repo",
            pr_number=1,
            head_sha="abc123",
            provider="codex",
            machine="nucbox",
        )
        
        self.assertIsNotNone(job.job_id)
        self.assertEqual(job.state, JobState.QUEUED.value)
        self.assertTrue(job.has_valid_lease())
    
    def test_no_duplicate_lease(self):
        """Should not create duplicate lease for same PR."""
        self.ledger.create_job(
            repo="owner/repo",
            pr_number=1,
            head_sha="abc123",
            provider="codex",
            machine="nucbox",
        )
        
        with self.assertRaises(ValueError):
            self.ledger.create_job(
                repo="owner/repo",
                pr_number=1,
                head_sha="def456",
                provider="codex",
                machine="nucbox",
            )
    
    def test_has_active_lease(self):
        """has_active_lease should detect existing lease."""
        self.assertFalse(self.ledger.has_active_lease("owner/repo", 1))
        
        self.ledger.create_job(
            repo="owner/repo",
            pr_number=1,
            head_sha="abc123",
            provider="codex",
            machine="nucbox",
        )
        
        self.assertTrue(self.ledger.has_active_lease("owner/repo", 1))
        self.assertFalse(self.ledger.has_active_lease("owner/repo", 2))
    
    def test_start_job(self):
        """start_job should set PID and state."""
        job = self.ledger.create_job(
            repo="owner/repo",
            pr_number=1,
            head_sha="abc123",
            provider="codex",
            machine="nucbox",
        )
        
        started = self.ledger.start_job(job.job_id, 12345)
        self.assertEqual(started.state, JobState.RUNNING.value)
        self.assertEqual(started.pid, 12345)
        self.assertNotEqual(started.start_time, "")
    
    def test_complete_job(self):
        """complete_job should mark done with outcome."""
        job = self.ledger.create_job(
            repo="owner/repo",
            pr_number=1,
            head_sha="abc123",
            provider="codex",
            machine="nucbox",
        )
        
        completed = self.ledger.complete_job(
            job.job_id,
            outcome="commit_pushed",
            outcome_sha="new_sha",
            verified=True,
        )
        
        self.assertEqual(completed.state, JobState.DONE.value)
        self.assertEqual(completed.outcome, "commit_pushed")
        self.assertTrue(completed.outcome_verified)
    
    def test_fail_job(self):
        """fail_job should mark failed with error."""
        job = self.ledger.create_job(
            repo="owner/repo",
            pr_number=1,
            head_sha="abc123",
            provider="codex",
            machine="nucbox",
        )
        
        failed = self.ledger.fail_job(job.job_id, "codex not found")
        self.assertEqual(failed.state, JobState.FAILED.value)
        self.assertEqual(failed.error, "codex not found")
    
    def test_expire_stale_leases(self):
        """Stale leases should be expired."""
        job = self.ledger.create_job(
            repo="owner/repo",
            pr_number=1,
            head_sha="abc123",
            provider="codex",
            machine="nucbox",
        )
        
        jobs = self.ledger._load_jobs()
        jobs[0].lease_expires = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).isoformat()
        self.ledger._save_jobs(jobs)
        
        expired = self.ledger.expire_stale_leases()
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0].state, JobState.EXPIRED.value)
    
    def test_get_running_jobs(self):
        """Should list running jobs."""
        job = self.ledger.create_job(
            repo="owner/repo",
            pr_number=1,
            head_sha="abc123",
            provider="codex",
            machine="nucbox",
        )
        self.ledger.start_job(job.job_id, 12345)
        
        running = self.ledger.get_running_jobs()
        self.assertEqual(len(running), 1)
        self.assertEqual(running[0].job_id, job.job_id)
    
    def test_summary(self):
        """Summary should aggregate stats."""
        job = self.ledger.create_job(
            repo="owner/repo",
            pr_number=1,
            head_sha="abc123",
            provider="codex",
            machine="nucbox",
        )
        self.ledger.start_job(job.job_id, 12345)
        self.ledger.complete_job(job.job_id, "done", verified=True)
        
        job2 = self.ledger.create_job(
            repo="owner/repo",
            pr_number=2,
            head_sha="def456",
            provider="codex",
            machine="nucbox",
        )
        
        summary = self.ledger.summary()
        self.assertEqual(summary["total_jobs"], 2)
        self.assertEqual(summary["queued"], 1)
        self.assertEqual(summary["done_24h"], 1)
        self.assertEqual(summary["verified_24h"], 1)
    
    def test_persistence(self):
        """Jobs should persist to disk."""
        job = self.ledger.create_job(
            repo="owner/repo",
            pr_number=1,
            head_sha="abc123",
            provider="codex",
            machine="nucbox",
        )
        
        ledger2 = JobLedger(jobs_dir=Path(self.tmpdir))
        jobs = ledger2._load_jobs()
        
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_id, job.job_id)


class TestGenerateJobId(unittest.TestCase):
    """Test job ID generation."""
    
    def test_generates_unique_ids(self):
        """Should generate unique IDs."""
        id1 = _generate_job_id("owner/repo", 1, "abc123")
        time.sleep(0.01)
        id2 = _generate_job_id("owner/repo", 1, "abc123")
        
        self.assertNotEqual(id1, id2)
    
    def test_id_contains_pr_info(self):
        """ID should contain PR info."""
        job_id = _generate_job_id("owner/repo", 42, "abc123def")
        
        self.assertIn("owner_repo", job_id)
        self.assertIn("42", job_id)
        self.assertIn("abc123de", job_id)


class TestOutcomeVerification(unittest.TestCase):
    """Test outcome verification logic."""
    
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ledger = JobLedger(jobs_dir=Path(self.tmpdir))
    
    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
    
    @patch("subprocess.run")
    def test_verify_commit_pushed(self, mock_run):
        """Should verify commit was pushed."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"headRefOid": "new_sha_after_push"}',
        )
        
        job = self.ledger.create_job(
            repo="owner/repo",
            pr_number=1,
            head_sha="abc123",
            provider="codex",
            machine="nucbox",
        )
        self.ledger.complete_job(job.job_id, "commit_pushed")
        
        verified = self.ledger.verify_job_outcome(job.job_id)
        self.assertTrue(verified)
    
    @patch("subprocess.run")
    def test_no_verify_if_same_sha(self, mock_run):
        """Should not verify if SHA unchanged."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"headRefOid": "abc123"}',
        )
        
        job = self.ledger.create_job(
            repo="owner/repo",
            pr_number=1,
            head_sha="abc123",
            provider="codex",
            machine="nucbox",
        )
        self.ledger.complete_job(job.job_id, "commit_pushed")
        
        verified = self.ledger.verify_job_outcome(job.job_id)
        self.assertFalse(verified)


if __name__ == "__main__":
    unittest.main()
