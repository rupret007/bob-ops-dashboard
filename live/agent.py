#!/usr/bin/env python3
"""Node metrics agent — one per machine. Stdlib only."""
from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from live.gate_parse import parse_progress_text

LANE_PREFIXES = ("lane-", "nlane-", "grok-", "resume-")
ROUTINE_PREFIXES = ("svc-",)
SCREEN_SESSION = re.compile(r"\d+\.([^\s(]+)\s*\(", re.IGNORECASE)
LOG_PREFIXES = ("nlane-", "lane-", "grok-", "resume-")
CLAIMED_PATH = re.compile(r"CLAIMED\s+(gate-\S+)", re.IGNORECASE)
CLAIM_RC_LINE = re.compile(r"CLAIM\s+rc[=:\s]+(\S+)", re.IGNORECASE)
TEST_LINE = re.compile(
    r"(npm test|pytest|unittest|playwright|nuc-verify|PASS|FAIL)", re.IGNORECASE
)
ACTION_LINE = re.compile(
    r"(tool_call|Tool:|invoke_tool|function_call)", re.IGNORECASE
)
HOST_LINE = re.compile(r"HOST[=:\s]+(\S+)", re.IGNORECASE)
RESULT_LINE = re.compile(r"RESULT[=:\s]+(\S+)", re.IGNORECASE)
EXIT_LINE = re.compile(r"EXIT[=:\s]+(\d+)", re.IGNORECASE)


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(path))


def load_nodes_config(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_vm_stat(text: str) -> dict:
    """Parse macOS vm_stat output into approximate memory stats (bytes)."""
    page_size = 4096
    pages: dict[str, int] = {}
    for line in text.splitlines():
        m = re.match(r"^([^:]+):\s+(\d+)", line.strip())
        if m:
            pages[m.group(1).strip()] = int(m.group(2))
    free = pages.get("Pages free", 0) * page_size
    inactive = pages.get("Pages inactive", 0) * page_size
    active = pages.get("Pages active", 0) * page_size
    wired = pages.get("Pages wired down", pages.get("Pages wired", 0)) * page_size
    compressed = pages.get("Pages occupied by compressor", 0) * page_size
    used = active + wired + compressed
    total_est = used + free + inactive
    return {
        "mem_total_bytes": total_est,
        "mem_used_bytes": used,
        "mem_free_bytes": free + inactive,
    }


def parse_meminfo(text: str) -> dict:
    vals: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":"):
            key = parts[0][:-1]
            try:
                vals[key] = int(parts[1]) * 1024
            except ValueError:
                pass
    total = vals.get("MemTotal", 0)
    avail = vals.get("MemAvailable", vals.get("MemFree", 0))
    swap_total = vals.get("SwapTotal", 0)
    swap_free = vals.get("SwapFree", 0)
    return {
        "mem_total_bytes": total,
        "mem_used_bytes": max(0, total - avail),
        "mem_free_bytes": avail,
        "swap_total_bytes": swap_total,
        "swap_used_bytes": max(0, swap_total - swap_free),
    }


def _screen_session_name(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("There") or "Sockets" in stripped:
        return None
    m = SCREEN_SESSION.search(stripped)
    if not m:
        return None
    return m.group(1)


def lane_log_stem(screen_name: str) -> str:
    for prefix in LOG_PREFIXES:
        if screen_name.startswith(prefix):
            return screen_name[len(prefix) :]
    return screen_name



ACTIVE_WORKTREE_MAX_AGE_SEC = int(os.environ.get("LIVE_WORKTREE_MAX_AGE_SEC", "1800"))


def active_worktrees(roots, now=None, max_age=ACTIVE_WORKTREE_MAX_AGE_SEC):
    """Git worktrees directly under each root (or the root itself) touched recently.

    Lane roots like ~/lane-wt hold one checkout per lane, so `git -C root` alone
    is not a repo. Only recently modified checkouts are scanned to keep it cheap.
    """
    now = time.time() if now is None else now
    found = []
    for root in roots:
        try:
            if not root.is_dir():
                continue
            cands = [root] if (root / ".git").exists() else [c for c in root.iterdir() if c.is_dir()]
        except OSError:
            continue
        for c in cands:
            try:
                g = c / ".git"
                if not g.exists():
                    continue
                stamps = [c.stat().st_mtime, g.stat().st_mtime]
                if g.is_dir():
                    for sub in ("index", "HEAD", "logs/HEAD"):
                        try:
                            stamps.append((g / sub).stat().st_mtime)
                        except OSError:
                            pass
                if now - max(stamps) <= max_age:
                    found.append(c)
            except OSError:
                continue
    return found


def read_head_sha(root):
    """Resolve HEAD by reading files (no git subprocess); handles worktrees and packed refs."""
    try:
        g = root / ".git"
        gitdir = g
        if g.is_file():
            ref = g.read_text().strip()
            if ref.startswith("gitdir:"):
                gitdir = (root / ref.split(":", 1)[1].strip()).resolve()
        head = (gitdir / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return head
        name = head.split(":", 1)[1].strip()
        common = gitdir
        cd = gitdir / "commondir"
        if cd.is_file():
            common = (gitdir / cd.read_text().strip()).resolve()
        for base in (gitdir, common):
            f = base / name
            if f.is_file():
                return f.read_text().strip()
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                if line.endswith(" " + name):
                    return line.split(" ", 1)[0]
    except OSError:
        return None
    return None


def committed_delta(head_prev, root):
    """Lines changed by commits since the last sample (keeps LOC when a lane commits)."""
    head = read_head_sha(root)
    if not head:
        return 0, 0
    prev = head_prev.get(str(root))
    head_prev[str(root)] = head
    if not prev or prev == head:
        return 0, 0
    try:
        out = subprocess.check_output(["git", "-C", str(root), "diff", "--numstat", prev, head],
                                      text=True, timeout=30, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return 0, 0
    a = d = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            a += int(parts[0]); d += int(parts[1])
    return a, d

def runs_on_for_session(screen_name: str, node_id: str) -> str:
    if screen_name.startswith("nlane-"):
        return "nuc"
    if screen_name.startswith(("lane-", "grok-", "resume-")):
        return "mini"
    return node_id


def parse_screen_ls(text: str) -> list[str]:
    """Active lane screen names (excludes svc-* routines)."""
    names: list[str] = []
    for line in text.splitlines():
        name = _screen_session_name(line)
        if not name:
            continue
        if any(name.startswith(p) for p in LANE_PREFIXES):
            names.append(name)
    return names


def parse_screen_routines(text: str) -> list[str]:
    names: list[str] = []
    for line in text.splitlines():
        name = _screen_session_name(line)
        if name and any(name.startswith(p) for p in ROUTINE_PREFIXES):
            names.append(name)
    return names


def repo_from_gate_path(path: str) -> str:
    body = path[5:] if path.startswith("gate-") else path
    bits = [b for b in body.split("-") if b]
    if len(bits) >= 4:
        return "-".join(bits[:-2])
    if len(bits) == 3:
        return "-".join(bits[:-1])
    if bits:
        return bits[0]
    return ""


def parse_lane_log_tail(tail: str) -> dict:
    """Structured lane fields from log tail; no free-text copies."""
    out: dict = {"repo": "", "status": "running"}
    repo_m = CLAIMED_PATH.search(tail)
    if repo_m:
        out["repo"] = repo_from_gate_path(repo_m.group(1))
    last_kind = None
    for line in tail.splitlines():
        rm = RESULT_LINE.search(line)
        if rm:
            val = rm.group(1).strip().lower()
            if val in ("running", "done", "blocked", "queued"):
                out["status"] = val
            else:
                out["status"] = val
            last_kind = "result"
            continue
        em = EXIT_LINE.search(line)
        if em:
            out["status"] = "done" if em.group(1) == "0" else "blocked"
            last_kind = "exit"
            continue
        if HOST_LINE.search(line):
            if last_kind is None:
                out["status"] = "running"
            last_kind = "host"
            continue
        cm = CLAIM_RC_LINE.search(line)
        if cm:
            rc = cm.group(1).strip().lower()
            if rc in ("0", "ok", "success"):
                out["status"] = "running"
            elif rc in ("queued", "queue", "wait"):
                out["status"] = "queued"
            else:
                out["status"] = "blocked"
            last_kind = "claim"
    return out


def parse_swapusage(text: str) -> dict:
    m = re.search(
        r"total\s*=\s*([\d.]+)([KMGT]?i?B?).*used\s*=\s*([\d.]+)",
        text,
        re.IGNORECASE,
    )
    if not m:
        return {"swap_total_bytes": 0, "swap_used_bytes": 0}

    def to_bytes(val: str, unit: str) -> int:
        u = unit.upper().replace("IB", "B").replace("I", "")
        mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}.get(u, 1)
        return int(float(val) * mult)

    total = to_bytes(m.group(1), m.group(2) or "B")
    used = to_bytes(m.group(3), m.group(2) or "B")
    return {"swap_total_bytes": total, "swap_used_bytes": used}


class LinuxCpuSampler:
    def __init__(self):
        self._prev = None

    def sample(self) -> float:
        try:
            with open("/proc/stat", encoding="utf-8") as f:
                line = f.readline()
        except OSError:
            return 0.0
        parts = line.split()
        if parts[0] != "cpu" or len(parts) < 8:
            return 0.0
        nums = [int(x) for x in parts[1:8]]
        total = sum(nums)
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
        if self._prev is None:
            self._prev = (total, idle)
            return 0.0
        pt, pi = self._prev
        dt = total - pt
        di = idle - pi
        self._prev = (total, idle)
        if dt <= 0:
            return 0.0
        return min(100.0, max(0.0, 100.0 * (1.0 - di / dt)))


class Agent:
    def __init__(self, args):
        self.node_id = args.node_id
        self.push_url = args.push
        self.interval = args.interval
        self.lane_log_glob = os.environ.get(
            "BOB_LANE_LOG_GLOB", str(_expand("~/lane-wt/logs/*.log"))
        )
        wt_roots = os.environ.get(
            "BOB_WORKTREE_ROOTS", "~/lane-wt ~/lane-wt.noindex ~/work"
        )
        self.worktree_roots = [_expand(p) for p in wt_roots.split()]
        self.progress_glob = os.environ.get(
            "BOB_PROGRESS_GLOB",
            str(_expand("~/Documents/bob-overnight-inject/jobs/*/progress.md")),
        )
        self.ai_capacity_path = _expand(
            os.environ.get(
                "BOB_AI_CAPACITY",
                "~/Documents/bob-overnight-inject/sync/ai-capacity.json",
            )
        )
        cfg_path = Path(__file__).resolve().parent / "nodes.json"
        nodes = load_nodes_config(cfg_path)
        self.node_meta = next((n for n in nodes if n["id"] == self.node_id), {})
        self.max_lanes = int(self.node_meta.get("max_lanes", 6))
        self.is_linux = platform.system() == "Linux"
        self._linux_cpu = LinuxCpuSampler() if self.is_linux else None
        self._ncpu = os.cpu_count() or 4
        self._loc_prev: dict[str, tuple[int, int]] = {}
        self._head_prev: dict = {}
        self._commit_carry: dict = {}
        self._loc_last_sample = 0.0
        self._loc_per_min = 0.0
        self._log_offsets: dict[str, int] = {}
        self._test_runs_per_min = 0.0
        self._actions_per_min = 0.0
        self._test_window: list[tuple[float, int]] = []
        self._action_window: list[tuple[float, int]] = []
        self._disk = {}
        self._lanes: list[dict] = []
        self._routines: list[dict] = []
        self._gates: list[dict] = []
        self._ai_capacity = []
        self._mem = {}
        self._swap = {}
        self._load1 = 0.0
        self._load5 = 0.0
        self._cpu_pct = 0.0
        self._cpu_note = "meas"
        self._backoff = 2.0
        self._start_time = time.time()
        self._last_slow_mem = 0.0
        self._last_disk = 0.0
        self._last_lanes = 0.0
        self._last_loc = 0.0
        self._last_extras = 0.0

    def _read_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def fast_tick(self):
        self._load1, self._load5, _ = os.getloadavg()
        if self.is_linux and self._linux_cpu:
            self._cpu_pct = self._linux_cpu.sample()
            self._cpu_note = "meas"
        else:
            self._cpu_pct = min(100.0, (self._load1 / self._ncpu) * 100.0)
            self._cpu_note = "est"
        now = time.time()
        if now - self._last_slow_mem >= 10.0:
            self._sample_memory()
            self._last_slow_mem = now

    def _sample_memory(self):
        if self.is_linux:
            try:
                text = Path("/proc/meminfo").read_text(encoding="utf-8")
                parsed = parse_meminfo(text)
                self._mem = {
                    "total": parsed["mem_total_bytes"],
                    "used": parsed["mem_used_bytes"],
                }
                self._swap = {
                    "total": parsed["swap_total_bytes"],
                    "used": parsed["swap_used_bytes"],
                }
            except OSError:
                pass
        else:
            try:
                vm = subprocess.check_output(["vm_stat"], text=True, timeout=5)
                parsed = parse_vm_stat(vm)
                self._mem = {
                    "total": parsed["mem_total_bytes"],
                    "used": parsed["mem_used_bytes"],
                }
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                sw = subprocess.check_output(
                    ["sysctl", "-n", "vm.swapusage"], text=True, timeout=5
                )
                sp = parse_swapusage(sw)
                self._swap = {"total": sp["swap_total_bytes"], "used": sp["swap_used_bytes"]}
            except (OSError, subprocess.SubprocessError):
                self._swap = {"total": 0, "used": 0}

    def slow_disk(self):
        try:
            usage = shutil.disk_usage("/")
            self._disk = {
                "path": "/",
                "free_gb": round(usage.free / (1024**3), 2),
                "total_gb": round(usage.total / (1024**3), 2),
            }
        except OSError:
            self._disk = {}

    def slow_lanes(self):
        lanes: list[dict] = []
        routines: list[dict] = []
        screen_text = ""
        try:
            # macOS `screen -ls` exits 1 even when sessions exist; ignore rc.
            screen_text = subprocess.run(
                ["screen", "-ls"], text=True, timeout=10,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            ).stdout or ""
        except (OSError, subprocess.SubprocessError):
            screen_text = ""
        lane_names = parse_screen_ls(screen_text)
        routine_names = parse_screen_routines(screen_text)
        log_dir = Path(self.lane_log_glob).parent
        for name in lane_names:
            stem = lane_log_stem(name)
            lane = {
                "name": name,
                "repo": "",
                "task": "",
                "status": "running",
                "runs_on": runs_on_for_session(name, self.node_id),
            }
            log_path = log_dir / f"{stem}.log"
            if log_path.is_file():
                tail = self._read_file(log_path)[-4000:]
                parsed = parse_lane_log_tail(tail)
                lane["repo"] = parsed.get("repo") or ""
                lane["status"] = parsed.get("status") or "running"
                prompt = log_dir / f"{stem}.prompt"
                if prompt.is_file():
                    lane["started_at"] = prompt.stat().st_mtime
            lanes.append(lane)
        for name in routine_names:
            stem = lane_log_stem(name)
            routine = {"name": name, "status": "running"}
            log_path = log_dir / f"{stem}.log"
            if log_path.is_file():
                parsed = parse_lane_log_tail(self._read_file(log_path)[-4000:])
                routine["status"] = parsed.get("status") or "running"
            routines.append(routine)
        self._lanes = lanes
        self._routines = routines

    def slow_loc(self):
        added, deleted = 0, 0
        for root in active_worktrees(self.worktree_roots):
            try:
                out = subprocess.check_output(
                    ["git", "-C", str(root), "diff", "--numstat", "HEAD"],
                    text=True, timeout=30, stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            a, d = committed_delta(self._head_prev, root)
            a, d = a + self._commit_carry.get(str(root), (0, 0))[0], d + self._commit_carry.get(str(root), (0, 0))[1]
            self._commit_carry[str(root)] = (a, d)
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2 and parts[0] != "-" and parts[1] != "-":
                    try:
                        a += int(parts[0])
                        d += int(parts[1])
                    except ValueError:
                        pass
            key = str(root)
            prev = self._loc_prev.get(key)
            if prev:
                da, dd = a - prev[0], d - prev[1]
                if da >= 0:
                    added += da
                if dd >= 0:
                    deleted += dd
            self._loc_prev[key] = (a, d)
        delta = added + deleted
        delta = min(delta, 5000)
        now = time.time()
        elapsed = max(now - self._loc_last_sample, self.interval) if self._loc_last_sample else self.interval
        self._loc_last_sample = now
        per_min = (delta / elapsed) * 60.0 if elapsed > 0 else 0.0
        self._loc_per_min = max(0.0, per_min)

    def _tail_logs_metrics(self):
        now = time.time()
        tests = 0
        actions = 0
        for path in glob.glob(self.lane_log_glob):
            try:
                size = os.path.getsize(path)
                off = self._log_offsets.get(path, 0)
                if off > size:
                    off = 0
                with open(path, "rb") as f:
                    f.seek(off)
                    chunk = f.read().decode("utf-8", errors="replace")
                    self._log_offsets[path] = size
            except OSError:
                continue
            for line in chunk.splitlines():
                if TEST_LINE.search(line):
                    tests += 1
                if ACTION_LINE.search(line):
                    actions += 1
        window = 60.0
        self._test_window = [(t, c) for t, c in self._test_window if now - t < window]
        self._action_window = [(t, c) for t, c in self._action_window if now - t < window]
        if tests:
            self._test_window.append((now, tests))
        if actions:
            self._action_window.append((now, actions))
        self._test_runs_per_min = sum(c for _, c in self._test_window)
        self._actions_per_min = sum(c for _, c in self._action_window)

    def slow_extras(self):
        self._gates = []
        for path in glob.glob(self.progress_glob):
            self._gates.extend(parse_progress_text(self._read_file(Path(path))))
        self._ai_capacity = []
        if self.ai_capacity_path.is_file():
            try:
                raw = json.loads(self._read_file(self.ai_capacity_path))
                if isinstance(raw, dict):
                    providers = raw.get("providers", raw)
                    if isinstance(providers, dict):
                        for name, info in providers.items():
                            if not isinstance(info, dict):
                                continue
                            self._ai_capacity.append(
                                {
                                    "provider": str(name),
                                    "remaining_pct": info.get("remaining_pct"),
                                    "reset_ct": info.get("reset_ct"),
                                    "freshness": info.get("freshness"),
                                    "status": info.get("status"),
                                }
                            )
                    elif isinstance(providers, list):
                        for info in providers:
                            if isinstance(info, dict) and "provider" in info:
                                self._ai_capacity.append(
                                    {
                                        "provider": info.get("provider"),
                                        "remaining_pct": info.get("remaining_pct"),
                                        "reset_ct": info.get("reset_ct"),
                                        "freshness": info.get("freshness"),
                                        "status": info.get("status"),
                                    }
                                )
            except json.JSONDecodeError:
                pass

    def build_payload(self) -> dict:
        return {
            "version": "node/v1",
            "node_id": self.node_id,
            "label": self.node_meta.get("label", self.node_id),
            "ts": time.time(),
            "load": {"l1": round(self._load1, 2), "l5": round(self._load5, 2), "limit": 14},
            "cpu": {"pct": round(self._cpu_pct, 1), "note": self._cpu_note},
            "memory": self._mem,
            "swap": self._swap,
            "disk": self._disk,
            "max_lanes": self.max_lanes,
            "lanes": self._lanes,
            "routines": self._routines,
            "throughput": {
                "loc_per_min": round(self._loc_per_min, 1),
                "test_runs_per_min": round(self._test_runs_per_min, 1),
                "actions_per_min": round(self._actions_per_min, 1),
            },
            "gates": self._gates[-20:],
            "ai_capacity": self._ai_capacity,
        }

    def push(self, payload: dict) -> bool:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.push_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        token = os.environ.get("LIVE_TOKEN")
        if token:
            req.add_header("X-Live-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError):
            return False

    def tick_scheduled(self, now: float):
        if now - self._last_disk >= 30.0:
            self.slow_disk()
            self._last_disk = now
        if now - self._last_lanes >= 10.0:
            self.slow_lanes()
            self._tail_logs_metrics()
            self._last_lanes = now
        if now - self._last_loc >= 30.0:
            self.slow_loc()
            self._last_loc = now
        if now - self._last_extras >= 45.0:
            self.slow_extras()
            self._last_extras = now

    def run(self):
        while True:
            t0 = time.time()
            self.fast_tick()
            self.tick_scheduled(t0)
            payload = self.build_payload()
            if self.push(payload):
                self._backoff = 2.0
            else:
                time.sleep(self._backoff)
                self._backoff = min(30.0, self._backoff * 1.5)
                continue
            elapsed = time.time() - t0
            time.sleep(max(0.0, self.interval - elapsed))


def selftest():
    """Run parse fixtures and measure average CPU over ~4s of fast ticks."""
    fixtures_dir = Path(__file__).resolve().parent / "tests" / "fixtures"
    if (fixtures_dir / "vm_stat.txt").is_file():
        parse_vm_stat((fixtures_dir / "vm_stat.txt").read_text())
    if (fixtures_dir / "meminfo.txt").is_file():
        parse_meminfo((fixtures_dir / "meminfo.txt").read_text())
    if (fixtures_dir / "screen_ls.txt").is_file():
        parse_screen_ls((fixtures_dir / "screen_ls.txt").read_text())
    agent = Agent(
        argparse.Namespace(
            node_id="selftest",
            push="http://127.0.0.1:9/api/node",
            interval=2,
        )
    )
    start = time.time()
    n = 0
    while time.time() - start < 4.0:
        agent.fast_tick()
        n += 1
        time.sleep(0.05)
    print(f"selftest: {n} fast ticks, load1={agent._load1:.2f}, cpu={agent._cpu_pct:.1f}%")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Bob Ops live node agent")
    p.add_argument("--node-id", required=True)
    p.add_argument("--push", required=True, help="Aggregator POST URL")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        return selftest()
    agent = Agent(args)
    try:
        agent.run()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
