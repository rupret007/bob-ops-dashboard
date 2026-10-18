#!/usr/bin/env python3
"""Fix dispatcher for Karen gate failures.

When Karen FAILs a PR with findings, the conductor dispatches a fix job:
1. Check fix attempt count (max 2 per PR)
2. Classify risk tier (low effort → NucBox Codex, high/security → leave for Jeff)
3. Dispatch fix to local Codex or Cursor
4. Push to PR branch
5. Re-queue with needs-karen label

Never processes never-touch repos.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from ..never_touch import is_never_touch, NEVER_TOUCH_REPOS
from .metrics import MetricsTracker

logger = logging.getLogger(__name__)

_metrics = None  # type: Optional[MetricsTracker]

def _get_metrics() -> MetricsTracker:
    """Get or create global metrics tracker."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsTracker()
    return _metrics

FIX_STATE_DIR = Path(
    os.environ.get("STORYPOOL_FIX_STATE_DIR", "~/.local/share/storypool/fix_state")
).expanduser()

MAX_FIX_ATTEMPTS = 2
MAX_FIX_JOBS_PER_HOUR = 6
HIGH_EFFORT_TIERS = frozenset(["high", "security", "critical"])
CODEX_HOST_NUCBOX = os.environ.get("CODEX_HOST", "nuc")

# Codex binary search paths (same as conductor.py)
CODEX_SEARCH_PATHS = [
    Path.home() / "bin" / "codex",
    Path.home() / ".local" / "bin" / "codex",
    Path("/usr/local/bin/codex"),
    Path("/usr/bin/codex"),
]


@dataclass
class FixAttempt:
    """Record of a fix attempt."""
    timestamp: str
    sha_before: str
    sha_after: Optional[str]
    tool: str  # "codex" or "cursor"
    machine: str
    success: bool
    error: Optional[str] = None
    findings_addressed: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "sha_before": self.sha_before,
            "sha_after": self.sha_after,
            "tool": self.tool,
            "machine": self.machine,
            "success": self.success,
            "error": self.error,
            "findings_addressed": self.findings_addressed,
        }


@dataclass
class FixState:
    """State tracking for fix attempts on a PR."""
    repo: str
    pr_number: int
    attempts: List[FixAttempt] = field(default_factory=list)
    left_for_jeff: bool = False
    left_for_jeff_reason: str = ""
    
    @property
    def attempt_count(self) -> int:
        return len(self.attempts)
    
    @property
    def can_attempt(self) -> bool:
        return self.attempt_count < MAX_FIX_ATTEMPTS and not self.left_for_jeff
    
    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "pr_number": self.pr_number,
            "attempts": [a.to_dict() for a in self.attempts],
            "left_for_jeff": self.left_for_jeff,
            "left_for_jeff_reason": self.left_for_jeff_reason,
        }
    
    @classmethod
    def from_dict(cls, d: dict) -> "FixState":
        attempts = [
            FixAttempt(**a) for a in d.get("attempts", [])
        ]
        return cls(
            repo=d["repo"],
            pr_number=d["pr_number"],
            attempts=attempts,
            left_for_jeff=d.get("left_for_jeff", False),
            left_for_jeff_reason=d.get("left_for_jeff_reason", ""),
        )


def _state_file_path(repo: str, pr_number: int) -> Path:
    """Get path to fix state file."""
    safe_repo = repo.replace("/", "_").replace("\\", "_")
    return FIX_STATE_DIR / f"{safe_repo}_{pr_number}.json"


def load_fix_state(repo: str, pr_number: int) -> FixState:
    """Load fix state for a PR."""
    path = _state_file_path(repo, pr_number)
    if path.is_file():
        try:
            data = json.loads(path.read_text())
            return FixState.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return FixState(repo=repo, pr_number=pr_number)


def save_fix_state(state: FixState) -> None:
    """Save fix state for a PR."""
    FIX_STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _state_file_path(state.repo, state.pr_number)
    path.write_text(json.dumps(state.to_dict(), indent=2))


def extract_findings_from_verdict(verdict: dict) -> List[str]:
    """Extract actionable findings from a Karen verdict."""
    findings = []
    
    for check in verdict.get("check_results", []):
        if check.get("verdict") == "FAIL":
            findings.append(f"[CHECK] {check.get('check', 'unknown')}: {check.get('reason', '')}")
    
    for review in verdict.get("review_results", []):
        if review.get("verdict") == "FAIL":
            findings.append(f"[REVIEW] {review.get('reviewer', 'unknown')}: {review.get('reason', '')}")
    
    return findings


def classify_fix_difficulty(verdict: dict) -> str:
    """Classify fix difficulty based on verdict.
    
    Returns: "low", "high", or "security"
    """
    tier = verdict.get("tier", "").lower()
    
    if tier in ("security", "critical"):
        return "security"
    
    if tier in ("high",):
        return "high"
    
    for check in verdict.get("check_results", []):
        reason = (check.get("reason") or "").lower()
        if any(word in reason for word in ("security", "auth", "secret", "credential", "injection")):
            return "security"
    
    for review in verdict.get("review_results", []):
        reason = (review.get("reason") or "").lower()
        if any(word in reason for word in ("security", "auth", "secret", "credential", "injection")):
            return "security"
    
    return "low"


def should_attempt_fix(repo: str, pr_number: int, verdict: dict) -> tuple:
    # type: (...) -> tuple[bool, str]
    """Check if we should attempt to fix this PR.
    
    Returns: (should_fix, reason)
    """
    if is_never_touch(repo):
        return False, f"repo {repo} is in never-touch list"
    
    state = load_fix_state(repo, pr_number)
    
    if state.left_for_jeff:
        return False, f"already left for Jeff: {state.left_for_jeff_reason}"
    
    if state.attempt_count >= MAX_FIX_ATTEMPTS:
        return False, f"max fix attempts ({MAX_FIX_ATTEMPTS}) reached"
    
    difficulty = classify_fix_difficulty(verdict)
    if difficulty == "security":
        state.left_for_jeff = True
        state.left_for_jeff_reason = "security-related findings require human review"
        save_fix_state(state)
        return False, "security findings - left for Jeff"
    
    if difficulty == "high":
        state.left_for_jeff = True
        state.left_for_jeff_reason = "high-effort findings require human review"
        save_fix_state(state)
        return False, "high-effort findings - left for Jeff"
    
    findings = extract_findings_from_verdict(verdict)
    if not findings:
        return False, "no actionable findings in verdict"
    
    return True, f"can attempt fix ({state.attempt_count + 1}/{MAX_FIX_ATTEMPTS})"


def _find_codex() -> Optional[Path]:
    """Find codex binary in PATH or common locations."""
    for path in CODEX_SEARCH_PATHS:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    # Check PATH next
    try:
        result = subprocess.run(
            ["which", "codex"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            path = Path(result.stdout.strip())
            if path.is_file():
                return path
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    
    # Check common locations
    for path in CODEX_SEARCH_PATHS:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    
    return None


def _get_hourly_fix_count() -> int:
    """Count fix attempts in the last hour across all PRs."""
    if not FIX_STATE_DIR.is_dir():
        return 0
    
    now = datetime.now(timezone.utc)
    count = 0
    
    for path in FIX_STATE_DIR.glob("*.json"):
        try:
            state = FixState.from_dict(json.loads(path.read_text()))
            for attempt in state.attempts:
                try:
                    ts = datetime.fromisoformat(attempt.timestamp.replace("Z", "+00:00"))
                    if (now - ts).total_seconds() < 3600:
                        count += 1
                except (ValueError, TypeError):
                    pass
        except (json.JSONDecodeError, KeyError):
            pass
    
    return count


def _run_codex_fix(
    repo: str,
    pr_number: int,
    findings: List[str],
    dry_run: bool = True,
) -> tuple:
    # type: (...) -> tuple[bool, str, Optional[str]]
    """Run Codex CLI to attempt a fix.
    
    Returns: (success, message, new_sha)
    """
    if dry_run:
        return True, "[DRY-RUN] would run codex fix", None
    
    codex_path = _find_codex()
    if codex_path is None:
        searched = ", ".join(str(p) for p in CODEX_SEARCH_PATHS)
        return False, f"codex CLI not found in PATH or {searched}", None
    
    from .fix_worker import fix_pr
    try:
        sha = _get_pr_head_sha(repo, pr_number)
        if not sha:
            return False, "could not resolve PR head", None
        new_sha = fix_pr(repo, pr_number, sha, findings)
        return True, "codex fix pushed and requeued", new_sha
    except Exception as exc:
        return False, f"codex fix failed: {type(exc).__name__}", None


def _get_pr_head_sha(repo: str, pr_number: int) -> Optional[str]:
    """Get current HEAD SHA for a PR."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json", "headRefOid"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("headRefOid")
    except (subprocess.SubprocessError, json.JSONDecodeError):
        pass
    return None


def _add_needs_karen_label(repo: str, pr_number: int) -> bool:
    """Re-add needs-karen label to PR."""
    try:
        result = subprocess.run(
            ["gh", "pr", "edit", str(pr_number), "--repo", repo, "--add-label", "needs-karen"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except subprocess.SubprocessError:
        return False


def dispatch_fix(
    repo: str,
    pr_number: int,
    verdict: dict,
    dry_run: bool = True,
    machine: str = "nucbox",
) -> dict:
    """Dispatch a fix attempt for a failed PR.
    
    Args:
        repo: Repository in owner/repo format
        pr_number: PR number
        verdict: Karen verdict dict with findings
        dry_run: If True, log but don't execute
        machine: Machine to run fix on
    
    Returns:
        Dict with result status
    """
    result = {
        "repo": repo,
        "pr_number": pr_number,
        "dispatched": False,
        "reason": "",
        "attempt_number": 0,
    }  # type: Dict[str, Any]
    
    # Check hourly rate limit
    hourly_count = _get_hourly_fix_count()
    if hourly_count >= MAX_FIX_JOBS_PER_HOUR:
        result["reason"] = f"hourly fix limit reached ({hourly_count}/{MAX_FIX_JOBS_PER_HOUR})"
        logger.info("Skip fix for %s#%d: %s", repo, pr_number, result["reason"])
        return result
    
    should_fix, reason = should_attempt_fix(repo, pr_number, verdict)
    if not should_fix:
        result["reason"] = reason
        logger.info("Skip fix for %s#%d: %s", repo, pr_number, reason)
        return result
    
    state = load_fix_state(repo, pr_number)
    findings = extract_findings_from_verdict(verdict)
    sha_before = _get_pr_head_sha(repo, pr_number)
    
    result["attempt_number"] = state.attempt_count + 1
    
    logger.info("Dispatching fix for %s#%d (attempt %d/%d)", 
                repo, pr_number, result["attempt_number"], MAX_FIX_ATTEMPTS)
    
    success, message, sha_after = _run_codex_fix(repo, pr_number, findings, dry_run=dry_run)
    
    attempt = FixAttempt(
        timestamp=datetime.now(timezone.utc).isoformat(),
        sha_before=sha_before or "",
        sha_after=sha_after,
        tool="codex",
        machine=machine,
        success=success,
        error=None if success else message,
        findings_addressed=findings if success else [],
    )
    
    state.attempts.append(attempt)
    
    if not success and state.attempt_count >= MAX_FIX_ATTEMPTS:
        state.left_for_jeff = True
        state.left_for_jeff_reason = f"max attempts reached after: {message}"
    
    save_fix_state(state)
    
    result["dispatched"] = success
    result["reason"] = message
    result["sha_before"] = sha_before
    result["sha_after"] = sha_after
    
    if not dry_run:
        _get_metrics().record_fix_attempt(successful=success)
    
    if success and not dry_run:
        if _add_needs_karen_label(repo, pr_number):
            logger.info("Re-queued %s#%d with needs-karen", repo, pr_number)
            result["requeued"] = True
        else:
            logger.warning("Failed to re-queue %s#%d", repo, pr_number)
            result["requeued"] = False
    
    return result


def get_pending_fixes() -> List[dict]:
    """Get list of PRs waiting for fix attempts."""
    pending = []
    
    if not FIX_STATE_DIR.is_dir():
        return pending
    
    for path in FIX_STATE_DIR.glob("*.json"):
        try:
            state = FixState.from_dict(json.loads(path.read_text()))
            if state.can_attempt:
                pending.append({
                    "repo": state.repo,
                    "pr_number": state.pr_number,
                    "attempts": state.attempt_count,
                })
        except (json.JSONDecodeError, KeyError):
            pass
    
    return pending


def get_prs_left_for_jeff() -> List[dict]:
    """Get list of PRs that have been left for Jeff."""
    left = []
    
    if not FIX_STATE_DIR.is_dir():
        return left
    
    for path in FIX_STATE_DIR.glob("*.json"):
        try:
            state = FixState.from_dict(json.loads(path.read_text()))
            if state.left_for_jeff:
                left.append({
                    "repo": state.repo,
                    "pr_number": state.pr_number,
                    "reason": state.left_for_jeff_reason,
                    "attempts": state.attempt_count,
                })
        except (json.JSONDecodeError, KeyError):
            pass
    
    return left
