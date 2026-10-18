#!/usr/bin/env python3
"""Job ledger for StoryPool dispatch system.

OUTCOME-VERIFIED: Jobs are tracked from dispatch to verified completion.
A job counts as done only when outcome is verified (commit pushed or verdict posted).
Orphaned leases expire after 45 minutes.

Uses JSONL for simplicity (stdlib-only, Python 3.9 compatible).
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any

JOBS_DIR = Path(
    os.environ.get("STORYPOOL_JOBS_DIR", "~/.local/share/storypool/jobs")
).expanduser()

LEDGER_FILE = JOBS_DIR / "ledger.jsonl"
LEASE_EXPIRY_MINUTES = 45


class JobState(Enum):
    """State of a job in the ledger."""
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class Job:
    """A job in the ledger."""
    job_id: str
    repo: str
    pr_number: int
    head_sha: str
    provider: str  # codex, cursor, gemini, etc.
    machine: str  # mini, nucbox
    model: str = ""  # provider model/effort label recorded by the worker from its real argv
    
    state: str = JobState.QUEUED.value
    
    lease_acquired: str = ""
    lease_expires: str = ""
    
    pid: int = 0
    start_time: str = ""
    end_time: str = ""
    
    outcome: str = ""
    outcome_verified: bool = False
    outcome_sha: str = ""
    
    error: str = ""
    
    created_at: str = ""
    updated_at: str = ""
    cluster_claim: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
    
    def has_valid_lease(self) -> bool:
        """Check if job has a non-expired lease."""
        if not self.lease_expires:
            return False
        try:
            expires = datetime.fromisoformat(self.lease_expires.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) < expires
        except (ValueError, TypeError):
            return False
    
    def is_process_running(self) -> bool:
        """Check if the job's process is still running."""
        if self.pid <= 0:
            return False
        try:
            os.kill(self.pid, 0)
            return True
        except PermissionError:
            # The process exists but belongs to another user/namespace.
            return True
        except (OSError, ProcessLookupError):
            pass
        # A checker in a different PID namespace (e.g. a sandboxed monitor)
        # cannot see the worker. Linux workers run as transient systemd units,
        # so ask systemd; if systemd cannot answer, do not declare the worker
        # dead (the lease expiry still bounds a truly dead job).
        unit_state = _systemd_job_unit_state(self.job_id)
        if unit_state is None:
            return sys.platform == "linux"
        return unit_state in ("active", "activating", "reloading")


def _systemd_job_unit_state(job_id: str) -> Optional[str]:
    """Return systemd's state for a worker unit, or None when unknown."""
    if sys.platform != "linux" or not job_id:
        return None
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", f"storypool-job-{job_id}.service"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    state = (result.stdout or "").strip()
    if state in ("active", "activating", "reloading", "inactive", "failed", "deactivating"):
        return state
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_job_id(repo: str, pr_number: int, head_sha: str) -> str:
    """Generate a unique job ID."""
    ts = int(time.time() * 1000)
    return f"{repo.replace('/', '_')}_{pr_number}_{head_sha[:8]}_{ts}"


class JobLedger:
    """Thread-safe job ledger with file locking.
    
    OUTCOME-VERIFIED:
    - Dispatcher must not re-dispatch a PR that already holds a lease
    - A job counts as done only when verified (commit pushed or verdict posted)
    - Orphaned leases expire after 45 min
    """
    
    def __init__(self, jobs_dir: Optional[Path] = None):
        self.jobs_dir = jobs_dir or JOBS_DIR
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_file = self.jobs_dir / "ledger.jsonl"
    
    def _lock_file(self) -> Path:
        return self.jobs_dir / ".ledger.lock"
    
    def _load_jobs(self) -> List[Job]:
        """Load all jobs from ledger file."""
        jobs = []
        if not self.ledger_file.is_file():
            return jobs
        
        try:
            for line in self.ledger_file.read_text().splitlines():
                if line.strip():
                    try:
                        jobs.append(Job.from_dict(json.loads(line)))
                    except (json.JSONDecodeError, TypeError):
                        pass
        except OSError:
            pass
        
        return jobs
    
    def _save_jobs(self, jobs: List[Job]) -> None:
        """Save all jobs to ledger file atomically."""
        previous = {job.job_id: job.to_dict() for job in self._load_jobs()}
        tmp_file = self.jobs_dir / ".ledger.tmp"
        try:
            with open(tmp_file, "w") as f:
                for job in jobs:
                    if previous.get(job.job_id) != job.to_dict():
                        job.updated_at = _now_iso()
                    f.write(json.dumps(job.to_dict()) + "\n")
            tmp_file.rename(self.ledger_file)
        except OSError:
            if tmp_file.exists():
                tmp_file.unlink()
            raise
    
    def _with_lock(self, fn):
        """Execute function with file lock (non-blocking with retry)."""
        lock_file = self._lock_file()
        lock_file.touch()
        
        for attempt in range(10):
            try:
                with open(lock_file, "r+") as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        return fn()
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except BlockingIOError:
                time.sleep(0.1)
        
        raise TimeoutError("job ledger lock unavailable")
    
    def has_active_lease(self, repo: str, pr_number: int) -> bool:
        """Check if PR already has an active (non-expired) lease.
        
        CRITICAL: Dispatcher must check this before dispatching.
        """
        def _check():
            jobs = self._load_jobs()
            for job in jobs:
                if job.repo == repo and job.pr_number == pr_number:
                    if job.state in (JobState.QUEUED.value, JobState.RUNNING.value):
                        if job.has_valid_lease():
                            return True
                        if job.is_process_running():
                            return True
            return False
        
        return self._with_lock(_check)
    
    def get_active_job(self, repo: str, pr_number: int) -> Optional[Job]:
        """Get the active job for a PR if one exists."""
        def _get():
            jobs = self._load_jobs()
            for job in jobs:
                if job.repo == repo and job.pr_number == pr_number:
                    if job.state in (JobState.QUEUED.value, JobState.RUNNING.value):
                        if job.has_valid_lease() or job.is_process_running():
                            return job
            return None
        
        return self._with_lock(_get)
    
    def create_job(
        self,
        repo: str,
        pr_number: int,
        head_sha: str,
        provider: str,
        machine: str,
    ) -> Job:
        """Create a new job with a lease.
        
        Returns the job if created, or raises if PR already has active lease.
        """
        def _create():
            if any(j.repo == repo and j.pr_number == pr_number
                   and j.state in ("queued", "running")
                   and (j.has_valid_lease() or j.is_process_running())
                   for j in self._load_jobs()):
                raise ValueError(f"PR {repo}#{pr_number} already has active lease")
            
            now = _now_iso()
            expires = (datetime.now(timezone.utc) + timedelta(minutes=LEASE_EXPIRY_MINUTES)).isoformat()
            
            job = Job(
                job_id=_generate_job_id(repo, pr_number, head_sha),
                repo=repo,
                pr_number=pr_number,
                head_sha=head_sha,
                provider=provider,
                machine=machine,
                state=JobState.QUEUED.value,
                lease_acquired=now,
                lease_expires=expires,
                created_at=now,
                updated_at=now,
            )
            
            jobs = self._load_jobs()
            jobs.append(job)
            self._save_jobs(jobs)
            
            return job
        
        return self._with_lock(_create)
    
    def start_job(self, job_id: str, pid: int, model: str = "") -> Optional[Job]:
        """Mark job as running with PID (and the model label the worker will run)."""
        def _start():
            jobs = self._load_jobs()
            for job in jobs:
                if job.job_id == job_id:
                    job.state = JobState.RUNNING.value
                    job.pid = pid
                    if model:
                        job.model = model
                    job.start_time = _now_iso()
                    self._save_jobs(jobs)
                    return job
            return None
        
        return self._with_lock(_start)

    def attach_cluster_claim(self, job_id: str, claim: dict) -> None:
        def attach():
            jobs = self._load_jobs()
            for job in jobs:
                if job.job_id == job_id:
                    job.cluster_claim = claim
                    self._save_jobs(jobs)
                    return
            raise ValueError('Job missing while attaching cluster claim')
        self._with_lock(attach)
    
    def complete_job(
        self,
        job_id: str,
        outcome: str,
        outcome_sha: str = "",
        verified: bool = False,
    ) -> Optional[Job]:
        """Mark job as done with outcome.
        
        OUTCOME-VERIFIED: Set verified=True only when outcome is confirmed.
        """
        def _complete():
            jobs = self._load_jobs()
            for job in jobs:
                if job.job_id == job_id:
                    job.state = JobState.DONE.value
                    job.end_time = _now_iso()
                    job.outcome = outcome
                    job.outcome_sha = outcome_sha
                    job.outcome_verified = verified
                    self._save_jobs(jobs)
                    return job
            return None
        
        return self._with_lock(_complete)
    
    def fail_job(self, job_id: str, error: str) -> Optional[Job]:
        """Mark job as failed with error."""
        def _fail():
            jobs = self._load_jobs()
            for job in jobs:
                if job.job_id == job_id:
                    job.state = JobState.FAILED.value
                    job.end_time = _now_iso()
                    job.error = error
                    self._save_jobs(jobs)
                    return job
            return None
        
        return self._with_lock(_fail)
    
    def expire_stale_leases(self) -> List[Job]:
        """Expire leases that are past their expiry time.
        
        Also marks jobs as failed if their process has died.
        """
        def _expire():
            jobs = self._load_jobs()
            expired = []
            changed = False
            
            for job in jobs:
                if job.state not in (JobState.QUEUED.value, JobState.RUNNING.value):
                    continue
                
                should_expire = False
                reason = ""
                
                if job.state == JobState.RUNNING.value and not job.is_process_running():
                    should_expire = True
                    reason = "worker-died"
                elif not job.has_valid_lease():
                    should_expire = True
                    reason = "lease expired"
                
                if should_expire:
                    job.state = (JobState.FAILED.value if reason == "worker-died"
                                 else JobState.EXPIRED.value)
                    job.end_time = _now_iso()
                    job.error = reason
                    expired.append(job)
                    changed = True
            
            if changed:
                self._save_jobs(jobs)
            
            return expired
        
        return self._with_lock(_expire)
    
    def verify_job_outcome(self, job_id: str) -> bool:
        """Verify a job's outcome by checking for commit or verdict.
        
        OUTCOME-VERIFIED: Returns True only if we can confirm the outcome.
        """
        def _verify():
            jobs = self._load_jobs()
            for job in jobs:
                if job.job_id != job_id:
                    continue
                
                if job.outcome_verified:
                    return True
                
                verified = self._check_outcome(job)
                if verified:
                    job.outcome_verified = True
                    self._save_jobs(jobs)
                
                return verified
            
            return False
        
        return self._with_lock(_verify)
    
    def _check_outcome(self, job: Job) -> bool:
        """Check if a job's outcome can be verified.
        
        A job outcome is verified if:
        1. A commit was pushed to the PR branch after the job started
        2. A Karen verdict exists for this PR at or after the job's SHA
        """
        if job.outcome == "commit_pushed":
            return self._verify_commit_pushed(job)
        elif job.outcome == "verdict_posted":
            return self._verify_verdict_posted(job)
        
        return self._verify_commit_pushed(job) or self._verify_verdict_posted(job)
    
    def _verify_commit_pushed(self, job: Job) -> bool:
        """Check if a commit was pushed to the PR branch."""
        try:
            result = subprocess.run(
                ["gh", "pr", "view", str(job.pr_number), "--repo", job.repo,
                 "--json", "headRefOid"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                current_sha = data.get("headRefOid", "")
                if current_sha != job.head_sha:
                    return True
        except (subprocess.SubprocessError, json.JSONDecodeError):
            pass
        return False
    
    def _verify_verdict_posted(self, job: Job) -> bool:
        """Check if a Karen verdict was posted for this PR."""
        verdict_dir = Path("~/.local/share/storypool/verdicts").expanduser()
        if not verdict_dir.is_dir():
            verdict_dir = Path("~/.local/share/karen/verdicts").expanduser()
        
        if not verdict_dir.is_dir():
            return False
        
        safe_repo = job.repo.replace("/", "_")
        repo_verdict_dir = verdict_dir / safe_repo
        
        if not repo_verdict_dir.is_dir():
            return False
        
        for verdict_file in repo_verdict_dir.glob(f"pr-{job.pr_number}-*.json"):
            try:
                verdict = json.loads(verdict_file.read_text())
                verdict_sha = verdict.get("head_sha", verdict.get("sha", ""))
                if verdict_sha == job.head_sha or verdict_sha.startswith(job.head_sha[:8]):
                    return True
            except (json.JSONDecodeError, OSError):
                pass
        
        return False
    
    def get_running_jobs(self) -> List[Job]:
        """Get all currently running jobs."""
        def _get():
            jobs = self._load_jobs()
            return [j for j in jobs if j.state == JobState.RUNNING.value]
        
        return self._with_lock(_get)
    
    def get_recent_jobs(self, hours: int = 24) -> List[Job]:
        """Get jobs from the last N hours."""
        def _get():
            jobs = self._load_jobs()
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            
            recent = []
            for job in jobs:
                try:
                    created = datetime.fromisoformat(job.created_at.replace("Z", "+00:00"))
                    if created >= cutoff:
                        recent.append(job)
                except (ValueError, TypeError):
                    pass
            
            return recent
        
        return self._with_lock(_get)
    
    def get_jobs_for_pr(self, repo: str, pr_number: int) -> List[Job]:
        """Get all jobs for a specific PR."""
        def _get():
            jobs = self._load_jobs()
            return [j for j in jobs if j.repo == repo and j.pr_number == pr_number]
        
        return self._with_lock(_get)
    
    def summary(self) -> Dict[str, Any]:
        """Get summary statistics from the ledger."""
        def _summary():
            jobs = self._load_jobs()
            
            now = datetime.now(timezone.utc)
            day_ago = now - timedelta(hours=24)
            hour_ago = now - timedelta(hours=1)
            
            stats = {
                "total_jobs": len(jobs),
                "running": 0,
                "queued": 0,
                "done_24h": 0,
                "failed_24h": 0,
                "verified_24h": 0,
                "jobs_last_hour": 0,
            }
            
            for job in jobs:
                if job.state == JobState.RUNNING.value:
                    stats["running"] += 1
                elif job.state == JobState.QUEUED.value:
                    stats["queued"] += 1
                
                try:
                    created = datetime.fromisoformat(job.created_at.replace("Z", "+00:00"))
                    if created >= hour_ago:
                        stats["jobs_last_hour"] += 1
                    
                    if created >= day_ago:
                        if job.state == JobState.DONE.value:
                            stats["done_24h"] += 1
                            if job.outcome_verified:
                                stats["verified_24h"] += 1
                        elif job.state == JobState.FAILED.value:
                            stats["failed_24h"] += 1
                except (ValueError, TypeError):
                    pass
            
            return stats
        
        return self._with_lock(_summary)


_ledger = None


def get_ledger() -> JobLedger:
    """Get the global ledger instance."""
    global _ledger
    if _ledger is None:
        _ledger = JobLedger()
    return _ledger
