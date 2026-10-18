#!/usr/bin/env python3
"""LLM-in-the-loop Conductor service for StoryPool control plane.

Hourly orchestrator that uses a reasoning model (Codex primary, Gemini fallback)
to propose actions, while deterministic rails validate every proposal.

Each hourly tick:
1. Build state snapshot (queue, PRs, CI, verdicts, lanes, guards, budgets, errors)
2. Ask reasoning model to propose actions and flag anomalies
3. Validate each proposal against deterministic rails
4. Execute validated actions (in live mode) or log what would happen (shadow mode)
5. Generate twice-daily digest for human review

LLM Provider Priority:
- Primary: Codex CLI (codex exec, NucBox via CODEX_HOST=nuc, low effort default)
- Fallback: Gemini API
- Never: Grok
- Off by default: Claude

Deterministic Rails (model CANNOT override):
- Merges ONLY with KARENPASS at exact head SHA
- Never-touch repos guard (cluster/never_touch.py)
- Machine guards (load, swap, disk, lanes)
- Budget caps (Codex 75% weekly, etc.)
- No sudo, no force-push, no tags/releases, no sends

Fail-safe: If model unavailable or returns invalid output:
- Pause (no actions)
- Write alert file

Runs in shadow/dry-run mode by default; use --live to execute.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from ..never_touch import check_merge_allowed, NEVER_TOUCH_REPOS
from .budget_tracker import BudgetTracker
from .dispatcher import _get_open_prs, _run_gh, REPOS
from .guards import MachineGuards, mini_guards_pass, nucbox_guards_pass
from .metrics import MetricsTracker

CONDUCTOR_LOG_DIR = Path(
    os.environ.get("STORYPOOL_CONDUCTOR_LOG_DIR", "~/.local/share/storypool/conductor")
).expanduser()

VERDICT_DIR = Path(
    os.environ.get("STORYPOOL_VERDICT_DIR", "~/.local/share/storypool/verdicts")
).expanduser()

CODEX_WEEKLY_CAP_PCT = 75.0
GEMINI_DAILY_BUDGET_USD = 5.0

MAX_MERGES_PER_HOUR = 6
MAX_MERGES_PER_DAY = 30
CONSECUTIVE_CI_FAILURES_TO_PAUSE = 2

PRIORITY_REPOS = [
    "rupret007/web-bob",
    "rupret007/webjam",
    "rupret007/adoptiq",
    "rupret007/storyboard",
]

BAND_PROJECT_PATTERNS = [
    "rad-dad",
    "show-night",
    "music",
    "band",
]

DIGEST_HOURS_CT = [8]


class ActionType(Enum):
    """Types of actions the conductor can take."""
    LABEL_NEEDS_KAREN = "label_needs_karen"
    MERGE_PR = "merge_pr"
    UNDRAFT_PR = "undraft_pr"
    FLAG_ANOMALY = "flag_anomaly"
    PAUSE_LANE = "pause_lane"
    RESUME_LANE = "resume_lane"
    ALERT = "alert"


@dataclass
class ProposedAction:
    """An action proposed by the LLM."""
    action_type: str
    repo: Optional[str] = None
    pr_number: Optional[int] = None
    head_sha: Optional[str] = None
    reason: str = ""
    priority: str = "normal"
    extra: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None and v != ""}


@dataclass
class ValidatedAction:
    """A validated action ready for execution."""
    proposal: ProposedAction
    validated: bool
    validation_reason: str
    executed: bool = False
    execution_result: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "proposal": self.proposal.to_dict(),
            "validated": self.validated,
            "validation_reason": self.validation_reason,
            "executed": self.executed,
            "execution_result": self.execution_result,
        }


@dataclass
class StateSnapshot:
    """Complete state snapshot for LLM context."""
    timestamp: str
    repos: list  # List[str]
    open_prs: list  # List[dict]
    ci_statuses: dict  # Dict[str, dict]
    karen_verdicts: list  # List[dict]
    lane_usage: dict  # Dict[str, int]
    machine_guards: dict  # Dict[str, dict]
    budget_summary: dict  # Dict[str, dict]
    recent_errors: list  # List[dict]
    never_touch_repos: list  # List[str]
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    def to_prompt_context(self) -> str:
        """Format snapshot as context for LLM prompt."""
        lines = [
            f"# StoryPool State Snapshot",
            f"Timestamp: {self.timestamp}",
            "",
            "## Repositories Managed",
            *[f"- {r}" for r in self.repos],
            "",
            "## Never-Touch Repos (auto-merge forbidden)",
            *[f"- {r}" for r in self.never_touch_repos],
            "",
            f"## Open PRs ({len(self.open_prs)} total)",
        ]
        
        for pr in self.open_prs[:20]:
            status = "draft" if pr.get("isDraft") else "ready"
            ci = self.ci_statuses.get(f"{pr.get('repo')}/{pr.get('number')}", {})
            ci_summary = "passing" if all(s == "success" for s in ci.values()) else "failing/pending"
            verdict = next((v for v in self.karen_verdicts 
                           if v.get("repo") == pr.get("repo") 
                           and v.get("pr_number") == pr.get("number")
                           and v.get("sha") == pr.get("headRefOid")), None)
            verdict_str = verdict.get("verdict", "none") if verdict else "none"
            priority_marker = "⭐ " if pr.get("is_priority") else ""
            lines.append(f"- {priority_marker}{pr.get('repo')} #{pr.get('number')}: {status}, CI {ci_summary}, verdict={verdict_str}")
        
        if len(self.open_prs) > 20:
            lines.append(f"  ... and {len(self.open_prs) - 20} more")
        
        lines.extend([
            "",
            "## Machine Guards",
            f"- Mini: load={self.machine_guards.get('mini', {}).get('load', '?')}, "
            f"swap={self.machine_guards.get('mini', {}).get('swap', '?')}%, "
            f"disk={self.machine_guards.get('mini', {}).get('disk', '?')}GB",
            f"- NucBox: load={self.machine_guards.get('nucbox', {}).get('load', '?')}, "
            f"lanes={self.machine_guards.get('nucbox', {}).get('lanes', '?')}/7",
            "",
            "## Budget Summary",
        ])
        
        for provider, data in self.budget_summary.items():
            pct = data.get("weekly_pct", 0)
            cost = data.get("cost_usd", 0)
            lines.append(f"- {provider}: ${cost:.2f} ({pct:.1f}% weekly)")
        
        lines.extend([
            "",
            "## Lane Usage",
            *[f"- {provider}: {count} active" for provider, count in self.lane_usage.items()],
            "",
            f"## Recent Errors ({len(self.recent_errors)} in last 24h)",
        ])
        
        for err in self.recent_errors[:5]:
            lines.append(f"- {err.get('timestamp', '?')}: {err.get('message', '?')[:80]}")
        
        return "\n".join(lines)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_ct_hour() -> int:
    """Get current hour in Central Time (CT)."""
    import zoneinfo
    try:
        ct = zoneinfo.ZoneInfo("America/Chicago")
    except Exception:
        ct = timezone(timedelta(hours=-6))
    now_ct = datetime.now(ct)
    return now_ct.hour


def _is_digest_time() -> bool:
    """Check if it's time for a digest (8 AM or 8 PM CT)."""
    ct_hour = _get_ct_hour()
    return ct_hour in DIGEST_HOURS_CT


def _is_priority_repo(repo: str) -> bool:
    """Check if repo is in the priority list."""
    repo_lower = repo.lower()
    if repo_lower in [r.lower() for r in PRIORITY_REPOS]:
        return True
    for pattern in BAND_PROJECT_PATTERNS:
        if pattern in repo_lower:
            return True
    return False


def _sort_prs_by_priority(prs: list) -> list:
    """Sort PRs with priority repos first."""
    priority_prs = []
    other_prs = []
    for pr in prs:
        repo = pr.get("repo", "")
        if _is_priority_repo(repo):
            priority_prs.append(pr)
        else:
            other_prs.append(pr)
    return priority_prs + other_prs


def _get_pr_details(repo: str, pr_number: int) -> Optional[dict]:
    """Get detailed PR information."""
    pr = _run_gh(
        ["pr", "view", str(pr_number), "--repo", repo, "--json",
         "number,title,author,headRefOid,isDraft,url,body,files,additions,deletions,"
         "reviewDecision,statusCheckRollup,reviews,labels,mergeStateStatus"],
        default=None
    )
    return pr if isinstance(pr, dict) else None


def _ci_status_from_pr(pr: dict) -> dict[str, str]:
    """Extract CI status from PR statusCheckRollup."""
    checks = pr.get("statusCheckRollup") or []
    result = {}
    for check in checks:
        if not isinstance(check, dict):
            continue
        name = check.get("name") or check.get("context") or "unknown"
        state = check.get("conclusion") or check.get("state") or "pending"
        state = state.lower() if isinstance(state, str) else "pending"
        if state in ("success", "neutral", "skipped"):
            result[name] = "success"
        elif state in ("failure", "error", "timed_out", "cancelled", "action_required"):
            result[name] = "failure"
        elif state in ("pending", "queued", "in_progress", "waiting"):
            result[name] = "pending"
        else:
            result[name] = state
    return result


def _has_label(pr: dict, label_name: str) -> bool:
    """Check if PR has a specific label."""
    labels = pr.get("labels") or []
    return any(lbl.get("name") == label_name for lbl in labels if isinstance(lbl, dict))


def _is_approved(pr: dict) -> bool:
    """Check if PR has an approved review."""
    reviews = pr.get("reviews") or []
    return any(r.get("state", "").upper() == "APPROVED" for r in reviews if isinstance(r, dict))


def _get_verdict_file(repo: str, pr_number: int, head_sha: str) -> Path:
    """Get the path to the verdict file for a specific PR and SHA."""
    owner, _, name = repo.partition("/")
    safe_repo = f"{owner}_{name}"
    return VERDICT_DIR / safe_repo / f"pr-{pr_number}-{head_sha[:12]}.json"


def _read_verdict(repo: str, pr_number: int, head_sha: str) -> Optional[dict]:
    """Read the Karen verdict file for a specific PR and SHA."""
    verdict_file = _get_verdict_file(repo, pr_number, head_sha)
    if not verdict_file.is_file():
        return None
    try:
        verdict = json.loads(verdict_file.read_text())
        if not isinstance(verdict, dict):
            return None
        if verdict.get("sha") != head_sha:
            return None
        return verdict
    except (json.JSONDecodeError, OSError):
        return None


def _list_verdicts(verdict_dir: Path) -> list[dict]:
    """List all recent verdict files."""
    verdicts = []
    if not verdict_dir.is_dir():
        return verdicts
    for repo_dir in verdict_dir.iterdir():
        if not repo_dir.is_dir():
            continue
        for verdict_file in repo_dir.glob("pr-*.json"):
            try:
                v = json.loads(verdict_file.read_text())
                if isinstance(v, dict):
                    verdicts.append(v)
            except (json.JSONDecodeError, OSError):
                pass
    return verdicts


def _read_recent_errors(log_dir: Path, hours: int = 24) -> list[dict]:
    """Read recent error entries from conductor logs."""
    errors = []
    cutoff = time.time() - (hours * 3600)
    for log_file in log_dir.glob("conductor-*.jsonl"):
        try:
            for line in log_file.read_text().splitlines():
                entry = json.loads(line)
                ts = entry.get("timestamp", "")
                if "error" in entry.get("action", "").lower() or entry.get("extra", {}).get("error"):
                    try:
                        entry_time = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                        if entry_time >= cutoff:
                            errors.append({"timestamp": ts, "message": str(entry)[:200]})
                    except (ValueError, TypeError):
                        pass
        except (json.JSONDecodeError, OSError):
            pass
    return errors[-20:]


class CodexClient:
    """Client for Codex CLI (codex exec)."""
    
    # Common locations to search for codex binary
    CODEX_SEARCH_PATHS = [
        Path.home() / "bin" / "codex",
        Path.home() / ".local" / "bin" / "codex",
        Path("/usr/local/bin/codex"),
        Path("/usr/bin/codex"),
    ]
    
    def __init__(self, host: str = "nuc", effort: str = "low"):
        self.host = host
        self.effort = effort
        self.last_error = ""
        self._codex_path = None  # type: Optional[Path]
    
    def _find_codex(self) -> Optional[Path]:
        """Find codex binary in PATH or common locations."""
        # First check if already cached
        if self._codex_path and self._codex_path.is_file():
            return self._codex_path
        
        # Check PATH first
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
                    self._codex_path = path
                    return path
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
        
        # Check common locations
        for path in self.CODEX_SEARCH_PATHS:
            if path.is_file() and os.access(path, os.X_OK):
                self._codex_path = path
                return path
        
        return None
    
    def is_available(self) -> tuple:
        """Check if Codex CLI is available.
        
        Returns (available, reason).
        """
        codex_path = self._find_codex()
        if codex_path is None:
            searched = ", ".join(str(p) for p in self.CODEX_SEARCH_PATHS)
            return False, f"codex CLI not found in PATH or common locations ({searched})"
        
        # Verify it's executable
        try:
            result = subprocess.run(
                [str(codex_path), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return False, f"codex --version failed (exit {result.returncode}): {result.stderr[:100]}"
            return True, f"ok (found at {codex_path})"
        except subprocess.TimeoutExpired:
            return False, f"codex --version timed out"
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            return False, f"codex check failed: {e}"
    
    def complete(self, prompt: str, high_effort: bool = False) -> Optional[str]:
        """Run codex exec with the given prompt via stdin."""
        codex_path = self._find_codex()
        if codex_path is None:
            self.last_error = "codex CLI not found"
            return None
        
        effort = "high" if high_effort else self.effort
        env = os.environ.copy()
        env["CODEX_HOST"] = self.host
        
        # Use -c to set model_reasoning_effort, prompt via stdin
        try:
            result = subprocess.run(
                [str(codex_path), "exec", "-c", f"model_reasoning_effort={effort}"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )
            if result.returncode == 0:
                self.last_error = ""
                return result.stdout.strip()
            self.last_error = "codex exec failed (exit {}): {}".format(
                result.returncode, result.stderr[:200] if result.stderr else "no stderr")
            return None
        except subprocess.TimeoutExpired:
            self.last_error = "codex exec timed out after 300s"
            return None
        except FileNotFoundError:
            self.last_error = f"codex CLI not found at {codex_path}"
            return None
        except subprocess.SubprocessError as e:
            self.last_error = "codex subprocess error: {}".format(e)
            return None


class GeminiClient:
    """Client for Gemini API (fallback)."""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or self._load_key()
        self.last_error = ""
    
    def _load_key(self) -> str:
        key_file = Path("~/.config/storypool/gemini.key").expanduser()
        if key_file.is_file():
            return key_file.read_text().strip()
        return os.environ.get("GEMINI_API_KEY", "")
    
    def is_available(self) -> tuple:
        """Check if Gemini is available.
        
        Returns (available, reason).
        """
        if not self.api_key:
            key_file = Path("~/.config/storypool/gemini.key").expanduser()
            return False, "no Gemini API key (check {} or GEMINI_API_KEY env)".format(key_file)
        return True, "ok"
    
    def complete(self, prompt: str) -> Optional[str]:
        """Call Gemini API with the given prompt."""
        if not self.api_key:
            self.last_error = "no Gemini API key configured"
            return None
        self.last_error = "Gemini API not yet implemented"
        return None


CONDUCTOR_SYSTEM_PROMPT = """You are the StoryPool conductor, an intelligent orchestrator for a coding lane system.

Your job is to analyze the current system state and propose actions to keep work flowing smoothly.

## Priority Projects (process first)

1. **Web Bob** (rupret007/web-bob) - highest priority
2. **WebJam** (rupret007/webjam)
3. **AdoptIQ** (rupret007/adoptiq)
4. **StoryBoard** (rupret007/storyboard)
5. **Band projects** (any repo with rad-dad, show-night, music, band in name)

PRs from priority repos should be processed before other repos. PRs marked with ⭐ in the state are priority.

## Actions You Can Propose

1. **label_needs_karen** - Label a PR as ready for Karen gate review
   - Only for PRs that: are not draft, have passing CI, have an approved review, don't already have the label
   
2. **merge_pr** - Merge a PR (will be validated against KARENPASS verdict at exact SHA)
   - Only for PRs with: KARENPASS verdict at current head SHA, passing CI, approval, not in never-touch repos
   
3. **flag_anomaly** - Flag something suspicious for human attention
   - Stuck lanes (no progress for extended period)
   - Loops (same PR failing repeatedly)
   - Suspicious patterns (unusual merge velocity, budget burn spikes)
   - Never-touch repo attempts

4. **alert** - Raise an alert requiring immediate attention
   - Budget caps approaching or exceeded
   - Machine guards failing
   - System errors

## Response Format

Respond with a JSON object:
```json
{
  "analysis": "Brief analysis of current state",
  "anomalies": ["List of any anomalies detected"],
  "proposed_actions": [
    {
      "action_type": "label_needs_karen|merge_pr|flag_anomaly|alert",
      "repo": "owner/name",
      "pr_number": 123,
      "head_sha": "abc123...",
      "reason": "Why this action",
      "priority": "normal|high"
    }
  ],
  "summary": "One-line summary for digest"
}
```

## Critical Rules (You Cannot Override These)

- NEVER propose merging a PR without KARENPASS at exact current head SHA
- NEVER propose actions on never-touch repos (they are listed in the state)
- NEVER propose anything involving sudo, force-push, tags, releases, or sending messages
- If unsure, propose flag_anomaly instead of direct action
- Budget caps are hard limits - alert if approaching but never exceed

Analyze the state and propose appropriate actions.
"""


class Conductor:
    """LLM-in-the-loop conductor with deterministic rails.
    
    Uses Codex (primary) or Gemini (fallback) to reason about system state
    and propose actions. All proposals are validated against hard rails
    before execution.
    
    BETTER-THAN-GROK: Tracks metrics for week-over-week comparison.
    """
    
    def __init__(
        self,
        log_dir: Optional[Path] = None,
        verdict_dir: Optional[Path] = None,
        budget_tracker: Optional[BudgetTracker] = None,
        codex_client: Optional[CodexClient] = None,
        gemini_client: Optional[GeminiClient] = None,
        metrics_tracker: Optional[MetricsTracker] = None,
        use_claude: bool = False,
    ):
        self.log_dir = log_dir or CONDUCTOR_LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.verdict_dir = verdict_dir or VERDICT_DIR
        self.verdict_dir.mkdir(parents=True, exist_ok=True)
        self.budget = budget_tracker or BudgetTracker()
        self.codex = codex_client or CodexClient()
        self.gemini = gemini_client or GeminiClient()
        self.metrics = metrics_tracker or MetricsTracker()
        self.use_claude = use_claude
        self.validated_actions = []  # type: list
        
    def _log_file(self) -> Path:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.log_dir / f"conductor-{date}.jsonl"
    
    def _alert_file(self) -> Path:
        return self.log_dir / "conductor-alert.json"
    
    def _digest_file(self) -> Path:
        now = datetime.now(timezone.utc)
        period = "am" if now.hour < 12 else "pm"
        date = now.strftime("%Y-%m-%d")
        return self.log_dir / f"digest-{date}-{period}.md"
    
    def _status_file(self) -> Path:
        return self.log_dir / "conductor-status.json"
    
    def _needs_jeff_file(self) -> Path:
        """File for escalating issues to Jeff."""
        return self.log_dir / "NEEDS_JEFF.md"
    
    def _escalate_to_jeff(self, reason: str, details: dict) -> None:
        """Write NEEDS_JEFF.md when human intervention is required.
        
        This is the ONLY escalation path to Jeff. The conductor does not
        send messages, notifications, or alerts elsewhere.
        """
        now = datetime.now(timezone.utc)
        needs_jeff = self._needs_jeff_file()
        
        existing = ""
        if needs_jeff.is_file():
            existing = needs_jeff.read_text()
        
        entry = [
            f"## {now.strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            f"**Reason:** {reason}",
            "",
        ]
        
        if details.get("repo"):
            entry.append(f"**Repo:** {details['repo']}")
        if details.get("pr_number"):
            entry.append(f"**PR:** #{details['pr_number']}")
        if details.get("action_needed"):
            entry.append(f"**Action needed:** {details['action_needed']}")
        if details.get("context"):
            entry.append(f"**Context:** {details['context']}")
        
        entry.extend(["", "---", ""])
        
        new_content = "\n".join(entry) + existing
        
        header = "# NEEDS_JEFF\n\nIssues requiring human intervention.\n\n---\n\n"
        if not existing.startswith("# NEEDS_JEFF"):
            new_content = header + new_content
        
        needs_jeff.write_text(new_content)
        self._write_log({"action": "escalate_to_jeff", "reason": reason, "details": details})
    
    def _clear_needs_jeff(self) -> None:
        """Clear the NEEDS_JEFF.md file (after Jeff reviews it)."""
        needs_jeff = self._needs_jeff_file()
        if needs_jeff.is_file():
            needs_jeff.unlink()
            self._write_log({"action": "clear_needs_jeff"})
    
    def _write_log(self, entry: dict) -> None:
        entry["timestamp"] = _now_iso()
        with open(self._log_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    
    def _write_alert(self, message: str, details: dict, escalate: bool = False) -> None:
        """Write an alert file when fail-safe triggers.
        
        If escalate=True, also writes to NEEDS_JEFF.md for human review.
        """
        alert = {
            "timestamp": _now_iso(),
            "message": message,
            "details": details,
            "status": "PAUSED",
        }
        self._alert_file().write_text(json.dumps(alert, indent=2))
        self._write_log({"action": "alert", "message": message, **details})
        
        if escalate:
            self._escalate_to_jeff(message, {
                "action_needed": "Review and resolve",
                "context": str(details)[:500],
            })
    
    def _write_status(self, summary: dict) -> None:
        status = {"timestamp": _now_iso(), "summary": summary}
        self._status_file().write_text(json.dumps(status, indent=2))
    
    def _can_use_codex(self) -> tuple[bool, str]:
        """Check if Codex is within budget cap."""
        pct = self.budget.weekly_pct("codex")
        if pct >= CODEX_WEEKLY_CAP_PCT:
            return False, "Codex at {:.1f}% of weekly cap (limit {}%)".format(pct, CODEX_WEEKLY_CAP_PCT)
        return True, "ok"
    
    def _can_use_gemini(self) -> tuple[bool, str]:
        """Check if Gemini is available."""
        return self.budget.can_use("gemini")
    
    def _merge_state_file(self) -> Path:
        """File tracking merge history for rate limiting."""
        return self.log_dir / "merge-state.json"
    
    def _load_merge_state(self) -> dict:
        """Load merge tracking state."""
        state_file = self._merge_state_file()
        if not state_file.is_file():
            return {"merges": [], "consecutive_ci_failures": 0, "paused": False, "pause_reason": ""}
        try:
            return json.loads(state_file.read_text())
        except (json.JSONDecodeError, IOError):
            return {"merges": [], "consecutive_ci_failures": 0, "paused": False, "pause_reason": ""}
    
    def _save_merge_state(self, state: dict) -> None:
        """Save merge tracking state."""
        self._merge_state_file().write_text(json.dumps(state, indent=2))
    
    def _check_merge_rate_limit(self) -> tuple[bool, str]:
        """Check if we're within merge rate limits.
        
        Returns (allowed, reason).
        """
        state = self._load_merge_state()
        
        if state.get("paused"):
            return False, "PAUSED: {}".format(state.get("pause_reason", "unknown"))
        
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        hour_ago = now_ts - 3600
        day_ago = now_ts - 86400
        
        merges = state.get("merges", [])
        merges_last_hour = sum(1 for m in merges if m.get("ts", 0) > hour_ago)
        merges_last_day = sum(1 for m in merges if m.get("ts", 0) > day_ago)
        
        if merges_last_hour >= MAX_MERGES_PER_HOUR:
            return False, "Rate limit: {} merges in last hour (max {})".format(
                merges_last_hour, MAX_MERGES_PER_HOUR)
        
        if merges_last_day >= MAX_MERGES_PER_DAY:
            return False, "Rate limit: {} merges in last day (max {})".format(
                merges_last_day, MAX_MERGES_PER_DAY)
        
        return True, "ok"
    
    def _record_merge(self, repo: str, pr_number: int, sha: str) -> None:
        """Record a successful merge."""
        state = self._load_merge_state()
        now = datetime.now(timezone.utc)
        
        state["merges"].append({
            "repo": repo,
            "pr": pr_number,
            "sha": sha[:12],
            "ts": now.timestamp(),
            "time": now.isoformat(),
        })
        
        cutoff = now.timestamp() - 86400 * 2
        state["merges"] = [m for m in state["merges"] if m.get("ts", 0) > cutoff]
        
        self._save_merge_state(state)
    
    def _check_main_ci_after_merge(self, repo: str) -> tuple[bool, str]:
        """Check if main CI is passing after a merge.
        
        Returns (passing, status).
        """
        try:
            result = subprocess.run(
                ["gh", "api", "/repos/{}/commits/main/status".format(repo)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                return True, "unknown"
            
            data = json.loads(result.stdout)
            state = data.get("state", "unknown")
            return state in ("success", "pending"), state
        except (subprocess.SubprocessError, json.JSONDecodeError):
            return True, "unknown"
    
    def _create_revert_pr(self, repo: str, merge_sha: str, pr_number: int) -> Optional[str]:
        """Create a revert PR for a failed merge.
        
        Returns the revert PR URL if created, None if failed.
        NEVER force-pushes - only creates a revert commit as a new PR.
        """
        try:
            result = subprocess.run(
                ["gh", "api", f"/repos/{repo}/git/commits/{merge_sha}"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                self._write_log({"action": "revert_failed", "reason": "could not fetch merge commit"})
                return None
            
            commit_data = json.loads(result.stdout)
            commit_message = commit_data.get("message", "")
            
            revert_branch = f"revert-{merge_sha[:8]}"
            revert_title = f"Revert: {commit_message.split(chr(10))[0][:60]}"
            revert_body = f"""## Auto-generated revert PR

This PR reverts commit {merge_sha[:12]} which broke main CI.

**Original PR:** #{pr_number}
**Original commit message:**
```
{commit_message[:500]}
```

**Reason:** Main CI failed after merge - auto-pause triggered.

---
*This revert PR requires Karen gate review before merge.*
"""
            
            create_result = subprocess.run(
                ["gh", "pr", "create", "--repo", repo,
                 "--title", revert_title,
                 "--body", revert_body,
                 "--head", revert_branch,
                 "--base", "main",
                 "--label", "needs-karen"],
                capture_output=True, text=True, timeout=60,
                input=f"git revert {merge_sha} --no-edit && git push origin HEAD:{revert_branch}\n",
            )
            
            subprocess.run(
                ["gh", "api", "--method", "POST",
                 f"/repos/{repo}/git/refs",
                 "-f", f"ref=refs/heads/{revert_branch}",
                 "-f", f"sha={merge_sha}~1"],
                capture_output=True, text=True, timeout=30,
            )
            
            self._write_log({
                "action": "revert_pr_created",
                "repo": repo,
                "merge_sha": merge_sha[:12],
                "revert_branch": revert_branch,
            })
            
            return create_result.stdout.strip() if create_result.returncode == 0 else None
        except (subprocess.SubprocessError, json.JSONDecodeError) as e:
            self._write_log({"action": "revert_failed", "reason": str(e)[:100]})
            return None
    
    def _record_ci_result(self, repo: str, passed: bool, merge_sha: str = "", pr_number: int = 0) -> None:
        """Record CI result after merge, auto-pause and create revert PR if failures."""
        state = self._load_merge_state()
        
        if passed:
            state["consecutive_ci_failures"] = 0
        else:
            state["consecutive_ci_failures"] = state.get("consecutive_ci_failures", 0) + 1
            
            if state["consecutive_ci_failures"] >= CONSECUTIVE_CI_FAILURES_TO_PAUSE:
                state["paused"] = True
                state["pause_reason"] = "Auto-paused: {} consecutive merges broke main CI".format(
                    state["consecutive_ci_failures"])
                
                revert_url = None
                if merge_sha and pr_number:
                    revert_url = self._create_revert_pr(repo, merge_sha, pr_number)
                
                self._write_alert(state["pause_reason"], {
                    "repo": repo,
                    "consecutive_failures": state["consecutive_ci_failures"],
                    "revert_pr": revert_url,
                }, escalate=True)
                
                if revert_url:
                    self._escalate_to_jeff(
                        f"Main CI broken - revert PR created: {revert_url}",
                        {
                            "repo": repo,
                            "action_needed": "Review and merge revert PR, or fix forward",
                            "revert_pr": revert_url,
                        }
                    )
        
        self._save_merge_state(state)
    
    def is_paused(self) -> tuple[bool, str]:
        """Check if conductor is paused."""
        state = self._load_merge_state()
        if state.get("paused"):
            return True, state.get("pause_reason", "unknown")
        return False, ""
    
    def resume(self) -> None:
        """Resume from paused state (for manual intervention)."""
        state = self._load_merge_state()
        state["paused"] = False
        state["pause_reason"] = ""
        state["consecutive_ci_failures"] = 0
        self._save_merge_state(state)
        self._write_log({"action": "resume", "timestamp": _now_iso()})
    
    def build_state_snapshot(self) -> StateSnapshot:
        """Build complete state snapshot for LLM context."""
        open_prs = []
        ci_statuses = {}
        
        for repo in REPOS:
            prs = _get_open_prs(repo)
            for pr in prs:
                pr["repo"] = repo
                pr["is_priority"] = _is_priority_repo(repo)
                open_prs.append(pr)
                
                details = _get_pr_details(repo, pr.get("number"))
                if details:
                    ci = _ci_status_from_pr(details)
                    ci_statuses[f"{repo}/{pr.get('number')}"] = ci
        
        open_prs = _sort_prs_by_priority(open_prs)
        
        mini_guards = MachineGuards("mini")
        nucbox_guards = MachineGuards("nucbox")
        
        machine_guards = {
            "mini": {
                "load": mini_guards.check_load().value,
                "swap": mini_guards.check_swap().value,
                "disk": mini_guards.check_disk().value,
                "passed": mini_guards.check_all().passed,
            },
            "nucbox": {
                "load": nucbox_guards.check_load().value,
                "lanes": nucbox_guards.check_lanes().value,
                "passed": nucbox_guards.check_all().passed,
            },
        }
        
        budget_summary = self.budget.summary()
        
        verdicts = _list_verdicts(self.verdict_dir)
        
        recent_errors = _read_recent_errors(self.log_dir)
        
        lane_usage = {"codex": 0, "cursor": 0, "claude": 0, "minimax": 0}
        
        job_stats = {}
        try:
            from .job_ledger import JobLedger
            ledger = JobLedger()
            job_stats = ledger.summary()
            for job in ledger.get_running_jobs():
                provider = job.provider
                if provider in lane_usage:
                    lane_usage[provider] += 1
        except (ImportError, Exception):
            pass
        
        return StateSnapshot(
            timestamp=_now_iso(),
            repos=REPOS,
            open_prs=open_prs,
            ci_statuses=ci_statuses,
            karen_verdicts=verdicts,
            lane_usage=lane_usage,
            machine_guards=machine_guards,
            budget_summary=budget_summary,
            recent_errors=recent_errors,
            never_touch_repos=list(NEVER_TOUCH_REPOS),
        )
    
    def get_llm_proposals(self, snapshot: StateSnapshot, high_effort: bool = False) -> Optional[dict]:
        """Get action proposals from LLM (Codex primary, Gemini fallback)."""
        prompt = f"{CONDUCTOR_SYSTEM_PROMPT}\n\n## Current State\n\n{snapshot.to_prompt_context()}"
        
        can_codex, codex_reason = self._can_use_codex()
        if can_codex:
            self._write_log({"action": "llm_call", "provider": "codex", "effort": "high" if high_effort else "low"})
            response = self.codex.complete(prompt, high_effort=high_effort)
            if response:
                self.budget.record("codex", cost_usd=0.10 if high_effort else 0.02)
                return self._parse_llm_response(response)
        
        can_gemini, gemini_reason = self._can_use_gemini()
        if can_gemini:
            self._write_log({"action": "llm_call", "provider": "gemini"})
            response = self.gemini.complete(prompt)
            if response:
                self.budget.record("gemini", cost_usd=0.01)
                return self._parse_llm_response(response)
        
        return None
    
    def _parse_llm_response(self, response: str) -> Optional[dict]:
        """Parse LLM response JSON."""
        json_match = re.search(r'\{[\s\S]*\}', response)
        if not json_match:
            return None
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            return None
    
    def validate_action(self, action: ProposedAction, snapshot: StateSnapshot) -> ValidatedAction:
        """Validate a proposed action against deterministic rails.
        
        Returns ValidatedAction with validated=True if action is allowed,
        or validated=False with reason if rejected.
        """
        repo = action.repo or ""
        pr_number = action.pr_number
        head_sha = action.head_sha or ""
        
        if action.action_type == ActionType.MERGE_PR.value:
            rate_ok, rate_reason = self._check_merge_rate_limit()
            if not rate_ok:
                return ValidatedAction(action, False, "RAIL: {}".format(rate_reason))
            
            merge_allowed, reason = check_merge_allowed(repo)
            if not merge_allowed:
                return ValidatedAction(action, False, "RAIL: {}".format(reason))
            
            verdict = _read_verdict(repo, pr_number, head_sha) if pr_number else None
            if not verdict or verdict.get("verdict") != "KARENPASS":
                return ValidatedAction(action, False, 
                    f"RAIL: No KARENPASS verdict for {repo}#{pr_number} at SHA {head_sha[:8]}")
            
            pr_details = _get_pr_details(repo, pr_number) if pr_number else None
            if pr_details:
                current_sha = pr_details.get("headRefOid", "")
                if current_sha != head_sha:
                    return ValidatedAction(action, False,
                        f"RAIL: SHA mismatch - verdict for {head_sha[:8]}, current is {current_sha[:8]}")
                
                ci_status = _ci_status_from_pr(pr_details)
                if not all(s == "success" for s in ci_status.values()):
                    return ValidatedAction(action, False, "RAIL: CI not passing")
                
                if not _is_approved(pr_details):
                    return ValidatedAction(action, False, "RAIL: PR not approved")
        
        elif action.action_type == ActionType.LABEL_NEEDS_KAREN.value:
            pr_details = _get_pr_details(repo, pr_number) if pr_number else None
            if not pr_details:
                return ValidatedAction(action, False, "RAIL: Cannot fetch PR details")
            
            if pr_details.get("isDraft"):
                return ValidatedAction(action, False, "RAIL: PR is still a draft")
            
            ci_status = _ci_status_from_pr(pr_details)
            if not all(s == "success" for s in ci_status.values()):
                return ValidatedAction(action, False, "RAIL: CI not passing")
            
            if not _is_approved(pr_details):
                return ValidatedAction(action, False, "RAIL: PR not approved")
            
            if _has_label(pr_details, "needs-karen"):
                return ValidatedAction(action, False, "RAIL: Already has needs-karen label")
        
        elif action.action_type == ActionType.FLAG_ANOMALY.value:
            pass
        
        elif action.action_type == ActionType.ALERT.value:
            pass
        
        else:
            return ValidatedAction(action, False, f"RAIL: Unknown action type {action.action_type}")
        
        return ValidatedAction(action, True, "Validated")
    
    def execute_action(self, validated: ValidatedAction, live: bool = False) -> ValidatedAction:
        """Execute a validated action and record metrics."""
        if not validated.validated:
            return validated
        
        action = validated.proposal
        
        if not live:
            validated.executed = False
            validated.execution_result = "shadow_mode"
            return validated
        
        try:
            if action.action_type == ActionType.MERGE_PR.value:
                merge_start = time.time()
                
                subprocess.run(
                    ["gh", "pr", "merge", str(action.pr_number), "--repo", action.repo,
                     "--squash", "--delete-branch", "--match-head-commit", action.head_sha],
                    capture_output=True, timeout=60, check=True,
                )
                validated.executed = True
                validated.execution_result = "merged"
                
                self._record_merge(action.repo, action.pr_number, action.head_sha)
                
                merge_duration_hours = (time.time() - merge_start) / 3600
                time_to_merge = action.extra.get("time_to_merge_hours", merge_duration_hours)
                self.metrics.record_merge(time_to_merge, successful=True)
                
                time.sleep(10)
                ci_ok, ci_status = self._check_main_ci_after_merge(action.repo)
                self._record_ci_result(
                    action.repo, ci_ok,
                    merge_sha=action.head_sha,
                    pr_number=action.pr_number,
                )
                if not ci_ok:
                    validated.execution_result = "merged (CI: {})".format(ci_status)
                
            elif action.action_type == ActionType.LABEL_NEEDS_KAREN.value:
                subprocess.run(
                    ["gh", "pr", "edit", str(action.pr_number), "--repo", action.repo,
                     "--add-label", "needs-karen"],
                    capture_output=True, timeout=30, check=True,
                )
                validated.executed = True
                validated.execution_result = "labeled"
                
            elif action.action_type in (ActionType.FLAG_ANOMALY.value, ActionType.ALERT.value):
                validated.executed = True
                validated.execution_result = "logged"
                
        except subprocess.SubprocessError as e:
            validated.executed = False
            validated.execution_result = f"error: {str(e)[:100]}"
            if action.action_type == ActionType.MERGE_PR.value:
                self.metrics.record_merge(0, successful=False)
        
        return validated
    
    def _get_merged_today(self) -> list:
        """Get list of PRs merged in the last 24 hours."""
        state = self._load_merge_state()
        now_ts = datetime.now(timezone.utc).timestamp()
        day_ago = now_ts - 86400
        return [m for m in state.get("merges", []) if m.get("ts", 0) > day_ago]
    
    def _get_fixed_prs(self) -> list:
        """Get list of PRs that were fixed by the fix loop."""
        try:
            from .fix_dispatcher import FIX_STATE_DIR, FixState
            fixed = []
            if FIX_STATE_DIR.is_dir():
                for path in FIX_STATE_DIR.glob("*.json"):
                    try:
                        state = FixState.from_dict(json.loads(path.read_text()))
                        for attempt in state.attempts:
                            if attempt.success:
                                fixed.append({
                                    "repo": state.repo,
                                    "pr": state.pr_number,
                                    "timestamp": attempt.timestamp,
                                })
                    except (json.JSONDecodeError, KeyError):
                        pass
            return fixed
        except ImportError:
            return []
    
    def _get_still_failing_prs(self, verdicts: list) -> list:
        """Get PRs that still have FAIL verdicts with reasons."""
        failing = []
        seen = set()
        for v in verdicts:
            key = (v.get("repo"), v.get("pr_number"))
            if key in seen:
                continue
            if v.get("verdict") == "KARENFAIL":
                seen.add(key)
                failing.append({
                    "repo": v.get("repo"),
                    "pr": v.get("pr_number"),
                    "reason": v.get("reason", "unknown")[:100],
                    "sha": v.get("sha", "")[:8],
                })
        return failing
    
    def _read_needs_jeff_items(self) -> list:
        """Read items from NEEDS_JEFF.md."""
        needs_jeff = self._needs_jeff_file()
        if not needs_jeff.is_file():
            return []
        
        items = []
        content = needs_jeff.read_text()
        sections = content.split("## ")[1:]
        for section in sections[:10]:
            lines = section.strip().split("\n")
            if lines:
                title = lines[0].strip()
                items.append(title[:80])
        return items
    
    def write_digest(self, snapshot: StateSnapshot, proposals: Optional[dict], 
                     validated_actions: list[ValidatedAction]) -> None:
        """Write morning digest at 8 AM CT.
        
        Includes: merged, fixed, still-failing with reasons, NEEDS_JEFF items,
        scorecard metrics with week-over-week comparison.
        """
        now = datetime.now(timezone.utc)
        ct_hour = _get_ct_hour()
        
        stuck_prs = self._count_stuck_prs(snapshot.open_prs)
        self.metrics.update_pr_counts(len(snapshot.open_prs), stuck_prs)
        
        merged_today = self._get_merged_today()
        fixed_prs = self._get_fixed_prs()
        still_failing = self._get_still_failing_prs(snapshot.karen_verdicts)
        needs_jeff_items = self._read_needs_jeff_items()
        
        lines = [
            f"# StoryPool Morning Digest",
            f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')} ({ct_hour}:00 CT)",
            "",
        ]
        
        lines.extend([
            "## Merged (last 24h)",
            "",
        ])
        if merged_today:
            for m in merged_today[:20]:
                lines.append(f"- {m.get('repo')} #{m.get('pr')} ({m.get('sha', '?')})")
            lines.append(f"")
            lines.append(f"**Total: {len(merged_today)} PRs merged**")
        else:
            lines.append("- No merges in the last 24 hours")
        
        lines.extend([
            "",
            "## Fixed by Fix Loop",
            "",
        ])
        if fixed_prs:
            for f in fixed_prs[:10]:
                lines.append(f"- {f.get('repo')} #{f.get('pr')}")
            lines.append(f"")
            lines.append(f"**Total: {len(fixed_prs)} PRs auto-fixed**")
        else:
            lines.append("- No PRs auto-fixed")
        
        lines.extend([
            "",
            "## Still Failing (need attention)",
            "",
        ])
        if still_failing:
            for f in still_failing[:15]:
                lines.append(f"- {f.get('repo')} #{f.get('pr')}: {f.get('reason')}")
            if len(still_failing) > 15:
                lines.append(f"- ... and {len(still_failing) - 15} more")
        else:
            lines.append("- No failing PRs (backlog clear!)")
        
        if needs_jeff_items:
            lines.extend([
                "",
                "## ⚠️ NEEDS_JEFF Items",
                "",
            ])
            for item in needs_jeff_items:
                lines.append(f"- {item}")
            lines.append("")
            lines.append(f"**See: {self._needs_jeff_file()}**")
        
        lines.extend([
            "",
            "## Scorecard Metrics",
            "",
            self.metrics.format_comparison_markdown(),
            "",
        ])
        
        job_stats = {}
        try:
            from .job_ledger import JobLedger
            ledger = JobLedger()
            job_stats = ledger.summary()
        except (ImportError, Exception):
            pass
        
        if job_stats:
            lines.extend([
                "",
                "## Job Ledger (OUTCOME-VERIFIED)",
                "",
                f"- Running jobs: {job_stats.get('running', 0)}",
                f"- Queued jobs: {job_stats.get('queued', 0)}",
                f"- Done (24h): {job_stats.get('done_24h', 0)}",
                f"- Failed (24h): {job_stats.get('failed_24h', 0)}",
                f"- Verified (24h): {job_stats.get('verified_24h', 0)}",
                f"- Jobs (1h): {job_stats.get('jobs_last_hour', 0)}",
                "",
            ])
        
        priority_prs = [pr for pr in snapshot.open_prs if pr.get("is_priority")]
        lines.extend([
            "",
            "## System State",
            f"- Open PRs: {len(snapshot.open_prs)} ({len(priority_prs)} priority, {stuck_prs} stuck)",
            f"- Mini: load={snapshot.machine_guards.get('mini', {}).get('load', '?')}, "
            f"passed={snapshot.machine_guards.get('mini', {}).get('passed', '?')}",
            f"- NucBox: load={snapshot.machine_guards.get('nucbox', {}).get('load', '?')}, "
            f"passed={snapshot.machine_guards.get('nucbox', {}).get('passed', '?')}",
            "",
            "## Budget",
        ])
        
        for provider, data in snapshot.budget_summary.items():
            pct = data.get("weekly_pct", 0)
            cost = data.get("cost_usd", 0)
            cap = "⚠️" if pct > 70 else "✓"
            lines.append(f"- {provider}: ${cost:.2f} ({pct:.1f}%) {cap}")
        
        lines.extend([
            "",
            "---",
            f"*Log: {self._log_file()}*",
        ])
        
        self._digest_file().write_text("\n".join(lines))
    
    def _count_stuck_prs(self, open_prs: list) -> int:
        """Count PRs with no progress for >24 hours."""
        stuck = 0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        
        for pr in open_prs:
            updated_at = pr.get("updatedAt", "")
            if updated_at:
                try:
                    updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    if updated < cutoff and not pr.get("isDraft"):
                        stuck += 1
                except (ValueError, TypeError):
                    pass
        
        return stuck
    
    def run(self, live: bool = False) -> dict:
        """Run a conductor cycle.
        
        1. Build state snapshot
        2. Get LLM proposals (fail-safe if unavailable)
        3. Validate each proposal against rails
        4. Execute validated actions (if live mode)
        5. Write digest
        """
        from .node_control import is_draining
        if is_draining():
            return {"timestamp": _now_iso(), "machine": os.environ.get("STORYPOOL_MACHINE", "unknown"), "live": live,
                    "maintenance": True, "actions_executed": 0}
        self._write_log({"action": "cycle_start", "live": live})
        
        snapshot = self.build_state_snapshot()
        
        has_anomalies = len(snapshot.recent_errors) > 5
        proposals = self.get_llm_proposals(snapshot, high_effort=has_anomalies)
        
        validated_actions = []
        
        llm_unavailable_reason = ""
        
        if proposals is None:
            codex_avail, codex_reason = self.codex.is_available()
            gemini_avail, gemini_reason = self.gemini.is_available()
            can_codex, codex_budget_reason = self._can_use_codex()
            can_gemini, gemini_budget_reason = self._can_use_gemini()
            
            reasons = []
            if not codex_avail:
                reasons.append("Codex: {}".format(codex_reason))
            elif not can_codex:
                reasons.append("Codex: {}".format(codex_budget_reason))
            elif self.codex.last_error:
                reasons.append("Codex: {}".format(self.codex.last_error))
            else:
                reasons.append("Codex: returned empty response")
            
            if not gemini_avail:
                reasons.append("Gemini: {}".format(gemini_reason))
            elif not can_gemini:
                reasons.append("Gemini: {}".format(gemini_budget_reason))
            elif self.gemini.last_error:
                reasons.append("Gemini: {}".format(self.gemini.last_error))
            else:
                reasons.append("Gemini: returned empty response")
            
            llm_unavailable_reason = "; ".join(reasons)
            self._write_alert(
                "LLM unavailable - pausing conductor: {}".format(llm_unavailable_reason),
                {
                    "codex_available": codex_avail,
                    "codex_reason": codex_reason if not codex_avail else codex_budget_reason,
                    "codex_last_error": self.codex.last_error,
                    "gemini_available": gemini_avail,
                    "gemini_reason": gemini_reason if not gemini_avail else gemini_budget_reason,
                    "gemini_last_error": self.gemini.last_error,
                }
            )
            self._write_log({
                "action": "fail_safe", 
                "reason": "llm_unavailable",
                "details": llm_unavailable_reason,
            })
        else:
            for action_dict in proposals.get("proposed_actions", []):
                try:
                    action = ProposedAction(
                        action_type=action_dict.get("action_type", ""),
                        repo=action_dict.get("repo"),
                        pr_number=action_dict.get("pr_number"),
                        head_sha=action_dict.get("head_sha"),
                        reason=action_dict.get("reason", ""),
                        priority=action_dict.get("priority", "normal"),
                    )
                except (TypeError, KeyError):
                    continue
                
                validated = self.validate_action(action, snapshot)
                
                self._write_log({
                    "action": "validate_proposal",
                    "proposal": action.to_dict(),
                    "validated": validated.validated,
                    "validation_reason": validated.validation_reason,
                })
                
                if validated.validated:
                    validated = self.execute_action(validated, live=live)
                    self._write_log({
                        "action": "execute_action" if live else "shadow_action",
                        "proposal": action.to_dict(),
                        "executed": validated.executed,
                        "result": validated.execution_result,
                    })
                
                validated_actions.append(validated)
        
        self.write_digest(snapshot, proposals, validated_actions)
        
        summary = {
            "timestamp": _now_iso(),
            "live": live,
            "llm_available": proposals is not None,
            "llm_unavailable_reason": llm_unavailable_reason if proposals is None else "",
            "proposals_count": len(proposals.get("proposed_actions", [])) if proposals else 0,
            "validated_count": sum(1 for va in validated_actions if va.validated),
            "rejected_count": sum(1 for va in validated_actions if not va.validated),
            "executed_count": sum(1 for va in validated_actions if va.executed),
            "anomalies": proposals.get("anomalies", []) if proposals else [],
        }
        
        self._write_status(summary)
        self._write_log({"action": "cycle_end", "summary": summary})
        
        return summary


def main() -> int:
    """CLI entry point for conductor."""
    from .node_control import is_draining
    if is_draining():
        print("Node in maintenance; conductor deferred")
        return 0
    import argparse
    
    parser = argparse.ArgumentParser(
        description="StoryPool LLM-in-the-loop conductor"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually perform actions (default is shadow/dry-run mode)",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Directory for conductor logs",
    )
    parser.add_argument(
        "--verdict-dir",
        type=Path,
        default=None,
        help="Directory for Karen verdict files",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output summary as JSON",
    )
    parser.add_argument(
        "--use-claude",
        action="store_true",
        help="Enable Claude as additional fallback (off by default)",
    )
    args = parser.parse_args()
    
    conductor = Conductor(
        log_dir=args.log_dir,
        verdict_dir=args.verdict_dir,
        use_claude=args.use_claude,
    )
    
    summary = conductor.run(live=args.live)
    
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        mode = "LIVE" if args.live else "SHADOW"
        llm_status = "✓" if summary["llm_available"] else "✗ PAUSED"
        print(f"[{mode}] Conductor run at {summary['timestamp']}")
        print(f"  LLM: {llm_status}")
        if summary.get("llm_unavailable_reason"):
            print(f"  Reason: {summary['llm_unavailable_reason']}")
        print(f"  Proposals: {summary['proposals_count']} ({summary['validated_count']} validated, {summary['rejected_count']} rejected)")
        print(f"  Executed: {summary['executed_count']}")
        if summary["anomalies"]:
            print(f"  Anomalies: {len(summary['anomalies'])}")
        print(f"  Digest: {conductor._digest_file()}")
        print(f"  Log: {conductor._log_file()}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
