#!/usr/bin/env python3
"""Lane dispatcher service for StoryPool control plane.

OUTCOME-VERIFIED: Dispatch is tracked in the job ledger.
- Dispatcher must not re-dispatch a PR that already holds a lease
- Jobs count as done only when verified (commit pushed or verdict posted)
- Orphaned leases expire after 45 min
- Dispatch actually spawns the Codex/Cursor worker process

Provider allocation rules:
- Mini: load < 10, swap < 80%, disk >= 15GB
- NucBox: load < 12, up to 7 lanes with 2 reserved for Codex when Codex work queued
- Codex: NucBox-only, stops new lanes at 75% weekly spend
- Cursor: used fully (no artificial cap)
- Gemini: used fully for priority judgment only
- Claude: 1 lane max at a time
- MiniMax: 1-2 cheap lanes
- Grok: NEVER used (target ~$0 spend)

Safety invariants:
- No sudo
- No force-push
- No tags/releases
- Never touch ~/.cursor, auth files, Keychain, or svc-* screens
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..never_touch import is_never_touch
from .budget_tracker import BudgetTracker
from .guards import MachineGuards
from .job_ledger import JobLedger, get_ledger, Job, JobState

DISPATCH_LOG_DIR = Path(
    os.environ.get("STORYPOOL_DISPATCH_LOG_DIR", "~/.local/share/storypool/dispatch")
).expanduser()

REPOS = [
    "rupret007/webjam",
    "rupret007/StoryLiner",
    "rupret007/StoryBoard",
    "rupret007/Rad-Dad-Merch",
    "rupret007/RadDadSite",
    "rupret007/Turdanoid",
    "rupret007/rad-dad-show-night",
    "rupret007/Andrea_NanoBot",
    "rupret007/bob-ops-dashboard",
    "rupret007/Cursor-OpenClaw-Integration",
]

IMPLEMENTED_PROVIDERS = frozenset({"codex"})

PROVIDER_RULES = {
    "codex": {
        "machines": ["nucbox"],
        "max_concurrent": 2,
        "weekly_cap_pct": 75.0,
        "reserved_when_queued": 2,
    },
    "cursor": {
        "machines": ["mini", "nucbox"],
        "max_concurrent": None,
        "weekly_cap_pct": None,
    },
    "gemini": {
        "machines": ["mini", "nucbox"],
        "max_concurrent": None,
        "weekly_cap_pct": None,
    },
    "claude": {
        "machines": ["mini", "nucbox"],
        "max_concurrent": 1,
        "weekly_cap_pct": None,
    },
    "minimax": {
        "machines": ["mini", "nucbox"],
        "max_concurrent": 2,
        "weekly_cap_pct": None,
    },
    "grok": {
        "machines": [],
        "max_concurrent": 0,
        "weekly_cap_pct": 0.0,
    },
}


@dataclass
class DispatchDecision:
    """A dispatch decision record."""
    
    timestamp: str
    action: str
    reason: str
    machine: str
    live: bool = False
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    repo: Optional[str] = None
    head_sha: Optional[str] = None
    provider: Optional[str] = None
    priority_score: Optional[float] = None
    guard_values: Optional[dict] = None
    extra: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_gh(args: list[str], default=None):
    """Run a gh CLI command and return parsed JSON, or default on failure."""
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return default
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        return default


def _get_open_prs(repo: str) -> list[dict]:
    """Get open PRs for a repository."""
    prs = _run_gh(
        ["pr", "list", "--repo", repo, "--state", "open", "--json",
         "number,title,author,headRefOid,isDraft,url,reviewDecision,statusCheckRollup,labels"],
        default=[]
    )
    return prs if isinstance(prs, list) else []


def _get_active_lanes(machine: str) -> dict[str, int]:
    """Get count of active lanes per provider.
    
    In production this would read from NATS or a lease file.
    For now returns empty counts (shadow mode).
    """
    jobs = get_ledger().get_running_jobs()
    return {p: sum(j.provider == p and j.machine == machine for j in jobs)
            for p in PROVIDER_RULES}


def _has_codex_work_queued() -> bool:
    """Check if there is Codex work in the queue.
    
    When Codex work is queued, 2 NucBox lanes are reserved for it.
    """
    try:
        ledger = get_ledger()
        running = ledger.get_running_jobs()
        return any(j.provider == "codex" for j in running)
    except Exception:
        return False


def _spawn_worker(
    repo: str,
    pr_number: int,
    head_sha: str,
    provider: str,
    machine: str,
    job_id: str,
) -> int:
    """Spawn a worker process for the job.
    
    OUTCOME-VERIFIED: The worker must push a commit or post a verdict.
    Linux workers run in independent transient services. Returns 0 on Linux
    (the worker records its own PID), or the spawned PID on macOS.
    """
    from .job_ledger import JOBS_DIR
    jobs_dir = JOBS_DIR
    jobs_dir.mkdir(parents=True, exist_ok=True)
    
    worker_log = jobs_dir / f"{job_id}.log"
    
    if provider not in IMPLEMENTED_PROVIDERS:
        raise ValueError(f"Provider has no implemented worker: {provider}")
    if is_never_touch(repo):
        raise ValueError("never-touch repository")
    if sys.platform == "linux":
        # A new session cannot escape the dispatcher's systemd cgroup. Submit
        # an independent service and fail closed if the user manager rejects it.
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", job_id):
            raise ValueError("Invalid worker job ID")
        repo_dir = Path(__file__).resolve().parents[2]
        subprocess.run(
            ["systemd-run", "--user", f"--unit=storypool-job-{job_id}",
             "--collect", "--property=Type=exec",
             f"--working-directory={repo_dir}",
             f"--property=StandardOutput=append:{worker_log}",
             "--property=StandardError=inherit",
             f"--setenv=STORYPOOL_JOBS_DIR={jobs_dir}",
             f"--setenv=PYTHONPATH={repo_dir}",
             f"--setenv=PATH={os.environ.get('PATH', '/usr/bin:/bin')}",
             sys.executable, "-m", "cluster.control_plane.fix_worker", job_id],
            check=True, capture_output=True, text=True, timeout=30,
        )
        return 0
    with open(worker_log, "w") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-m", "cluster.control_plane.fix_worker", job_id],
            stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True,
            env={**os.environ, "STORYPOOL_JOBS_DIR": str(jobs_dir)},
        )
    return process.pid


class Dispatcher:
    """Lane dispatcher that allocates work to coding agents.
    
    OUTCOME-VERIFIED: All dispatch decisions are tracked in the job ledger.
    Runs in shadow mode by default (logs decisions, takes no action).
    Use run(live=True) to actually dispatch work.
    """
    
    def __init__(
        self,
        machine: str = "unknown",
        log_dir: Optional[Path] = None,
        budget_tracker: Optional[BudgetTracker] = None,
        job_ledger: Optional[JobLedger] = None,
    ):
        self.machine = machine.lower()
        self.log_dir = log_dir or DISPATCH_LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.budget = budget_tracker or BudgetTracker()
        self.ledger = job_ledger or get_ledger()
        self.decisions: list[DispatchDecision] = []
        
    def _log_file(self) -> Path:
        """Get the log file for today."""
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.log_dir / f"dispatch-{date}.jsonl"
    
    def _write_decision(self, decision: DispatchDecision) -> None:
        """Append a decision to the log file."""
        self.decisions.append(decision)
        with open(self._log_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(decision.to_dict(), sort_keys=True) + "\n")
    
    def _status_file(self) -> Path:
        """Get the status file path."""
        return self.log_dir / "dispatch-status.json"
    
    def _write_status(self, summary: dict) -> None:
        """Write a status file with the latest dispatch summary."""
        status = {
            "timestamp": _now_iso(),
            "machine": self.machine,
            "summary": summary,
        }
        self._status_file().write_text(json.dumps(status, indent=2))
    
    def check_machine_guards(self) -> tuple[bool, str, dict]:
        """Check if this machine can accept work."""
        from .node_control import is_draining
        if is_draining(self.machine):
            return False, "Node in maintenance; running jobs may finish", {}
        guards = MachineGuards(self.machine)
        result = guards.check_all()
        guard_values = {
            "load": guards.check_load().value,
        }
        if self.machine == "mini":
            guard_values["swap"] = guards.check_swap().value
            guard_values["disk"] = guards.check_disk().value
        elif self.machine == "nucbox":
            guard_values["lanes"] = guards.check_lanes().value
        return result.passed, result.reason, guard_values
    
    def check_provider_allowed(
        self, 
        provider: str, 
        active_lanes: dict[str, int]
    ) -> tuple[bool, str]:
        """Check if a provider can accept work on this machine."""
        rules = PROVIDER_RULES.get(provider)
        if not rules:
            return False, f"unknown provider {provider}"
        
        if provider == "grok":
            return False, "grok is never used (target ~$0 spend)"
        
        if provider not in IMPLEMENTED_PROVIDERS:
            return False, f"{provider} worker not implemented"

        if self.machine not in rules["machines"]:
            return False, f"{provider} not allowed on {self.machine}"
        
        max_concurrent = rules.get("max_concurrent")
        if max_concurrent is not None:
            current = active_lanes.get(provider, 0)
            if current >= max_concurrent:
                return False, f"{provider} at max concurrent ({current}/{max_concurrent})"
        
        weekly_cap = rules.get("weekly_cap_pct")
        if weekly_cap is not None and weekly_cap > 0:
            pct = self.budget.weekly_pct(provider)
            if pct >= weekly_cap:
                return False, f"{provider} at {pct:.1f}% of weekly cap (limit {weekly_cap}%)"
        
        if provider == "codex":
            can_use, reason = self.budget.can_use("codex")
            if not can_use:
                return False, reason
        
        return True, "ok"
    
    def get_nucbox_lane_capacity(self, active_lanes: dict[str, int]) -> int:
        """Get remaining lane capacity on NucBox.
        
        NucBox supports up to 7 lanes, with 2 reserved for Codex when
        Codex work is queued.
        """
        if self.machine != "nucbox":
            return 0
        
        total_active = sum(active_lanes.values())
        max_lanes = 7
        
        if _has_codex_work_queued():
            codex_active = active_lanes.get("codex", 0)
            codex_reserved = max(0, 2 - codex_active)
            effective_max = max_lanes - codex_reserved
        else:
            effective_max = max_lanes
        
        return max(0, effective_max - total_active)
    
    def _priority_score(self, pr: dict) -> float:
        """Calculate priority score for a PR (higher = more urgent)."""
        score = 0.0
        
        if pr.get("isDraft"):
            score -= 100.0
        
        review_decision = pr.get("reviewDecision") or ""
        if review_decision == "APPROVED":
            score += 50.0
        elif review_decision == "CHANGES_REQUESTED":
            score += 75.0
        
        checks = pr.get("statusCheckRollup") or []
        ci_failing = any(
            c.get("conclusion", "").lower() in ("failure", "error")
            for c in checks if isinstance(c, dict)
        )
        if ci_failing:
            score += 80.0
        
        labels = [lbl.get("name", "") for lbl in (pr.get("labels") or []) if isinstance(lbl, dict)]
        if "needs-karen" in labels:
            score += 60.0
        if "high-priority" in labels:
            score += 40.0
        
        additions = pr.get("additions") or 0
        deletions = pr.get("deletions") or 0
        diff_size = additions + deletions
        if diff_size < 100:
            score += 10.0
        elif diff_size > 1000:
            score -= 10.0
        
        return score
    
    def select_provider(
        self, 
        pr: dict, 
        active_lanes: dict[str, int]
    ) -> Optional[str]:
        """Only select providers with an executable worker implementation."""
        provider_order = ["codex"]

        for provider in provider_order:
            allowed, _ = self.check_provider_allowed(provider, active_lanes)
            if allowed:
                return provider
        
        return None
    
    def evaluate(self, live: bool = False) -> list[DispatchDecision]:
        """Evaluate dispatch decisions.
        
        Args:
            live: If True, actually dispatch work. If False, only log decisions.
        
        Returns:
            List of dispatch decisions made.
        """
        decisions = []
        
        # Reconcile even during maintenance; draining must not hide dead workers.
        self.ledger.expire_stale_leases()
        guards_ok, guards_reason, guard_values = self.check_machine_guards()
        if not guards_ok:
            decision = DispatchDecision(
                timestamp=_now_iso(),
                action="skip",
                reason=f"Machine guards failed: {guards_reason}",
                machine=self.machine,
                live=live,
                guard_values=guard_values,
            )
            self._write_decision(decision)
            decisions.append(decision)
            return decisions
        
        active_lanes = _get_active_lanes(self.machine)
        
        candidates = []
        for repo in REPOS:
            if is_never_touch(repo):
                continue
            prs = _get_open_prs(repo)
            for pr in prs:
                if pr.get("isDraft"):
                    continue
                pr["_repo"] = repo
                pr["_score"] = self._priority_score(pr)
                candidates.append(pr)
        
        candidates.sort(key=lambda p: -p.get("_score", 0))
        
        dispatched = 0
        max_dispatch = 3
        
        if self.machine == "nucbox":
            remaining_capacity = self.get_nucbox_lane_capacity(active_lanes)
            max_dispatch = min(max_dispatch, remaining_capacity)
        
        for pr in candidates[:15]:
            if dispatched >= max_dispatch:
                break
            
            repo = pr.get("_repo", "")
            pr_number = pr.get("number")
            head_sha = pr.get("headRefOid", "")
            
            if self.ledger.has_active_lease(repo, pr_number):
                decision = DispatchDecision(
                    timestamp=_now_iso(),
                    action="skip_leased",
                    reason=f"PR {repo}#{pr_number} already has active lease",
                    machine=self.machine,
                    live=live,
                    pr_number=pr_number,
                    pr_url=pr.get("url"),
                    repo=repo,
                    head_sha=head_sha,
                    guard_values=guard_values,
                )
                self._write_decision(decision)
                decisions.append(decision)
                continue
            
            provider = self.select_provider(pr, active_lanes)
            if not provider:
                continue
            
            if live:
                from .fix_worker import findings_for_pr
                try:
                    findings = findings_for_pr(repo, pr_number, head_sha)
                except (OSError, subprocess.SubprocessError, ValueError):
                    findings = []
                if not findings:
                    decision = DispatchDecision(
                        timestamp=_now_iso(), action="skip", reason="No Karen findings available",
                        machine=self.machine, live=True, repo=repo, pr_number=pr_number,
                        head_sha=head_sha, provider=provider,
                    )
                    self._write_decision(decision)
                    decisions.append(decision)
                    continue

            job = None
            pid = 0
            spawn_error = ""
            
            if live:
                from .node_control import is_draining
                if is_draining(self.machine):
                    break
                try:
                    job = self.ledger.create_job(
                        repo=repo,
                        pr_number=pr_number,
                        head_sha=head_sha,
                        provider=provider,
                        machine=self.machine,
                    )
                    
                    pid = _spawn_worker(
                        repo=repo,
                        pr_number=pr_number,
                        head_sha=head_sha,
                        provider=provider,
                        machine=self.machine,
                        job_id=job.job_id,
                    )
                    
                    # Worker owns RUNNING and terminal transitions to avoid races.
                    pass
                    
                except FileNotFoundError as e:
                    spawn_error = f"worker not found: {e}"
                    if job:
                        self.ledger.fail_job(job.job_id, spawn_error)
                except NotImplementedError as e:
                    spawn_error = str(e)
                    if job:
                        self.ledger.fail_job(job.job_id, spawn_error)
                except ValueError as e:
                    spawn_error = str(e)
                except Exception as e:
                    spawn_error = f"spawn failed: {e}"
                    if job:
                        self.ledger.fail_job(job.job_id, spawn_error)
            
            decision = DispatchDecision(
                timestamp=_now_iso(),
                action="dispatch" if live and not spawn_error else "would_dispatch" if not live else "dispatch_failed",
                reason=spawn_error if spawn_error else f"Priority score {pr.get('_score', 0):.1f}",
                machine=self.machine,
                live=live,
                pr_number=pr_number,
                pr_url=pr.get("url"),
                repo=repo,
                head_sha=head_sha,
                provider=provider,
                priority_score=pr.get("_score"),
                guard_values=guard_values,
                extra={"job_id": job.job_id if job else None, "pid": pid} if live else None,
            )
            
            self._write_decision(decision)
            decisions.append(decision)
            
            active_lanes[provider] = active_lanes.get(provider, 0) + 1
            dispatched += 1
        
        if dispatched == 0 and candidates:
            decision = DispatchDecision(
                timestamp=_now_iso(),
                action="skip",
                reason="No candidates passed provider filters",
                machine=self.machine,
                live=live,
                guard_values=guard_values,
                extra={"candidate_count": len(candidates)},
            )
            self._write_decision(decision)
            decisions.append(decision)
        elif dispatched == 0:
            decision = DispatchDecision(
                timestamp=_now_iso(),
                action="skip",
                reason="No open non-draft PRs found",
                machine=self.machine,
                live=live,
                guard_values=guard_values,
            )
            self._write_decision(decision)
            decisions.append(decision)
        
        return decisions
    
    def run(self, live: bool = False) -> dict:
        """Run a dispatch cycle.
        
        Args:
            live: If True, actually dispatch work. If False (default), shadow mode.
        
        Returns:
            Summary of the dispatch run.
        """
        decisions = self.evaluate(live=live)
        
        summary = {
            "timestamp": _now_iso(),
            "machine": self.machine,
            "live": live,
            "dispatched": sum(1 for d in decisions if d.action in ("dispatch", "would_dispatch")),
            "skipped": sum(1 for d in decisions if d.action == "skip"),
            "decisions": [d.to_dict() for d in decisions],
        }
        
        self._write_status(summary)
        return summary


def main() -> int:
    """CLI entry point for dispatcher."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="StoryPool lane dispatcher"
    )
    parser.add_argument(
        "--machine",
        default=os.environ.get("STORYPOOL_MACHINE", "unknown"),
        help="Machine name (mini or nucbox)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually dispatch work (default is shadow/dry-run mode)",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Directory for dispatch logs",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output summary as JSON",
    )
    args = parser.parse_args()
    
    dispatcher = Dispatcher(
        machine=args.machine,
        log_dir=args.log_dir,
    )
    
    summary = dispatcher.run(live=args.live)
    
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        mode = "LIVE" if args.live else "SHADOW"
        print(f"[{mode}] Dispatch run at {summary['timestamp']} on {summary['machine']}")
        print(f"  Dispatched: {summary['dispatched']}")
        print(f"  Skipped: {summary['skipped']}")
        print(f"  Log: {dispatcher._log_file()}")
        print(f"  Status: {dispatcher._status_file()}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
