"""Machine health guards for StoryPool control plane.

Guards are deterministic checks that prevent work from being dispatched
to machines that are overloaded or unhealthy. No LLM calls.
"""

from __future__ import annotations

import math
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

MINI_LOAD_LIMIT = 10.0
MINI_SWAP_LIMIT_PCT = 80.0
MINI_DISK_MIN_GB = 15.0
MINI_MAX_LANES = 6
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
        result = subprocess.run(["sysctl", "-n", "vm.swapusage"],
                                capture_output=True, text=True, timeout=5, check=True)
        values = dict(re.findall(r"(total|used)\s*=\s*([\d.]+[KMGT]?)", result.stdout))

        def size(value):
            match = re.fullmatch(r"([\d.]+)([KMGT]?)", value)
            if not match:
                raise ValueError("Invalid swap telemetry")
            multiplier = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
            return float(match[1]) * multiplier[match[2]]

        total, used = size(values["total"]), size(values["used"])
        if not math.isfinite(total) or not math.isfinite(used) or not 0 <= used <= total:
            raise ValueError("Invalid swap telemetry")
        return used / total * 100 if total > 0 else 0.0
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
            total = vals["SwapTotal"]
            free = vals["SwapFree"]
            if total < 0 or not 0 <= free <= total:
                raise ValueError("Invalid swap counters")
            if total > 0:
                return ((total - free) / total) * 100
    except OSError as error:
        raise ValueError("Swap telemetry unavailable") from error
    return 0.0


def get_lane_count() -> int:
    """Count live coding screens, excluding service routines."""
    from live.agent import parse_screen_ls

    result = subprocess.run(["screen", "-ls"], capture_output=True, text=True, timeout=5)
    if result.returncode not in (0, 1):
        raise ValueError("Lane telemetry unavailable")
    if result.returncode == 1 and "No Sockets found" not in result.stdout and not parse_screen_ls(result.stdout):
        raise ValueError("Lane telemetry unavailable")
    return len(parse_screen_ls(result.stdout))


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
        self._lane_count_fn = lane_count_fn or get_lane_count
        self.values: dict[str, float | None] = {}

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
        """Check the number of occupied coding lanes."""
        count = self._lane_count_fn()
        limit = NUCBOX_MAX_LANES if self.machine == "nucbox" else MINI_MAX_LANES
        if type(count) is not int or count < 0:
            return GuardResult(passed=False, reason="Invalid lane telemetry")
        if count >= limit:
            return GuardResult(
                passed=False,
                reason=f"lanes {count} >= {limit}",
                value=float(count),
                limit=float(limit),
            )
        return GuardResult(
            passed=True, reason="ok", value=float(count), limit=float(limit)
        )

    def check_all(self) -> GuardResult:
        """Run all applicable guards and return first failure or success."""
        if self.machine not in ("mini", "nucbox"):
            return GuardResult(passed=False, reason="Unknown machine")
        checks = [("load", self.check_load), ("swap", self.check_swap),
                  ("disk", self.check_disk)]
        if self.machine == "nucbox":
            checks.append(("lanes", self.check_lanes))
        self.values = {}
        for name, check in checks:
            try:
                result = check()
                self.values[name] = result.value
                if result.value is not None and (type(result.value) not in (int, float)
                        or not math.isfinite(result.value) or result.value < 0):
                    return GuardResult(passed=False, reason="Invalid machine telemetry")
            except (OSError, ValueError, TypeError, OverflowError, KeyError, subprocess.SubprocessError):
                return GuardResult(passed=False, reason="Machine telemetry unavailable")
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
