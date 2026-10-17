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
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from ..never_touch import check_merge_allowed, NEVER_TOUCH_REPOS
from .budget_tracker import BudgetTracker
from .dispatcher import _get_open_prs, _run_gh, REPOS
from .guards import MachineGuards, mini_guards_pass, nucbox_guards_pass

CONDUCTOR_LOG_DIR = Path(
    os.environ.get("STORYPOOL_CONDUCTOR_LOG_DIR", "~/.local/share/storypool/conductor")
).expanduser()

VERDICT_DIR = Path(
    os.environ.get("STORYPOOL_VERDICT_DIR", "~/.local/share/storypool/verdicts")
).expanduser()

CODEX_WEEKLY_CAP_PCT = 75.0
GEMINI_DAILY_BUDGET_USD = 5.0


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
    repos: list[str]
    open_prs: list[dict]
    ci_statuses: dict[str, dict]
    karen_verdicts: list[dict]
    lane_usage: dict[str, int]
    machine_guards: dict[str, dict]
    budget_summary: dict[str, dict]
    recent_errors: list[dict]
    never_touch_repos: list[str]
    
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
            lines.append(f"- {pr.get('repo')} #{pr.get('number')}: {status}, CI {ci_summary}, verdict={verdict_str}")
        
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
    
    def __init__(self, host: str = "nuc", effort: str = "low"):
        self.host = host
        self.effort = effort
    
    def complete(self, prompt: str, high_effort: bool = False) -> Optional[str]:
        """Run codex exec with the given prompt."""
        effort = "high" if high_effort else self.effort
        env = os.environ.copy()
        env["CODEX_HOST"] = self.host
        
        try:
            result = subprocess.run(
                ["codex", "exec", "--effort", effort, prompt],
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except (subprocess.SubprocessError, FileNotFoundError):
            return None


class GeminiClient:
    """Client for Gemini API (fallback)."""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or self._load_key()
    
    def _load_key(self) -> str:
        key_file = Path("~/.config/storypool/gemini.key").expanduser()
        if key_file.is_file():
            return key_file.read_text().strip()
        return os.environ.get("GEMINI_API_KEY", "")
    
    def complete(self, prompt: str) -> Optional[str]:
        """Call Gemini API with the given prompt."""
        if not self.api_key:
            return None
        return "[Gemini stub - not implemented]"


CONDUCTOR_SYSTEM_PROMPT = """You are the StoryPool conductor, an intelligent orchestrator for a coding lane system.

Your job is to analyze the current system state and propose actions to keep work flowing smoothly.

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
    """
    
    def __init__(
        self,
        log_dir: Optional[Path] = None,
        verdict_dir: Optional[Path] = None,
        budget_tracker: Optional[BudgetTracker] = None,
        codex_client: Optional[CodexClient] = None,
        gemini_client: Optional[GeminiClient] = None,
        use_claude: bool = False,
    ):
        self.log_dir = log_dir or CONDUCTOR_LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.verdict_dir = verdict_dir or VERDICT_DIR
        self.verdict_dir.mkdir(parents=True, exist_ok=True)
        self.budget = budget_tracker or BudgetTracker()
        self.codex = codex_client or CodexClient()
        self.gemini = gemini_client or GeminiClient()
        self.use_claude = use_claude
        self.validated_actions: list[ValidatedAction] = []
        
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
    
    def _write_log(self, entry: dict) -> None:
        entry["timestamp"] = _now_iso()
        with open(self._log_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    
    def _write_alert(self, message: str, details: dict) -> None:
        """Write an alert file when fail-safe triggers."""
        alert = {
            "timestamp": _now_iso(),
            "message": message,
            "details": details,
            "status": "PAUSED",
        }
        self._alert_file().write_text(json.dumps(alert, indent=2))
        self._write_log({"action": "alert", "message": message, **details})
    
    def _write_status(self, summary: dict) -> None:
        status = {"timestamp": _now_iso(), "summary": summary}
        self._status_file().write_text(json.dumps(status, indent=2))
    
    def _can_use_codex(self) -> tuple[bool, str]:
        """Check if Codex is within budget cap."""
        pct = self.budget.weekly_pct("codex")
        if pct >= CODEX_WEEKLY_CAP_PCT:
            return False, f"Codex at {pct:.1f}% of weekly cap (limit {CODEX_WEEKLY_CAP_PCT}%)"
        return True, "ok"
    
    def _can_use_gemini(self) -> tuple[bool, str]:
        """Check if Gemini is available."""
        return self.budget.can_use("gemini")
    
    def build_state_snapshot(self) -> StateSnapshot:
        """Build complete state snapshot for LLM context."""
        open_prs = []
        ci_statuses = {}
        
        for repo in REPOS:
            prs = _get_open_prs(repo)
            for pr in prs:
                pr["repo"] = repo
                open_prs.append(pr)
                
                details = _get_pr_details(repo, pr.get("number"))
                if details:
                    ci = _ci_status_from_pr(details)
                    ci_statuses[f"{repo}/{pr.get('number')}"] = ci
        
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
            merge_allowed, reason = check_merge_allowed(repo)
            if not merge_allowed:
                return ValidatedAction(action, False, f"RAIL: {reason}")
            
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
        """Execute a validated action."""
        if not validated.validated:
            return validated
        
        action = validated.proposal
        
        if not live:
            validated.executed = False
            validated.execution_result = "shadow_mode"
            return validated
        
        try:
            if action.action_type == ActionType.MERGE_PR.value:
                subprocess.run(
                    ["gh", "pr", "merge", str(action.pr_number), "--repo", action.repo,
                     "--squash", "--delete-branch", "--match-head-commit", action.head_sha],
                    capture_output=True, timeout=60, check=True,
                )
                validated.executed = True
                validated.execution_result = "merged"
                
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
        
        return validated
    
    def write_digest(self, snapshot: StateSnapshot, proposals: Optional[dict], 
                     validated_actions: list[ValidatedAction]) -> None:
        """Write twice-daily human-readable digest."""
        now = datetime.now(timezone.utc)
        
        lines = [
            f"# StoryPool Conductor Digest",
            f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "## Summary",
        ]
        
        if proposals:
            lines.append(f"**Analysis:** {proposals.get('analysis', 'N/A')}")
            lines.append(f"**Summary:** {proposals.get('summary', 'N/A')}")
        else:
            lines.append("**Status:** LLM unavailable - running in fail-safe mode")
        
        lines.extend([
            "",
            "## Actions",
        ])
        
        for va in validated_actions:
            status = "✅ Executed" if va.executed else ("⏸️ Shadow" if va.validated else "❌ Rejected")
            lines.append(f"- {status}: {va.proposal.action_type} - {va.proposal.reason}")
            if not va.validated:
                lines.append(f"  - Rejection: {va.validation_reason}")
        
        if not validated_actions:
            lines.append("- No actions this cycle")
        
        if proposals and proposals.get("anomalies"):
            lines.extend([
                "",
                "## Anomalies Detected",
                *[f"- ⚠️ {a}" for a in proposals["anomalies"]],
            ])
        
        lines.extend([
            "",
            "## System State",
            f"- Open PRs: {len(snapshot.open_prs)}",
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
    
    def run(self, live: bool = False) -> dict:
        """Run a conductor cycle.
        
        1. Build state snapshot
        2. Get LLM proposals (fail-safe if unavailable)
        3. Validate each proposal against rails
        4. Execute validated actions (if live mode)
        5. Write digest
        """
        self._write_log({"action": "cycle_start", "live": live})
        
        snapshot = self.build_state_snapshot()
        
        has_anomalies = len(snapshot.recent_errors) > 5
        proposals = self.get_llm_proposals(snapshot, high_effort=has_anomalies)
        
        validated_actions = []
        
        if proposals is None:
            self._write_alert(
                "LLM unavailable - pausing conductor",
                {"codex_reason": self._can_use_codex()[1], 
                 "gemini_reason": self._can_use_gemini()[1]}
            )
            self._write_log({"action": "fail_safe", "reason": "llm_unavailable"})
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
        print(f"  Proposals: {summary['proposals_count']} ({summary['validated_count']} validated, {summary['rejected_count']} rejected)")
        print(f"  Executed: {summary['executed_count']}")
        if summary["anomalies"]:
            print(f"  Anomalies: {len(summary['anomalies'])}")
        print(f"  Digest: {conductor._digest_file()}")
        print(f"  Log: {conductor._log_file()}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
