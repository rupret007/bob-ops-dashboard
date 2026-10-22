"""Machine health guards for StoryPool control plane.

Guards are deterministic checks that prevent work from being dispatched
to machines that are overloaded or unhealthy. No LLM calls.
"""

from __future__ import annotations

import os
import shutil
import platform
import subprocess
import re
from dataclasses import dataclass
from typing import Callable, Optional

MINI_LOAD_LIMIT = 10.0
MINI_SWAP_LIMIT_PCT = 80.0
MINI_DISK_MIN_GB = 15.0
NUCBOX_LOAD_LIMIT = 12.0
NUCBOX_MAX_LANES = 7


@dataclass
class GuardResult:
    """Result of a guard check."""

    passed: bool
    reason: str
    value: Optional[float] = None
    limit: Optional[float] = None


def get_load1() -> float:
    """Get 1-minute load average."""
    load1, _, _ = os.getloadavg()
    return load1


def get_swap_pct() -> float:
    """Get swap usage percentage (Linux/macOS)."""
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(["/usr/sbin/sysctl", "-n", "vm.swapusage"],
                                    capture_output=True, text=True, timeout=5, check=True)
            values = dict(re.findall(r"(total|used)\s*=\s*([0-9.]+)M", result.stdout))
            total, used = float(values["total"]), float(values["used"])
            return used / total * 100 if total else 0.0
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            return 100.0  # Unknown telemetry cannot grant a Mini lane.
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            vals: dict[str, int] = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0].endswith(":"):
                    key = parts[0][:-1]
                    try:
                        vals[key] = int(parts[1])
                    except ValueError:
                        pass
            total = vals.get("SwapTotal", 0)
            free = vals.get("SwapFree", 0)
            if total > 0:
                return ((total - free) / total) * 100
    except OSError:
        pass
    return 0.0


def get_free_disk_gb(path: str = "/") -> float:
    """Get free disk space in GB."""
    usage = shutil.disk_usage(path)
    return usage.free / (1024**3)


class MachineGuards:
    """Guard checks for a specific machine.

    Example:
        guards = MachineGuards("mini")
        result = guards.check_all()
        if not result.passed:
            print(f"Guard failed: {result.reason}")
    """

    def __init__(
        self,
        machine: str,
        load_fn: Optional[Callable[[], float]] = None,
        swap_fn: Optional[Callable[[], float]] = None,
        disk_fn: Optional[Callable[[], float]] = None,
        lane_count_fn: Optional[Callable[[], int]] = None,
    ):
        self.machine = machine.lower()
        self._load_fn = load_fn or get_load1
        self._swap_fn = swap_fn or get_swap_pct
        self._disk_fn = disk_fn or get_free_disk_gb
        self._lane_count_fn = lane_count_fn or (lambda: 0)

    def check_load(self) -> GuardResult:
        """Check if load is within limits."""
        load = self._load_fn()
        if self.machine == "mini":
            limit = MINI_LOAD_LIMIT
        elif self.machine == "nucbox":
            limit = NUCBOX_LOAD_LIMIT
        else:
            limit = MINI_LOAD_LIMIT

        if load >= limit:
            return GuardResult(
                passed=False,
                reason=f"load {load:.1f} >= {limit}",
                value=load,
                limit=limit,
            )
        return GuardResult(
            passed=True, reason="ok", value=load, limit=limit
        )

    def check_swap(self) -> GuardResult:
        """Check if swap usage is within limits (Mini only)."""
        if self.machine != "mini":
            return GuardResult(passed=True, reason="swap check not applicable")

        pct = self._swap_fn()
        if pct >= MINI_SWAP_LIMIT_PCT:
            return GuardResult(
                passed=False,
                reason=f"swap {pct:.1f}% >= {MINI_SWAP_LIMIT_PCT}%",
                value=pct,
                limit=MINI_SWAP_LIMIT_PCT,
            )
        return GuardResult(
            passed=True, reason="ok", value=pct, limit=MINI_SWAP_LIMIT_PCT
        )

    def check_disk(self) -> GuardResult:
        """Check if free disk is above minimum (Mini only)."""
        if self.machine != "mini":
            return GuardResult(passed=True, reason="disk check not applicable")

        free_gb = self._disk_fn()
        if free_gb < MINI_DISK_MIN_GB:
            return GuardResult(
                passed=False,
                reason=f"disk {free_gb:.1f}GB < {MINI_DISK_MIN_GB}GB",
                value=free_gb,
                limit=MINI_DISK_MIN_GB,
            )
        return GuardResult(
            passed=True, reason="ok", value=free_gb, limit=MINI_DISK_MIN_GB
        )

    def check_lanes(self) -> GuardResult:
        """Check if lane count is within limits (NucBox only)."""
        if self.machine != "nucbox":
            return GuardResult(passed=True, reason="lane check not applicable")

        count = self._lane_count_fn()
        if count >= NUCBOX_MAX_LANES:
            return GuardResult(
                passed=False,
                reason=f"lanes {count} >= {NUCBOX_MAX_LANES}",
                value=float(count),
                limit=float(NUCBOX_MAX_LANES),
            )
        return GuardResult(
            passed=True, reason="ok", value=float(count), limit=float(NUCBOX_MAX_LANES)
        )

    def check_all(self) -> GuardResult:
        """Run all applicable guards and return first failure or success."""
        checks = [
            self.check_load,
            self.check_swap,
            self.check_disk,
            self.check_lanes,
        ]
        for check in checks:
            result = check()
            if not result.passed:
                return result
        return GuardResult(passed=True, reason="all guards passed")


def mini_guards_pass(
    load_fn: Optional[Callable[[], float]] = None,
    swap_fn: Optional[Callable[[], float]] = None,
    disk_fn: Optional[Callable[[], float]] = None,
) -> tuple[bool, str]:
    """Convenience function: check Mini guards."""
    guards = MachineGuards("mini", load_fn=load_fn, swap_fn=swap_fn, disk_fn=disk_fn)
    result = guards.check_all()
    return result.passed, result.reason


def nucbox_guards_pass(
    load_fn: Optional[Callable[[], float]] = None,
    lane_count_fn: Optional[Callable[[], int]] = None,
) -> tuple[bool, str]:
    """Convenience function: check NucBox guards."""
    guards = MachineGuards("nucbox", load_fn=load_fn, lane_count_fn=lane_count_fn)
    result = guards.check_all()
    return result.passed, result.reason
