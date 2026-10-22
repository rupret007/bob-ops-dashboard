#!/usr/bin/env python3
"""Watchdog service for StoryPool self-healing.

Detects and auto-repairs:
- Stuck services (no log line in 30 min)
- Repeated tracebacks in logs
- gh/codex missing from PATH
- Missing labels (needs-karen, karen-pass, karen-fail)
- Stale install (behind origin by >1 day)

Anything it can't fix goes to NEEDS_JEFF.md.

Runs every 10 minutes via launchd (Mini) or systemd timer (NucBox).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

from . import watchdog_io as _watchdog_io
from . import watchdog_repo_checks as _watchdog_repo_checks
from . import watchdog_log_checks as _watchdog_log_checks

from .github_budget import GitHubDeferred
from .github_reads import GitHubReadError
from .health_state import authoritative, serialized_recovery

LOG_DIR = Path(
    os.environ.get("STORYPOOL_LOG_DIR", "~/.local/share/storypool")
).expanduser()

WATCHDOG_LOG = LOG_DIR / "watchdog.log"
NEEDS_JEFF_FILE = LOG_DIR / "conductor" / "NEEDS_JEFF.md"

STUCK_THRESHOLD_MINUTES = 30
MAX_TRACEBACKS_PER_HOUR = 5
STALE_INSTALL_HOURS = 24
HEAD_LAG_ALERT_MINUTES = 30

REQUIRED_LABELS = ["needs-karen", "karen-pass", "karen-fail"]

SERVICE_LOG_FILES = {
    "shadow": LOG_DIR / "shadow" / "shadow.log",
    "dispatch": LOG_DIR / "dispatch" / "dispatch.log",
    "conductor": LOG_DIR / "conductor",
    "poller": Path("~/.local/share/karen/poller.log").expanduser(),
    "dashboard": LOG_DIR / "dashboard.log",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("watchdog")


@dataclass
class HealthCheck:
    """Result of a health check."""
    name: str
    passed: bool
    message: str
    can_repair: bool = True
    repaired: bool = False
    repair_action: str = ""
    execution_error: bool = False
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass  
class WatchdogResult:
    """Result of a watchdog run."""
    timestamp: str
    machine: str
    checks: List[HealthCheck] = field(default_factory=list)
    repairs_attempted: int = 0
    repairs_successful: int = 0
    escalated_to_jeff: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "machine": self.machine,
            "checks": [c.to_dict() for c in self.checks],
            "repairs_attempted": self.repairs_attempted,
            "repairs_successful": self.repairs_successful,
            "escalated_to_jeff": self.escalated_to_jeff,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_machine() -> str:
    """Detect which machine we're running on."""
    return os.environ.get("STORYPOOL_MACHINE", "mini" if os.uname().sysname == "Darwin" else "nucbox")


def _write_watchdog_log(entry: dict, followup: bool = False) -> None:
    """Append entry to watchdog log."""
    return _watchdog_io._write_watchdog_log(sys.modules[__name__], entry, followup)


def _escalate_to_jeff(issue: str, details: str) -> None:
    """Write issue to NEEDS_JEFF.md."""
    return _watchdog_io._escalate_to_jeff(sys.modules[__name__], issue, details)


from .peer_endpoints import PeerURLs, PeerUnavailable

PEER_URLS = PeerURLs()


def _dashboard_alive(timeout: float = 3.0) -> bool:
    """An idle dashboard writes no logs; a live /health answer means it is not stuck."""
    return _watchdog_io._dashboard_alive(sys.modules[__name__], timeout)


def store_peer_engine(peer: str, data: dict) -> bool:
    """Keep the peer's self-reported running jobs and LLM usage for the local dashboard.

    Only the peer's own (source=local) section is kept, so data never echoes back.
    """
    return _watchdog_io.store_peer_engine(sys.modules[__name__], peer, data)


def _peer_sync_status(peer, ok, failure_kind=None):
    return _watchdog_io._peer_sync_status(sys.modules[__name__], peer, ok, failure_kind)


def sync_peer_health(machine: str) -> bool:
    """Copy the peer's self-reported health (over Tailscale) into machines/<peer>/watchdog.log.

    The row keeps the peer's own watchdog timestamp, so stale peers still show stale.
    """
    return _watchdog_io.sync_peer_health(sys.modules[__name__], machine)


def check_service_stuck(service: str, log_path: Path) -> HealthCheck:
    """Check if a service has logged recently."""
    return _watchdog_log_checks.check_service_stuck(sys.modules[__name__], service, log_path)


def check_tracebacks(service: str, log_path: Path) -> HealthCheck:
    """Check for repeated tracebacks in service logs."""
    return _watchdog_log_checks.check_tracebacks(sys.modules[__name__], service, log_path)


def check_gh_in_path() -> HealthCheck:
    """Check if gh CLI is available."""
    return _watchdog_repo_checks.check_gh_in_path(sys.modules[__name__])


def check_codex_in_path() -> HealthCheck:
    """Check if codex CLI is available."""
    return _watchdog_repo_checks.check_codex_in_path(sys.modules[__name__])


def check_labels_exist(repo: str) -> HealthCheck:
    """Check if required labels exist in a repo."""
    return _watchdog_repo_checks.check_labels_exist(sys.modules[__name__], repo)


def check_head_lag() -> HealthCheck:
    """Check if HEAD lags behind origin by more than 30 minutes.
    
    This detects when self-update is failing silently.
    """
    return _watchdog_repo_checks.check_head_lag(sys.modules[__name__])


def check_install_stale() -> HealthCheck:
    """Check if install is behind origin."""
    return _watchdog_repo_checks.check_install_stale(sys.modules[__name__])


@serialized_recovery
def repair_restart_service(service: str, machine: str) -> bool:
    """Restart a stuck service."""
    try:
        if machine == "mini":
            label = f"com.storypool.{service}"
            if service == "poller":
                label = "com.storypool.karen-poller"
            
            result = subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"],
                capture_output=True,
                timeout=30,
            )
            logger.info("Restarted %s via launchctl", service)
            return result.returncode == 0
        else:
            unit = f"storypool-{service}"
            result = subprocess.run(
                ["systemctl", "--user", "restart", f"{unit}.service"],
                capture_output=True,
                timeout=30,
            )
            logger.info("Restarted %s via systemctl", service)
            return result.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        logger.error("Failed to restart %s: %s", service, e)
        return False


@serialized_recovery
def repair_create_labels(repo: str) -> bool:
    """Create missing labels in a repo."""
    label_specs = {
        "needs-karen": ("FBCA04", "Ready for Karen gate review"),
        "karen-pass": ("0E8A16", "Karen gate passed"),
        "karen-fail": ("D93F0B", "Karen gate failed"),
    }
    
    created = 0
    for label, (color, description) in label_specs.items():
        try:
            result = subprocess.run(
                ["gh", "label", "create", label, "--repo", repo,
                 "--color", color, "--description", description, "--force"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                created += 1
                logger.info("Created label %s in %s", label, repo)
        except subprocess.SubprocessError:
            pass
    
    return created > 0


@serialized_recovery
def repair_self_update(machine: str) -> bool:
    """Trigger self-update."""
    try:
        from .self_update import self_update
        result = self_update(machine=machine)
        if result.get("updated") or result.get("reinstalled"):
            logger.info("Self-update completed: %s", result.get("reason"))
            return True
        return False
    except Exception as e:
        logger.error("Self-update failed: %s", e)
        return False


def run_watchdog(machine: Optional[str] = None, check_only: bool = False) -> WatchdogResult:
    """Run all watchdog checks and attempt repairs."""
    if machine is None:
        machine = _get_machine()
    
    result = WatchdogResult(
        timestamp=_now_iso(),
        machine=machine,
    )
    
    from .node_control import is_draining
    if is_draining(machine):
        result.checks.append(HealthCheck(name="maintenance", passed=True,
                            message="Operator maintenance; repairs suspended", can_repair=False))
        _write_watchdog_log(result.to_dict())
        return result

    from .watchdog_check import run_check
    tasks = []
    for service, log_path in SERVICE_LOG_FILES.items():
        if service == "poller" and machine == "nucbox":
            continue
        
        tasks.extend([(check_service_stuck, service, log_path),
                      (check_tracebacks, service, log_path)])
    tasks.extend([(check_gh_in_path,), (check_codex_in_path,)])
    
    sample_repos = ["rupret007/webjam", "rupret007/bob-ops-dashboard"]
    for repo in sample_repos[:1]:
        tasks.append((check_labels_exist, repo))
    
    tasks.extend([(check_install_stale,), (check_head_lag,)])
    # Four bounded subprocesses at a time. Repairs stay sequential and outside
    # the pool so a timed-out check cannot later perform a repair.
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = [pool.submit(run_check, *task) for task in tasks]
        result.checks.extend(future.result() for future in pending)

    if check_only:
        _write_watchdog_log(result.to_dict())
        return result
    
    for check in result.checks:
        if not check.passed:
            result.repairs_attempted += 1
            
            if check.repair_action == "restart_service":
                service = check.name.split("_")[0]
                if repair_restart_service(service, machine):
                    check.repaired = True
                    result.repairs_successful += 1
            
            elif check.repair_action == "create_labels":
                repo = check.name.replace("labels_", "").replace("_", "/")
                if repair_create_labels(repo):
                    check.repaired = True
                    result.repairs_successful += 1
            
            elif check.repair_action == "self_update":
                if repair_self_update(machine):
                    check.repaired = True
                    result.repairs_successful += 1
            
            if not check.repaired and not check.can_repair:
                _escalate_to_jeff(check.name, check.message)
                result.escalated_to_jeff.append(check.name)
    
    _write_watchdog_log(result.to_dict())
    
    return result


def main() -> int:
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="StoryPool watchdog")
    parser.add_argument("--machine", choices=["mini", "nucbox"], default=None)
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--check-only", action="store_true", help="Don't attempt repairs")
    parser.add_argument("--findings-only", action="store_true",
                        help="Report findings without repairs; execution errors still fail")
    
    args = parser.parse_args()
    if authoritative() and not (args.check_only or args.findings_only):
        return 0
    
    check_only = args.check_only or args.findings_only
    result = run_watchdog(machine=args.machine, check_only=check_only)
    sync_peer_health(result.machine)
    if not check_only:
        from .node_control import is_draining
        if not is_draining():
            from .main_ci import reconcile_recent
            from .coordination import CoordinationError
            try:
                _write_watchdog_log({"timestamp": _now_iso(), "action": "main_ci_followup",
                                     "results": reconcile_recent()}, followup=True)
            except (CoordinationError, OSError, subprocess.SubprocessError, ValueError):
                _write_watchdog_log({"timestamp": _now_iso(), "action": "main_ci_followup",
                                     "status": "deferred"}, followup=True)
    
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        passed = sum(1 for c in result.checks if c.passed)
        failed = sum(1 for c in result.checks if not c.passed)
        print(f"[{result.machine}] Watchdog: {passed} passed, {failed} failed")
        print(f"  Repairs: {result.repairs_successful}/{result.repairs_attempted}")
        if result.escalated_to_jeff:
            print(f"  Escalated: {', '.join(result.escalated_to_jeff)}")
        
        for check in result.checks:
            status = "✓" if check.passed else ("⚠ REPAIRED" if check.repaired else "✗")
            print(f"  {status} {check.name}: {check.message}")
    
    if any(c.execution_error for c in result.checks):
        return 2
    if args.findings_only:
        return 0
    return 0 if all(c.passed or c.repaired for c in result.checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
