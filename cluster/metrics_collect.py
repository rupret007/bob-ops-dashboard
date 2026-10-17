"""Host metrics for cluster workers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any

COUNTERS: dict[str, int] = {
    "jobs_done_total": 0,
    "test_runs_total": 0,
}


def bump_jobs_done() -> None:
    COUNTERS["jobs_done_total"] += 1


def bump_test_runs() -> None:
    COUNTERS["test_runs_total"] += 1


def _read_mem_linux() -> tuple[float, float, float]:
    mem_total_kb = mem_avail_kb = swap_total_kb = swap_free_kb = 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_avail_kb = int(line.split()[1])
                elif line.startswith("SwapTotal:"):
                    swap_total_kb = int(line.split()[1])
                elif line.startswith("SwapFree:"):
                    swap_free_kb = int(line.split()[1])
    except OSError:
        pass
    mem_total_gb = mem_total_kb / (1024 * 1024)
    mem_used_gb = max(0.0, (mem_total_kb - mem_avail_kb) / (1024 * 1024))
    swap_used_gb = max(0.0, (swap_total_kb - swap_free_kb) / (1024 * 1024))
    return mem_used_gb, mem_total_gb, swap_used_gb


def _cpu_pct_linux() -> float:
    try:
        with open("/proc/stat", encoding="utf-8") as f:
            line = f.readline()
        parts = [int(x) for x in line.split()[1:]]
        total = sum(parts)
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        key = "_cpu_sample"
        prev = getattr(_cpu_pct_linux, key, None)
        setattr(_cpu_pct_linux, key, (total, idle, time.time()))
        if prev is None:
            time.sleep(0.05)
            return _cpu_pct_linux()
        ptotal, pidle, _ = prev
        dt = total - ptotal
        if dt <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (1.0 - (idle - pidle) / dt)))
    except (OSError, ValueError, IndexError):
        return 0.0


def _mem_macos_vm_stat() -> tuple[float, float, float]:
    mem_used_gb = mem_total_gb = swap_used_gb = 0.0
    try:
        out = subprocess.check_output(["vm_stat"], text=True, timeout=2)
        page_size = 4096
        free_pages = active = inactive = wired = 0
        for line in out.splitlines():
            if "page size of" in line:
                page_size = int(line.split()[-2])
            elif line.startswith("Pages free:"):
                free_pages = int(line.split()[-1].rstrip("."))
            elif line.startswith("Pages active:"):
                active = int(line.split()[-1].rstrip("."))
            elif line.startswith("Pages inactive:"):
                inactive = int(line.split()[-1].rstrip("."))
            elif line.startswith("Pages wired down:"):
                wired = int(line.split()[-1].rstrip("."))
        used = (active + wired) * page_size
        total = (active + inactive + wired + free_pages) * page_size
        mem_used_gb = used / (1024**3)
        mem_total_gb = total / (1024**3) if total else 8.0
    except (subprocess.SubprocessError, ValueError, OSError):
        mem_total_gb = 8.0
    return mem_used_gb, mem_total_gb, swap_used_gb


def collect_metrics(
    node_id: str,
    active_jobs: int,
    light: bool = False,
) -> dict[str, Any]:
    load1, load5 = os.getloadavg()[:2]
    disk = shutil.disk_usage(os.path.expanduser("~"))
    disk_free_gb = disk.free / (1024**3)

    if light:
        mem_used_gb, mem_total_gb, swap_used_gb = _mem_macos_vm_stat()
        cpu_pct = 0.0
    else:
        mem_used_gb, mem_total_gb, swap_used_gb = _read_mem_linux()
        cpu_pct = _cpu_pct_linux()

    return {
        "node_id": node_id,
        "grok_bot": {"label": "Grok Bot (conserve-only)", "status": "unknown"},
        "ts": time.time(),
        "cpu_pct": round(cpu_pct, 2),
        "load1": round(load1, 3),
        "load5": round(load5, 3),
        "mem_used_gb": round(mem_used_gb, 3),
        "mem_total_gb": round(mem_total_gb, 3),
        "swap_used_gb": round(swap_used_gb, 3),
        "disk_free_gb": round(disk_free_gb, 3),
        "active_jobs": active_jobs,
        "jobs_done_total": COUNTERS["jobs_done_total"],
        "test_runs_total": COUNTERS["test_runs_total"],
    }


def metrics_subject(node_id: str) -> str:
    return f"metrics.{node_id}"


def to_node_v1(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "v": "node/v1",
        "node_id": m["node_id"],
        "ts": m["ts"],
        "cpu_pct": m["cpu_pct"],
        "load1": m["load1"],
        "load5": m["load5"],
        "mem_used_gb": m["mem_used_gb"],
        "mem_total_gb": m["mem_total_gb"],
        "swap_used_gb": m["swap_used_gb"],
        "disk_free_gb": m["disk_free_gb"],
        "lanes": [],
        "loc_per_min": 0,
        "test_runs_per_min": 0,
        "actions_per_min": 0,
        "online": True,
    }


def post_node_v1(url: str, payload: dict[str, Any]) -> None:
    import urllib.error
    import urllib.request

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except (urllib.error.URLError, OSError):
        pass
