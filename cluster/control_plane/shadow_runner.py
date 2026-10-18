#!/usr/bin/env python3
"""Shadow mode runner for StoryPool control plane migration.

Reads queue/PR state, logs what it WOULD dispatch/gate to a JSONL file,
and takes NO ACTIONS:
- No merges
- No pushes
- No sends (messages)
- No NATS publishes
- No modifications to any external system

This is Phase 1 of the migration: validate that deterministic decisions
match the existing Grok-based system before cutover.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .budget_tracker import BudgetTracker
from .gate_runner import (
    ApprovalChecker,
    CIStatusChecker,
    DraftChecker,
    PRContext,
)
from .guards import MachineGuards, MINI_MAX_LANES, NUCBOX_MAX_LANES
from .state_paths import private_state_dir


SHADOW_LOG_DIR = Path(
    os.environ.get("STORYPOOL_SHADOW_LOG_DIR", "~/.local/share/storypool/shadow")
).expanduser()

# Expand scope only after a repository is authorized for this shadow deployment.
REPOS = ["rupret007/bob-ops-dashboard"]


@dataclass
class ShadowDecision:
    """A logged shadow decision."""

    timestamp: str
    decision_type: str  # "dispatch", "gate", or "run"
    action: str  # "would_dispatch", "would_skip", "would_pass", "would_fail"
    reason: str
    machine: str
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    repo: Optional[str] = None
    head_sha: Optional[str] = None
    lane_provider: Optional[str] = None
    priority_score: Optional[float] = None
    guard_values: Optional[dict] = None
    run_id: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d = {k: v for k, v in d.items() if v is not None}
        return d


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_gh(args: list[str], default: Any = None) -> Any:
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
    prs = _run_gh(["pr", "list", "--repo", repo, "--state", "open", "--json",
                   "number,title,author,headRefOid,isDraft,url,reviewDecision,statusCheckRollup"],
                  default=[])
    return prs if isinstance(prs, list) else []


def _get_pr_details(repo: str, pr_number: int) -> Optional[dict]:
    """Get detailed PR information."""
    pr = _run_gh(["pr", "view", str(pr_number), "--repo", repo, "--json",
                  "number,title,author,headRefOid,isDraft,url,files,additions,deletions,"
                  "reviewDecision,statusCheckRollup,reviews"],
                 default=None)
    return pr if isinstance(pr, dict) else None


def _ci_status_from_pr(pr: dict) -> dict[str, str]:
    """Extract CI status from PR statusCheckRollup."""
    checks = pr.get("statusCheckRollup") or []
    result = {}
    for check in checks:
        if not isinstance(check, dict):
            continue
        name = check.get("name") or check.get("context") or "unknown"
        state = check.get("conclusion") or check.get("state") or check.get("status") or "pending"
        if check.get("status") and check["status"].lower() != "completed":
            state = "pending"
        state = state.lower() if isinstance(state, str) else "pending"
        if state in ("success", "neutral", "skipped"):
            state = "success"
        elif state in ("failure", "error", "timed_out", "cancelled", "action_required"):
            state = "failure"
        elif state in ("pending", "queued", "in_progress", "waiting"):
            state = "pending"
        # Duplicate check names must never let a successful run hide a blocker.
        if name not in result or result[name] == "success" or state == "failure":
            result[name] = state
    return result


def _reviews_from_pr(pr: dict) -> list[dict]:
    """Extract reviews from PR."""
    reviews = pr.get("reviews") or []
    return [{**r, "state": (r.get("state") or "").upper(), "user": r.get("author") or {}}
            for r in reviews if isinstance(r, dict)]


class ShadowRunner:
    """Shadow mode runner that logs decisions without taking action.

    This is the main entry point for Phase 1 validation. It reads the current
    state of PRs and machine health, decides what WOULD be dispatched or gated,
    and logs those decisions to a JSONL file.
    """

    def __init__(
        self,
        machine: str = "unknown",
        log_dir: Optional[Path] = None,
        budget_tracker: Optional[BudgetTracker] = None,
    ):
        self.machine = machine.lower()
        self.log_dir = private_state_dir(log_dir or SHADOW_LOG_DIR)
        self.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.budget = budget_tracker or BudgetTracker(read_only=True)
        self.decisions: list[ShadowDecision] = []
        self.run_id = uuid.uuid4().hex

    def _log_file(self) -> Path:
        """Get the log file for today."""
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.log_dir / f"shadow-{date}.jsonl"

    def _write_decision(self, decision: ShadowDecision) -> None:
        """Append a decision to the log file."""
        decision.run_id = self.run_id
        self.decisions.append(decision)
        log_file = self._log_file()
        private_state_dir(log_file)
        fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(json.dumps(decision.to_dict(), sort_keys=True) + "\n")

    def check_machine_guards(self) -> tuple[bool, str, dict]:
        """Check if this machine can accept work."""
        try:
            guards = MachineGuards(self.machine)
            result = guards.check_all()
            if result.passed and self.machine == "mini":
                result = guards.check_lanes()
                guards.values["lanes"] = result.value
            return result.passed, result.reason, guards.values
        except (OSError, ValueError, TypeError, OverflowError, KeyError, subprocess.SubprocessError):
            return False, "Machine telemetry unavailable", {}

    def _priority_score(self, pr: dict) -> float:
        """Calculate priority score for a PR (higher = more urgent).

        Scoring:
        - Blocked PRs (FAIL gate) get highest priority (need fix)
        - Older PRs get higher priority
        - Smaller diffs get slight priority boost
        - Ready for review gets boost over draft
        """
        score = 0.0

        if pr.get("isDraft"):
            score -= 100.0

        review_decision = pr.get("reviewDecision") or ""
        if review_decision == "APPROVED":
            score += 50.0
        elif review_decision == "CHANGES_REQUESTED":
            score += 75.0

        ci_status = _ci_status_from_pr(pr)
        if any(s == "failure" for s in ci_status.values()):
            score += 80.0

        additions = pr.get("additions") or 0
        deletions = pr.get("deletions") or 0
        diff_size = additions + deletions
        if diff_size < 100:
            score += 10.0
        elif diff_size > 1000:
            score -= 10.0

        return score

    def evaluate_dispatch(self) -> list[ShadowDecision]:
        """Evaluate what lanes WOULD be dispatched.

        Returns a list of decisions (dispatches and skips).
        Does NOT actually dispatch anything.
        """
        decisions = []

        guards_ok, guards_reason, guard_values = self.check_machine_guards()
        if not guards_ok:
            decision = ShadowDecision(
                timestamp=_now_iso(),
                decision_type="dispatch",
                action="would_skip",
                reason=f"Machine guards failed: {guards_reason}",
                machine=self.machine,
                guard_values=guard_values,
            )
            self._write_decision(decision)
            decisions.append(decision)
            return decisions

        for provider in ["codex", "cursor", "claude"]:
            can_use, budget_reason = self.budget.can_use(provider)
            if not can_use:
                decision = ShadowDecision(
                    timestamp=_now_iso(),
                    decision_type="dispatch",
                    action="would_skip",
                    reason=f"Budget cap: {budget_reason}",
                    machine=self.machine,
                    lane_provider=provider,
                    guard_values=guard_values,
                )
                self._write_decision(decision)
                decisions.append(decision)

        candidates = []
        for repo in REPOS:
            prs = _get_open_prs(repo)
            for pr in prs:
                if pr.get("isDraft"):
                    continue
                pr["_repo"] = repo
                pr["_score"] = self._priority_score(pr)
                candidates.append(pr)

        candidates.sort(key=lambda p: -p.get("_score", 0))

        dispatched = 0
        occupied = guard_values.get("lanes")
        if (type(occupied) not in (int, float) or not math.isfinite(occupied)
                or occupied < 0 or int(occupied) != occupied):
            occupied = NUCBOX_MAX_LANES if self.machine == "nucbox" else MINI_MAX_LANES
        capacity = NUCBOX_MAX_LANES if self.machine == "nucbox" else MINI_MAX_LANES
        max_dispatch = min(3, max(0, capacity - int(occupied)))

        for pr in candidates[:10]:
            if dispatched >= max_dispatch:
                break

            can_use, _ = self.budget.can_use("cursor")
            if not can_use:
                break

            decision = ShadowDecision(
                timestamp=_now_iso(),
                decision_type="dispatch",
                action="would_dispatch",
                reason=f"Priority score {pr.get('_score', 0):.1f}",
                machine=self.machine,
                pr_number=pr.get("number"),
                pr_url=pr.get("url"),
                repo=pr.get("_repo"),
                head_sha=pr.get("headRefOid"),
                lane_provider="cursor",
                priority_score=pr.get("_score"),
                guard_values=guard_values,
            )
            self._write_decision(decision)
            decisions.append(decision)
            dispatched += 1

        if dispatched == 0 and candidates:
            decision = ShadowDecision(
                timestamp=_now_iso(),
                decision_type="dispatch",
                action="would_skip",
                reason="No candidates passed filters",
                machine=self.machine,
                guard_values=guard_values,
                extra={"candidate_count": len(candidates)},
            )
            self._write_decision(decision)
            decisions.append(decision)

        return decisions

    def evaluate_gate(self, repo: str, pr_number: int) -> ShadowDecision:
        """Evaluate what gate verdict WOULD be returned.

        Returns a decision (pass, fail, or needs_review).
        Does NOT actually apply any gate or merge.
        """
        pr = _get_pr_details(repo, pr_number)
        if not pr:
            decision = ShadowDecision(
                timestamp=_now_iso(),
                decision_type="gate",
                action="would_fail",
                reason="Could not fetch PR details",
                machine=self.machine,
                pr_number=pr_number,
                repo=repo,
            )
            self._write_decision(decision)
            return decision

        is_draft = pr.get("isDraft", False)
        head_sha = pr.get("headRefOid", "")
        ci_status = _ci_status_from_pr(pr)
        reviews = _reviews_from_pr(pr)

        context = PRContext(
            owner=repo.split("/")[0],
            repo=repo.split("/")[1],
            number=pr_number,
            title=pr.get("title", ""),
            author=(pr.get("author") or {}).get("login", ""),
            head_sha=head_sha,
            base_branch="main",
            is_draft=is_draft,
            diff="(diff not loaded in shadow mode)",
            test_files=[],
            ci_status=ci_status,
            reviews=reviews,
        )

        draft_result = DraftChecker().check(context)
        if draft_result:
            decision = ShadowDecision(
                timestamp=_now_iso(),
                decision_type="gate",
                action="would_fail",
                reason=draft_result.reason,
                machine=self.machine,
                pr_number=pr_number,
                pr_url=pr.get("url"),
                repo=repo,
                head_sha=head_sha,
            )
            self._write_decision(decision)
            return decision

        ci_result = CIStatusChecker().check(context)
        if ci_result:
            decision = ShadowDecision(
                timestamp=_now_iso(),
                decision_type="gate",
                action="would_fail",
                reason=ci_result.reason,
                machine=self.machine,
                pr_number=pr_number,
                pr_url=pr.get("url"),
                repo=repo,
                head_sha=head_sha,
                extra={"ci_status": ci_status},
            )
            self._write_decision(decision)
            return decision

        approval_result = ApprovalChecker().check(context)
        if approval_result:
            decision = ShadowDecision(
                timestamp=_now_iso(),
                decision_type="gate",
                action="would_fail",
                reason=approval_result.reason,
                machine=self.machine,
                pr_number=pr_number,
                pr_url=pr.get("url"),
                repo=repo,
                head_sha=head_sha,
            )
            self._write_decision(decision)
            return decision

        can_use, budget_reason = self.budget.can_use("claude")
        if not can_use:
            decision = ShadowDecision(
                timestamp=_now_iso(),
                decision_type="gate",
                action="would_skip",
                reason=f"Would need LLM but budget cap: {budget_reason}",
                machine=self.machine,
                pr_number=pr_number,
                pr_url=pr.get("url"),
                repo=repo,
                head_sha=head_sha,
            )
            self._write_decision(decision)
            return decision

        decision = ShadowDecision(
            timestamp=_now_iso(),
            decision_type="gate",
            action="would_need_llm",
            reason="Deterministic checks passed; would invoke Claude for diff review",
            machine=self.machine,
            pr_number=pr_number,
            pr_url=pr.get("url"),
            repo=repo,
            head_sha=head_sha,
            extra={"ci_status": ci_status, "review_count": len(reviews)},
        )
        self._write_decision(decision)
        return decision

    def evaluate_all_gates(self) -> list[ShadowDecision]:
        """Evaluate gates for all open non-draft PRs.

        Returns a list of gate decisions.
        """
        decisions = []
        for repo in REPOS:
            prs = _get_open_prs(repo)
            for pr in prs:
                if pr.get("isDraft"):
                    continue
                decision = self.evaluate_gate(repo, pr.get("number"))
                decisions.append(decision)
        return decisions

    def run(self) -> dict:
        """Run a full shadow evaluation cycle.

        Returns a summary of all decisions made.
        """
        self.run_id = uuid.uuid4().hex
        self.decisions = []
        summary = {
            "run_id": self.run_id,
            "timestamp": _now_iso(),
            "machine": self.machine,
            "dispatch_decisions": [],
            "gate_decisions": [],
        }

        dispatch_decisions = self.evaluate_dispatch()
        summary["dispatch_decisions"] = [d.to_dict() for d in dispatch_decisions]

        gate_decisions = self.evaluate_all_gates()
        summary["gate_decisions"] = [d.to_dict() for d in gate_decisions]

        summary["totals"] = {
            "would_dispatch": sum(1 for d in dispatch_decisions if d.action == "would_dispatch"),
            "dispatch_skipped": sum(1 for d in dispatch_decisions if d.action == "would_skip"),
            "gate_pass": sum(1 for d in gate_decisions if d.action == "would_pass"),
            "gate_fail": sum(1 for d in gate_decisions if d.action == "would_fail"),
            "gate_needs_llm": sum(1 for d in gate_decisions if d.action == "would_need_llm"),
            "gate_skipped": sum(1 for d in gate_decisions if d.action == "would_skip"),
        }

        self._write_decision(ShadowDecision(
            timestamp=summary["timestamp"], decision_type="run", action="evaluated",
            reason="Shadow evaluation completed", machine=self.machine,
            extra={"totals": summary["totals"]},
        ))

        return summary


def main() -> int:
    """CLI entry point for shadow runner."""
    import argparse

    parser = argparse.ArgumentParser(
        description="StoryPool shadow mode runner (Phase 1 validation)"
    )
    parser.add_argument(
        "--machine",
        default=os.environ.get("STORYPOOL_MACHINE", "unknown"),
        choices=("mini", "nucbox"),
        help="Machine name (mini or nucbox)",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Directory for shadow decision logs",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output summary as JSON",
    )
    args = parser.parse_args()

    runner = ShadowRunner(
        machine=args.machine,
        log_dir=args.log_dir,
    )

    summary = runner.run()

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Shadow run at {summary['timestamp']} on {summary['machine']}")
        print(f"  Dispatch: {summary['totals']['would_dispatch']} would dispatch, "
              f"{summary['totals']['dispatch_skipped']} skipped")
        print(f"  Gate: {summary['totals']['gate_pass']} pass, "
              f"{summary['totals']['gate_fail']} fail, "
              f"{summary['totals']['gate_needs_llm']} need LLM, "
              f"{summary['totals']['gate_skipped']} skipped")
        print(f"  Log: {runner._log_file()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
