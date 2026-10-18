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
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

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
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "main-ci.jsonl" if followup else WATCHDOG_LOG
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _escalate_to_jeff(issue: str, details: str) -> None:
    """Write issue to NEEDS_JEFF.md."""
    NEEDS_JEFF_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    now = datetime.now(timezone.utc)
    
    existing = ""
    if NEEDS_JEFF_FILE.is_file():
        existing = NEEDS_JEFF_FILE.read_text()
    
    entry = f"""## {now.strftime('%Y-%m-%d %H:%M UTC')} - Watchdog

**Issue:** {issue}

**Details:** {details}

**Action needed:** Manual review required

---

"""
    
    header = "# NEEDS_JEFF\n\nIssues requiring human intervention.\n\n---\n\n"
    if not existing.startswith("# NEEDS_JEFF"):
        existing = header + existing
    
    NEEDS_JEFF_FILE.write_text(entry + existing)
    logger.warning("Escalated to NEEDS_JEFF.md: %s", issue)


from .peer_endpoints import PeerURLs, PeerUnavailable

PEER_URLS = PeerURLs()


def _dashboard_alive(timeout: float = 3.0) -> bool:
    """An idle dashboard writes no logs; a live /health answer means it is not stuck."""
    import urllib.request
    port = os.environ.get("STORYPOOL_DASHBOARD_PORT", "8790")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def store_peer_engine(peer: str, data: dict) -> bool:
    """Keep the peer's self-reported running jobs and LLM usage for the local dashboard.

    Only the peer's own (source=local) section is kept, so data never echoes back.
    """
    jobs = (data.get("jobs") or {}).get(peer) if isinstance(data, dict) else None
    if not isinstance(jobs, dict) or jobs.get("source") != "local":
        return False
    snapshot = {"synced_at": datetime.now(timezone.utc).isoformat(), "machine": peer,
                "peer_timestamp": data.get("timestamp"), "control": data.get("control", {}),
                "guardian": data.get("machines", {}).get(peer, {}).get("guardian", {}),
                "jobs": dict(jobs, running=list(jobs.get("running") or [])[:50]),
                "llm_local": [g for g in (data.get("llm_local") or []) if isinstance(g, dict)][:20]}
    out = LOG_DIR / "machines" / peer / "engine.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot))
    tmp.replace(out)
    return True


def _peer_sync_status(peer, ok, failure_kind=None):
    path = LOG_DIR / 'machines' / peer / 'sync-status.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {'timestamp': _now_iso(), 'ok': ok,
             'detail': 'Peer sync verified' if ok else
                       'Peer sync failed; check private endpoint configuration and connectivity'}
    if failure_kind:
        value['failure_kind'] = failure_kind
        if failure_kind == 'address-unavailable':
            value['detail'] += '; peer address unavailable; configure private peers.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def sync_peer_health(machine: str) -> bool:
    """Copy the peer's self-reported health (over Tailscale) into machines/<peer>/watchdog.log.

    The row keeps the peer's own watchdog timestamp, so stale peers still show stale.
    """
    import urllib.request
    peer = "nucbox" if machine == "mini" else "mini"
    try:
        with urllib.request.urlopen(PEER_URLS[peer] + "/api/engine", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        store_peer_engine(peer, data)
        info = data.get("machines", {}).get(peer, {})
        if not info.get("timestamp"):
            _peer_sync_status(peer, False)
            return False
        row = {"timestamp": info["timestamp"], "machine": peer, "source": "peer-sync",
               "checks": [{"name": "peer_reported_health", "passed": info.get("state") != "red",
                           "message": str(info.get("detail", ""))[:200]}]}
        out = LOG_DIR / "machines" / peer / "watchdog.log"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a") as f:
            f.write(json.dumps(row) + "\n")
        _peer_sync_status(peer, True)
        return True
    except Exception as exc:
        _peer_sync_status(peer, False, "address-unavailable" if isinstance(exc, PeerUnavailable) else "transport-or-response")
        logger.warning("peer health sync for %s failed; check private endpoint configuration and connectivity", peer)
        return False


def check_service_stuck(service: str, log_path: Path) -> HealthCheck:
    """Check if a service has logged recently."""
    if service == "dashboard" and _dashboard_alive():
        return HealthCheck(name="dashboard_stuck", passed=True,
                           message="dashboard answering /health")
    if not log_path.exists():
        return HealthCheck(
            name=f"{service}_stuck", passed=True,
            message=f"Log file not found: {log_path}",
        )
    if log_path.is_dir():
        log_files = list(log_path.glob("*.jsonl")) + list(log_path.glob("*.log"))
        if not log_files:
            return HealthCheck(
                name=f"{service}_stuck", passed=True,
                message=f"No log files yet for {service}",
            )
        log_path = max(log_files, key=lambda p: p.stat().st_mtime)
    
    try:
        mtime = datetime.fromtimestamp(log_path.stat().st_mtime, tz=timezone.utc)
        age_minutes = (datetime.now(timezone.utc) - mtime).total_seconds() / 60
        
        threshold = 75 if service == "conductor" else STUCK_THRESHOLD_MINUTES
        if age_minutes > threshold:
            return HealthCheck(
                name=f"{service}_stuck",
                passed=False,
                message=f"{service} log not updated in {int(age_minutes)} minutes",
                can_repair=True,
                repair_action="restart_service",
            )
        
        return HealthCheck(
            name=f"{service}_stuck",
            passed=True,
            message=f"{service} last active {int(age_minutes)} min ago",
        )
    except OSError as e:
        return HealthCheck(
            name=f"{service}_stuck",
            passed=True,
            message=f"Could not check {service}: {e}",
        )


def check_tracebacks(service: str, log_path: Path) -> HealthCheck:
    """Check for repeated tracebacks in service logs."""
    if not log_path.exists():
        return HealthCheck(
            name=f"{service}_tracebacks",
            passed=True,
            message="No log file to check",
        )
    
    if log_path.is_dir():
        log_files = list(log_path.glob("*.jsonl")) + list(log_path.glob("*.log"))
        if not log_files:
            return HealthCheck(
                name=f"{service}_tracebacks",
                passed=True,
                message="No log files",
            )
        log_path = max(log_files, key=lambda p: p.stat().st_mtime)
    
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        traceback_count = 0
        
        with open(log_path, "r") as f:
            lines = f.readlines()[-500:]
            for line in lines:
                if "Traceback" in line or "Error:" in line or "Exception:" in line:
                    traceback_count += 1
        
        if traceback_count > MAX_TRACEBACKS_PER_HOUR:
            return HealthCheck(
                name=f"{service}_tracebacks",
                passed=False,
                message=f"{traceback_count} tracebacks/errors in recent logs",
                can_repair=True,
                repair_action="restart_service",
            )
        
        return HealthCheck(
            name=f"{service}_tracebacks",
            passed=True,
            message=f"{traceback_count} tracebacks in recent logs",
        )
    except OSError as e:
        return HealthCheck(
            name=f"{service}_tracebacks",
            passed=True,
            message=f"Could not check: {e}",
        )


def check_gh_in_path() -> HealthCheck:
    """Check if gh CLI is available."""
    gh_path = shutil.which("gh")
    if gh_path:
        return HealthCheck(
            name="gh_available",
            passed=True,
            message=f"gh found at {gh_path}",
        )
    
    common_paths = [
        "/opt/homebrew/bin/gh",
        "/usr/local/bin/gh",
        "/usr/bin/gh",
    ]
    for path in common_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return HealthCheck(
                name="gh_available",
                passed=False,
                message=f"gh found at {path} but not in PATH",
                can_repair=False,
                repair_action="fix_path",
            )
    
    return HealthCheck(
        name="gh_available",
        passed=False,
        message="gh CLI not found",
        can_repair=False,
    )


def check_codex_in_path() -> HealthCheck:
    """Check if codex CLI is available."""
    codex_path = shutil.which("codex")
    if codex_path:
        return HealthCheck(
            name="codex_available",
            passed=True,
            message=f"codex found at {codex_path}",
        )
    
    home = Path.home()
    common_paths = [
        home / "bin" / "codex",
        home / ".local" / "bin" / "codex",
        Path("/usr/local/bin/codex"),
    ]
    for path in common_paths:
        if path.is_file() and os.access(path, os.X_OK):
            return HealthCheck(
                name="codex_available",
                passed=True,
                message=f"codex found at {path} (may not be in PATH)",
            )
    
    machine = _get_machine()
    if machine == "mini":
        return HealthCheck(
            name="codex_available",
            passed=True,
            message="codex not local (Mini uses NucBox via SSH)",
        )
    
    return HealthCheck(
        name="codex_available",
        passed=False,
        message="codex CLI not found on NucBox",
        can_repair=False,
    )


def check_labels_exist(repo: str) -> HealthCheck:
    """Check if required labels exist in a repo."""
    try:
        result = subprocess.run(
            ["gh", "label", "list", "--repo", repo, "--json", "name"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return HealthCheck(
                name=f"labels_{repo.replace('/', '_')}",
                passed=True,
                message=f"Could not list labels: {result.stderr[:100]}",
            )
        
        labels = json.loads(result.stdout)
        label_names = {lbl.get("name", "") for lbl in labels}
        
        missing = [lbl for lbl in REQUIRED_LABELS if lbl not in label_names]
        
        if missing:
            return HealthCheck(
                name=f"labels_{repo.replace('/', '_')}",
                passed=False,
                message=f"Missing labels: {', '.join(missing)}",
                can_repair=True,
                repair_action="create_labels",
            )
        
        return HealthCheck(
            name=f"labels_{repo.replace('/', '_')}",
            passed=True,
            message="All required labels exist",
        )
    except (subprocess.SubprocessError, json.JSONDecodeError) as e:
        return HealthCheck(
            name=f"labels_{repo.replace('/', '_')}",
            passed=True,
            message=f"Could not check labels: {e}",
        )


def check_head_lag() -> HealthCheck:
    """Check if HEAD lags behind origin by more than 30 minutes.
    
    This detects when self-update is failing silently.
    """
    try:
        from .self_update import check_head_lag as _check_lag, UPDATE_LOG_FILE
    except ImportError:
        return HealthCheck(
            name="head_lag",
            passed=True,
            message="self_update module not available",
        )
    
    lag_info = _check_lag()
    
    if lag_info.get("error"):
        return HealthCheck(
            name="head_lag",
            passed=True,
            message=f"Could not check: {lag_info['error']}",
        )
    
    if not lag_info.get("lagging"):
        return HealthCheck(
            name="head_lag",
            passed=True,
            message=f"HEAD {lag_info['local_head']} is current",
        )
    
    commits_behind = lag_info.get("commits_behind", 0)
    
    last_update_time = None
    if UPDATE_LOG_FILE.is_file():
        try:
            lines = UPDATE_LOG_FILE.read_text().strip().split("\n")
            if lines:
                last_entry = json.loads(lines[-1])
                last_update_time = datetime.fromisoformat(
                    last_entry.get("timestamp", "").replace("Z", "+00:00")
                )
        except (json.JSONDecodeError, ValueError, OSError):
            pass
    
    lag_minutes = 0
    if last_update_time:
        lag_minutes = (datetime.now(timezone.utc) - last_update_time).total_seconds() / 60
    
    if commits_behind > 0 and lag_minutes > HEAD_LAG_ALERT_MINUTES:
        return HealthCheck(
            name="head_lag",
            passed=False,
            message=f"HEAD {lag_info['local_head']} is {commits_behind} commits behind origin/{lag_info['remote_head']} for {int(lag_minutes)}m",
            can_repair=False,
        )
    
    return HealthCheck(
        name="head_lag",
        passed=True,
        message=f"HEAD {lag_info['local_head']} ({commits_behind} commits behind, last check {int(lag_minutes)}m ago)",
    )


def check_install_stale() -> HealthCheck:
    """Check if install is behind origin."""
    install_dir = Path(os.environ.get("STORYPOOL_INSTALL_DIR", "~/storypool-install")).expanduser()
    
    if not install_dir.is_dir():
        return HealthCheck(
            name="install_stale",
            passed=True,
            message="Install dir not found",
        )
    
    try:
        result = subprocess.run(
            ["git", "fetch", "origin", "--dry-run"],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/cursor/storypool-all-in-one-47c7"],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        
        if result.returncode == 0:
            behind = int(result.stdout.strip() or "0")
            if behind > 0:
                return HealthCheck(
                    name="install_stale",
                    passed=False,
                    message=f"Install is {behind} commits behind origin",
                    can_repair=True,
                    repair_action="self_update",
                )
        
        return HealthCheck(
            name="install_stale",
            passed=True,
            message="Install is up to date",
        )
    except (subprocess.SubprocessError, ValueError) as e:
        return HealthCheck(
            name="install_stale",
            passed=True,
            message=f"Could not check: {e}",
        )


def repair_restart_service(service: str, machine: str) -> bool:
    """Restart a stuck service."""
    try:
        if machine == "mini":
            label = f"com.storypool.{service}"
            if service == "poller":
                label = "com.storypool.karen-poller"
            
            subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"],
                capture_output=True,
                timeout=30,
            )
            logger.info("Restarted %s via launchctl", service)
            return True
        else:
            unit = f"storypool-{service}"
            subprocess.run(
                ["systemctl", "--user", "restart", f"{unit}.service"],
                capture_output=True,
                timeout=30,
            )
            logger.info("Restarted %s via systemctl", service)
            return True
    except subprocess.SubprocessError as e:
        logger.error("Failed to restart %s: %s", service, e)
        return False


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


def run_watchdog(machine: Optional[str] = None) -> WatchdogResult:
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

    for service, log_path in SERVICE_LOG_FILES.items():
        if service == "poller" and machine == "nucbox":
            continue
        
        stuck_check = check_service_stuck(service, log_path)
        result.checks.append(stuck_check)
        
        traceback_check = check_tracebacks(service, log_path)
        result.checks.append(traceback_check)
    
    result.checks.append(check_gh_in_path())
    result.checks.append(check_codex_in_path())
    
    sample_repos = ["rupret007/webjam", "rupret007/bob-ops-dashboard"]
    for repo in sample_repos[:1]:
        result.checks.append(check_labels_exist(repo))
    
    result.checks.append(check_install_stale())
    result.checks.append(check_head_lag())
    
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
    
    args = parser.parse_args()
    
    result = run_watchdog(machine=args.machine)
    sync_peer_health(result.machine)
    if not args.check_only:
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
    
    return 0 if all(c.passed or c.repaired for c in result.checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
