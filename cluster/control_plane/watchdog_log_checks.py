"""Watchdog implementation; the public facade supplies current runtime bindings."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .watchdog import HealthCheck


def check_service_stuck(context, service: str, log_path: Path) -> HealthCheck:
    """Check if a service has logged recently."""
    if service == "dashboard" and context._dashboard_alive():
        return context.HealthCheck(name="dashboard_stuck", passed=True,
                           message="dashboard answering /health")
    if not log_path.exists():
        return context.HealthCheck(
            name=f"{service}_stuck", passed=True,
            message=f"Log file not found: {log_path}",
        )
    if log_path.is_dir():
        log_files = list(log_path.glob("*.jsonl")) + list(log_path.glob("*.log"))
        if not log_files:
            return context.HealthCheck(
                name=f"{service}_stuck", passed=True,
                message=f"No log files yet for {service}",
            )
        log_path = max(log_files, key=lambda p: p.stat().st_mtime)

    try:
        mtime = context.datetime.fromtimestamp(log_path.stat().st_mtime, tz=context.timezone.utc)
        age_minutes = (context.datetime.now(context.timezone.utc) - mtime).total_seconds() / 60

        threshold = 75 if service == "conductor" else context.STUCK_THRESHOLD_MINUTES
        if age_minutes > threshold:
            return context.HealthCheck(
                name=f"{service}_stuck",
                passed=False,
                message=f"{service} log not updated in {int(age_minutes)} minutes",
                can_repair=True,
                repair_action="restart_service",
            )

        return context.HealthCheck(
            name=f"{service}_stuck",
            passed=True,
            message=f"{service} last active {int(age_minutes)} min ago",
        )
    except OSError as e:
        return context.HealthCheck(
            name=f"{service}_stuck",
            passed=True,
            message=f"Could not check {service}: {e}",
        )


def check_tracebacks(context, service: str, log_path: Path) -> HealthCheck:
    """Check for repeated tracebacks in service logs."""
    if not log_path.exists():
        return context.HealthCheck(
            name=f"{service}_tracebacks",
            passed=True,
            message="No log file to check",
        )

    if log_path.is_dir():
        log_files = list(log_path.glob("*.jsonl")) + list(log_path.glob("*.log"))
        if not log_files:
            return context.HealthCheck(
                name=f"{service}_tracebacks",
                passed=True,
                message="No log files",
            )
        log_path = max(log_files, key=lambda p: p.stat().st_mtime)

    try:
        cutoff = context.datetime.now(context.timezone.utc) - context.timedelta(hours=1)
        traceback_count = 0

        with open(log_path, "r") as f:
            # Quiet services can retain historical, untimestamped stderr for days.
            # An unchanged file cannot contain a new failure in this hour. Fresh
            # or future-dated logs still use the existing conservative threshold.
            if context.os.fstat(f.fileno()).st_mtime < cutoff.timestamp():
                return context.HealthCheck(
                    name=f"{service}_tracebacks",
                    passed=True,
                    message="0 new tracebacks: log unchanged for more than one hour",
                    can_repair=False,
                )
            lines = context.deque(f, maxlen=500)
            for line in lines:
                if "Traceback" in line or "Error:" in line or "Exception:" in line:
                    traceback_count += 1

        if traceback_count > context.MAX_TRACEBACKS_PER_HOUR:
            return context.HealthCheck(
                name=f"{service}_tracebacks",
                passed=False,
                message=f"{traceback_count} tracebacks/errors in recent logs",
                can_repair=True,
                repair_action="restart_service",
            )

        return context.HealthCheck(
            name=f"{service}_tracebacks",
            passed=True,
            message=f"{traceback_count} tracebacks in recent logs",
        )
    except OSError as e:
        return context.HealthCheck(
            name=f"{service}_tracebacks",
            passed=True,
            message=f"Could not check: {e}",
        )
