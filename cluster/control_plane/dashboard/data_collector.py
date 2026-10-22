"""Data collector for StoryPool dashboard.

OUTCOME-VERIFIED: Reads from job ledger and verdicts for verified outcomes.
Reads from existing JSON/log files to build dashboard state.
Read-only, no secrets displayed.
"""

from __future__ import annotations

from . import system_collection, activity_collection

import json
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

from ..codex_quota import read_codex_quota
from ..guards import MINI_SWAP_LIMIT_PCT

try:
    from ..job_ledger import JobLedger, Job, JobState
    LEDGER_AVAILABLE = True
except ImportError:
    LEDGER_AVAILABLE = False
    JobLedger = None
    Job = None
    JobState = None

STORYPOOL_DATA_DIR = Path(
    os.environ.get("STORYPOOL_LOG_DIR", "~/.local/share/storypool")
).expanduser()

KAREN_DATA_DIR = Path(
    os.environ.get("KAREN_STATE_DIR", "~/.local/share/karen")
).expanduser()

METRICS_DIR = STORYPOOL_DATA_DIR / "metrics"


def _safe_read_json(path: Path) -> Optional[dict]:
    """Safely read a JSON file, returning None on error."""
    try:
        if path.is_file():
            return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _safe_read_lines(path: Path, max_lines: int = 100) -> List[str]:
    """Safely read last N lines from a file."""
    try:
        if path.is_file():
            lines = path.read_text().splitlines()
            return lines[-max_lines:]
    except OSError:
        pass
    return []


def _parse_jsonl(path: Path, max_entries: int = 50) -> List[dict]:
    """Parse a JSONL file, returning last N entries."""
    entries = []
    lines = _safe_read_lines(path, max_entries * 2)
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return entries[-max_entries:]


def _get_machine() -> str:
    """Detect which machine we're on."""
    return os.environ.get("STORYPOOL_MACHINE", "mini" if os.uname().sysname == "Darwin" else "nucbox")


def _is_live_mode() -> bool:
    """Check if running in live mode."""
    live_marker = Path("~/.config/storypool/live").expanduser()
    return live_marker.is_file() or os.environ.get("STORYPOOL_LIVE", "").lower() in ("1", "true", "yes")


@dataclass
class ServiceHealth:
    """Health status of a service."""
    name: str
    status: str  # "running", "stopped", "error", "unknown"
    last_run: str
    last_run_ago: str
    log_file: str
    error_count: int = 0
    details: str = ""


@dataclass 
class GuardValues:
    """Current guard values for a machine."""
    machine: str
    load: float
    load_limit: float
    swap_pct: float = 0.0
    swap_limit: float = MINI_SWAP_LIMIT_PCT
    disk_gb: float = 0.0
    disk_min: float = 15.0
    lanes_active: int = 0
    lanes_max: int = 7
    guards_passed: bool = True
    reason: str = ""


@dataclass
class PRStatus:
    """Status of a PR in the backlog."""
    repo: str
    pr_number: int
    title: str
    state: str  # "draft", "needs-karen", "karen-pass", "karen-fail", "merged"
    ci_status: str  # "passing", "failing", "pending"
    author: str
    head_sha: str
    updated_at: str
    verdict: str = ""
    verdict_reason: str = ""
    fix_attempts: int = 0
    url: str = ""


@dataclass
class KarenVerdict:
    """A Karen verdict with details."""
    repo: str
    pr_number: int
    sha: str
    verdict: str  # "PASS", "FAIL"
    reason: str
    reviewer: str
    timestamp: str
    url: str = ""


@dataclass
class FixAttemptRecord:
    """Record of a fix loop attempt."""
    repo: str
    pr_number: int
    timestamp: str
    success: bool
    tool: str
    error: str = ""


@dataclass
class MergeRecord:
    """Record of a merge."""
    repo: str
    pr_number: int
    sha: str
    timestamp: str


@dataclass
class JobRecord:
    """A job from the ledger (OUTCOME-VERIFIED)."""
    job_id: str
    repo: str
    pr_number: int
    head_sha: str
    provider: str
    machine: str
    state: str
    outcome: str
    outcome_verified: bool
    pid: int
    start_time: str
    end_time: str
    error: str


@dataclass
class WatchdogEvent:
    """A watchdog event."""
    timestamp: str
    check_name: str
    passed: bool
    message: str
    repaired: bool = False


@dataclass
class NeedsJeffItem:
    """An item requiring Jeff's attention."""
    timestamp: str
    issue: str
    details: str


@dataclass
class ProviderBudget:
    """Budget usage for a provider."""
    name: str
    weekly_pct: Optional[float]
    daily_spend_usd: Optional[float]
    weekly_spend_usd: Optional[float]
    cap_pct: float
    status: str  # "ok", "warning", "capped", "conserve"


@dataclass
class ScorecardMetrics:
    """Scorecard metrics."""
    first_try_pass_rate: float
    pre_llm_catch_rate: float
    fix_loop_success_rate: float
    median_time_to_merge_hours: float
    total_reviews: int
    total_merges: int
    stuck_pr_rate: float
    week_over_week: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DashboardData:
    """Complete dashboard data."""
    timestamp: str
    machine: str
    live_mode: bool
    
    services: List[ServiceHealth] = field(default_factory=list)
    guards: Optional[GuardValues] = None
    
    backlog: List[PRStatus] = field(default_factory=list)
    verdicts: List[KarenVerdict] = field(default_factory=list)
    fix_attempts: List[FixAttemptRecord] = field(default_factory=list)
    
    merges_today: int = 0
    merges_today_list: List[MergeRecord] = field(default_factory=list)
    merge_cap_hour: int = 6
    merge_cap_day: int = 30
    merges_this_hour: int = 0
    
    watchdog_events: List[WatchdogEvent] = field(default_factory=list)
    needs_jeff: List[NeedsJeffItem] = field(default_factory=list)
    
    providers: List[ProviderBudget] = field(default_factory=list)
    scorecard: Optional[ScorecardMetrics] = None
    
    jobs_running: List[JobRecord] = field(default_factory=list)
    jobs_recent: List[JobRecord] = field(default_factory=list)
    jobs_stats: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        d = asdict(self)
        return d


class DataCollector:
    """Collects dashboard data from local files."""
    
    def __init__(
        self,
        data_dir: Optional[Path] = None,
        karen_dir: Optional[Path] = None,
        metrics_dir: Optional[Path] = None,
    ):
        self.data_dir = data_dir or STORYPOOL_DATA_DIR
        self.karen_dir = karen_dir or KAREN_DATA_DIR
        self.metrics_dir = metrics_dir or METRICS_DIR
        self.machine = _get_machine()
    
    def collect(self) -> DashboardData:
        """Collect all dashboard data.
        
        OUTCOME-VERIFIED: Reads from job ledger for verified job outcomes.
        """
        data = DashboardData(
            timestamp=datetime.now(timezone.utc).isoformat(),
            machine=self.machine,
            live_mode=_is_live_mode(),
        )
        
        data.services = self._collect_services()
        data.guards = self._collect_guards()
        data.backlog = self._collect_backlog()
        data.verdicts = self._collect_verdicts()
        data.fix_attempts = self._collect_fix_attempts()
        
        merges = self._collect_merges()
        data.merges_today = len(merges)
        data.merges_today_list = merges
        data.merges_this_hour = self._count_merges_this_hour(merges)
        
        data.watchdog_events = self._collect_watchdog_events()
        data.needs_jeff = self._collect_needs_jeff()
        data.providers = self._collect_providers()
        data.scorecard = self._collect_scorecard()
        
        jobs_running, jobs_recent, jobs_stats = self._collect_jobs()
        data.jobs_running = jobs_running
        data.jobs_recent = jobs_recent
        data.jobs_stats = jobs_stats
        
        return data
    
    def _collect_services(self) -> List[ServiceHealth]:
        """Collect service health status."""
        return system_collection._collect_services(globals(), self)
    
    def _collect_guards(self) -> GuardValues:
        """Collect current guard values."""
        return system_collection._collect_guards(globals(), self)
    
    def _collect_backlog(self) -> List[PRStatus]:
        """Collect backlog from backlog report."""
        backlog = []
        
        backlog_file = self.data_dir / "backlog.json"
        data = _safe_read_json(backlog_file)
        if not data:
            return backlog
        
        for pr in data.get("prs", []):
            status = PRStatus(
                repo=pr.get("repo", ""),
                pr_number=pr.get("number", 0),
                title=pr.get("title", "")[:60],
                state=pr.get("state", "unknown"),
                ci_status=pr.get("ci", "unknown"),
                author=pr.get("author", "unknown"),
                head_sha=pr.get("sha", "")[:8],
                updated_at=pr.get("updated_at", ""),
                url=pr.get("url", ""),
            )
            backlog.append(status)
        
        return backlog[:50]
    
    def _collect_verdicts(self) -> List[KarenVerdict]:
        """Collect recent Karen verdicts."""
        verdicts = []
        verdict_dir = self.karen_dir / "verdicts"
        
        if not verdict_dir.is_dir():
            verdict_dir = self.data_dir / "verdicts"
        
        if not verdict_dir.is_dir():
            return verdicts
        
        verdict_files = []
        for repo_dir in verdict_dir.iterdir():
            if repo_dir.is_dir():
                verdict_files.extend(repo_dir.glob("pr-*.json"))
        
        verdict_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        
        for vf in verdict_files[:20]:
            v = _safe_read_json(vf)
            if not v:
                continue
            
            verdict = KarenVerdict(
                repo=v.get("repo", f"{v.get('owner', '')}/{v.get('repo', '')}"),
                pr_number=v.get("pr_number", 0),
                sha=v.get("head_sha", "")[:8],
                verdict="PASS" if v.get("passed") else "FAIL",
                reason=v.get("reason", "")[:100],
                reviewer=v.get("reviewer", "unknown"),
                timestamp=v.get("timestamp", ""),
            )
            if verdict.repo and not "/" in verdict.repo:
                verdict.repo = f"{v.get('owner', 'unknown')}/{verdict.repo}"
            verdicts.append(verdict)
        
        return verdicts
    
    def _collect_fix_attempts(self) -> List[FixAttemptRecord]:
        """Collect fix loop attempts."""
        attempts = []
        fix_dir = self.data_dir / "fix_state"
        
        if not fix_dir.is_dir():
            return attempts
        
        for state_file in fix_dir.glob("*.json"):
            data = _safe_read_json(state_file)
            if not data:
                continue
            
            for a in data.get("attempts", []):
                attempt = FixAttemptRecord(
                    repo=data.get("repo", ""),
                    pr_number=data.get("pr_number", 0),
                    timestamp=a.get("timestamp", ""),
                    success=a.get("success", False),
                    tool=a.get("tool", "unknown"),
                    error=a.get("error", "")[:100] if not a.get("success") else "",
                )
                attempts.append(attempt)
        
        attempts.sort(key=lambda a: a.timestamp, reverse=True)
        return attempts[:20]
    
    def _collect_merges(self) -> List[MergeRecord]:
        """Collect today's merges."""
        merges = []
        
        merge_state = _safe_read_json(self.data_dir / "conductor" / "merge-state.json")
        if not merge_state:
            return merges
        
        now_ts = datetime.now(timezone.utc).timestamp()
        day_ago = now_ts - 86400
        
        for m in merge_state.get("merges", []):
            if m.get("ts", 0) > day_ago:
                merges.append(MergeRecord(
                    repo=m.get("repo", ""),
                    pr_number=m.get("pr", 0),
                    sha=m.get("sha", "")[:8],
                    timestamp=m.get("time", ""),
                ))
        
        return merges
    
    def _count_merges_this_hour(self, merges: List[MergeRecord]) -> int:
        """Count merges in the last hour."""
        now = datetime.now(timezone.utc)
        hour_ago = now - timedelta(hours=1)
        
        count = 0
        for m in merges:
            try:
                ts = datetime.fromisoformat(m.timestamp.replace("Z", "+00:00"))
                if ts > hour_ago:
                    count += 1
            except (ValueError, TypeError):
                pass
        
        return count
    
    def _collect_jobs(self) -> tuple:
        """Collect job ledger data (OUTCOME-VERIFIED).
            
            Returns (running_jobs, recent_jobs, stats).
            """
        return activity_collection._collect_jobs(globals(), self)
    
    def _collect_watchdog_events(self) -> List[WatchdogEvent]:
        """Collect recent watchdog events."""
        return activity_collection._collect_watchdog_events(globals(), self)
    
    def _collect_needs_jeff(self) -> List[NeedsJeffItem]:
        """Collect NEEDS_JEFF items."""
        return system_collection._collect_needs_jeff(globals(), self)
    
    def _collect_providers(self) -> List[ProviderBudget]:
        """Collect provider budget usage."""
        return system_collection._collect_providers(globals(), self)
    
    def _collect_scorecard(self) -> Optional[ScorecardMetrics]:
        """Collect scorecard metrics."""
        return system_collection._collect_scorecard(globals(), self)
