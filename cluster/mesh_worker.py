#!/usr/bin/env python3
"""Mesh node agent: capability heartbeat + capability-routed job execution.

    python3 -m cluster.mesh_worker --env ~/.config/bob-cluster/node.env [--dry-run]

Members pull CAPJOBS consumers for the capabilities they are both granted (by
the hub, enforced by NATS permissions) and actually signed in to locally.
Guests pull only SANDBOX.guests and run every job in a container sandbox.
Credentials for LLM CLIs never leave this machine: the CLI uses its own login.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from cluster.capabilities import (
    CAPABILITIES, GUEST_CAPS, build_heartbeat, cap_consumer, detect_providers, detected_caps,
    effective_caps, heartbeat_subject, load_quota_hints, sandbox_runtime, _default_which,
)
from cluster.registry import CONSUMER_GUESTS, STREAM_CAPJOBS, STREAM_SANDBOX, _read_env, inbox_prefix
from cluster.routing import CLI_FOR_PROVIDER, checkout_spec, llm_argv, validate_cap_spec
from cluster.sanitize import sanitize_tail
from cluster.spec import SpecError
from cluster.worker_logic import check_load_ceiling, fetch_or_empty

log = logging.getLogger("bob-mesh-worker")
CFG = Path.home() / ".config" / "bob-cluster"
PROVIDER_REFRESH_S = 300


class NodeConfig:
    def __init__(self, env: dict[str, str]):
        self.url = env.get("BOB_NATS_URL", "nats://127.0.0.1:4222")
        self.user = env["BOB_NODE_USER"]
        self.token = env["BOB_NODE_TOKEN"]
        self.role = env.get("BOB_ROLE", "member")
        self.granted = [c for c in env.get("BOB_GRANTED_CAPS", "").split(",") if c in CAPABILITIES]
        self.max_lanes = max(1, min(16, int(env.get("BOB_MAX_LANES", "1") or 1)))
        self.heartbeat_s = max(5.0, float(env.get("BOB_HEARTBEAT_S", "15") or 15))
        if self.role not in ("member", "guest"):
            raise SystemExit("BOB_ROLE must be member or guest")

    @classmethod
    def from_file(cls, path: Path) -> "NodeConfig":
        env = _read_env(path)
        if "BOB_NODE_TOKEN" not in env or "BOB_NODE_USER" not in env:
            raise SystemExit(f"{path} is missing BOB_NODE_USER/BOB_NODE_TOKEN; run cluster.join first")
        return cls(env)


def llm_env(home: Path) -> dict[str, str]:
    """LLM CLIs need the real HOME to find their own login; nothing else is inherited."""
    path = ":".join([str(home / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin",
                     str(home / ".npm-global/bin"), "/usr/bin", "/bin"])
    return {"PATH": path, "HOME": str(home), "LANG": "C.UTF-8", "TERM": "dumb", "CI": "1"}


class Executor:
    """Runs one validated v2 spec. Separate class so tests can stub it."""

    def __init__(self, role: str, dry_run: bool, which=_default_which):
        self.role = role
        self.dry_run = dry_run
        self.which = which

    def run(self, spec: dict[str, Any]) -> tuple[str, int, str]:
        if self.dry_run:
            return "ok", 0, f"dry-run {spec['cap']} {spec['id']}\n"
        from cluster.job_exec import prepare_worktree

        wd = prepare_worktree(checkout_spec(spec), dry_run=False)
        cap = spec["cap"]
        if cap.startswith("llm."):
            prov = CAPABILITIES[cap]["provider"]
            cli = self.which(CLI_FOR_PROVIDER.get(prov, ""))
            if not cli:
                return "error", -1, f"{prov} CLI not found"
            argv, via_stdin = llm_argv(cap, cli)
            if not via_stdin:
                argv = argv + [spec["prompt"]]
            return self._proc(argv, wd, llm_env(Path.home()), spec["timeout_s"],
                              stdin=spec["prompt"] if via_stdin else None)
        if cap in GUEST_CAPS:
            rt = sandbox_runtime(self.which)
            if not rt:
                return "refused", -1, "no docker/podman: sandbox jobs are never run on the host"
            from cluster.sandbox import sandbox_argv

            argv = sandbox_argv(rt, wd, spec["cmd"], spec["id"])
            return self._proc(argv, wd, {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                         "HOME": str(Path.home())}, spec["timeout_s"])
        from cluster.job_exec import minimal_env

        home = wd / ".job-home"
        home.mkdir(mode=0o700, exist_ok=True)
        return self._proc(spec["cmd"], wd, minimal_env(home), spec["timeout_s"])

    @staticmethod
    def _proc(argv, cwd, env, timeout, stdin=None) -> tuple[str, int, str]:
        try:
            p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                               timeout=timeout, input=stdin, shell=False)
            return ("ok" if p.returncode == 0 else "failed"), p.returncode, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired as e:
            out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode(errors="replace")
            return "timeout", -1, out or ""


class MeshWorker:
    def __init__(self, cfg: NodeConfig, executor: Executor, quota_path: Path | None = None,
                 providers=None, detected: set[str] | None = None):
        self.cfg = cfg
        self.exec = executor
        self.quota_path = quota_path or (CFG / "quota.json")
        self._providers = providers
        self._detected = detected
        self._providers_at = 0.0
        self.active = 0
        self.done = 0
        self.sem = asyncio.Semaphore(cfg.max_lanes)
        self.nc = None
        self.js = None

    # ---- capabilities
    def refresh_caps(self, force: bool = False) -> list[str]:
        if self._providers is None or force or (time.time() - self._providers_at > PROVIDER_REFRESH_S
                                                 and self._detected is None):
            self._providers = detect_providers()
            self._providers_at = time.time()
        det = self._detected if self._detected is not None else detected_caps(self._providers)
        return effective_caps(self.cfg.role, self.cfg.granted, det)

    def heartbeat(self) -> dict[str, Any]:
        caps = self.refresh_caps()
        return build_heartbeat(self.cfg.user, self.cfg.role, self._providers or {}, caps,
                               self.cfg.max_lanes, self.active, load_quota_hints(self.quota_path))

    async def heartbeat_loop(self, stop: asyncio.Event) -> None:
        subj = heartbeat_subject(self.cfg.user, self.cfg.role)
        while not stop.is_set():
            try:
                await self.nc.publish(subj, json.dumps(self.heartbeat()).encode())
            except Exception as e:  # noqa: BLE001 - keep beating through transient errors
                log.warning("heartbeat failed: %s", type(e).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.cfg.heartbeat_s)
            except asyncio.TimeoutError:
                pass

    # ---- jobs
    def result_subject(self, job_id: str) -> str:
        if self.cfg.role == "guest":
            return f"sandbox.results.{self.cfg.user}.{job_id}"
        # Scoped to this node: NATS only lets it publish results.<self>.>.
        return f"results.{self.cfg.user}.{job_id}"

    async def handle(self, msg, cap: str) -> None:
        guest = self.cfg.role == "guest"
        try:
            spec = validate_cap_spec(json.loads(msg.data), guest=guest)
        except (SpecError, ValueError, UnicodeDecodeError) as e:
            log.warning("terminating invalid job: %s", e)
            await msg.term()
            return
        if spec["cap"] != cap:
            # Subject/consumer says one capability, body another: never trust the body.
            await msg.term()
            return
        dec = check_load_ceiling(spec["load_ceiling"])
        if dec is not None:
            await msg.nak(delay=dec.delay_s)
            return
        async with self.sem:
            self.active += 1
            t0 = time.time()
            hb_stop = asyncio.Event()

            async def keepalive():
                while not hb_stop.is_set():
                    try:
                        await msg.in_progress()
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        await asyncio.wait_for(hb_stop.wait(), timeout=30)
                    except asyncio.TimeoutError:
                        pass
            ka = asyncio.create_task(keepalive())
            try:
                status, rc, tail = await asyncio.to_thread(self.exec.run, spec)
            except Exception as e:  # noqa: BLE001
                status, rc, tail = "error", -1, f"{type(e).__name__}: {e}"
            finally:
                hb_stop.set()
                await ka
                self.active -= 1
            res = {"id": spec["id"], "cap": cap, "node": self.cfg.user, "status": status, "rc": rc,
                   "duration_s": round(time.time() - t0, 3), "tail": sanitize_tail(tail)}
            await self.nc.publish(self.result_subject(spec["id"]), json.dumps(res).encode())
            self.done += 1
            if status in ("ok", "failed"):
                await msg.ack()
            elif status == "refused":
                await msg.nak(delay=60)
            elif (msg.metadata.num_delivered if msg.metadata else 1) >= 3:
                await msg.term()
            else:
                await msg.nak(delay=15)

    async def pull_loop(self, stream: str, consumer: str, cap: str, stop: asyncio.Event) -> None:
        psub = await self.js.pull_subscribe_bind(durable=consumer, stream=stream)
        while not stop.is_set():
            free = self.cfg.max_lanes - self.active
            if free <= 0:
                await asyncio.sleep(1)
                continue
            msgs = await fetch_or_empty(psub, 1, timeout=5)
            for m in msgs:
                asyncio.create_task(self.handle(m, cap))

    def consumers(self) -> list[tuple[str, str, str]]:
        caps = self.refresh_caps()
        if self.cfg.role == "guest":
            return [(STREAM_SANDBOX, CONSUMER_GUESTS, "sandbox.test")] if "sandbox.test" in caps else []
        return [(STREAM_CAPJOBS, cap_consumer(c), c) for c in caps if c not in GUEST_CAPS]

    async def run(self, stop: asyncio.Event | None = None) -> None:
        import nats

        stop = stop or asyncio.Event()
        self.nc = await nats.connect(self.cfg.url, user=self.cfg.user, password=self.cfg.token,
                                     inbox_prefix=inbox_prefix(self.cfg.user).encode(),
                                     max_reconnect_attempts=-1, name=f"bob-mesh-{self.cfg.user}")
        self.js = self.nc.jetstream()
        tasks = [asyncio.create_task(self.heartbeat_loop(stop))]
        for stream, consumer, cap in self.consumers():
            tasks.append(asyncio.create_task(self.pull_loop(stream, consumer, cap, stop)))
            log.info("pulling %s.%s (%s)", stream, consumer, cap)
        try:
            await stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.nc.drain()


def main() -> int:
    ap = argparse.ArgumentParser(description="Bob mesh node agent")
    ap.add_argument("--env", default=str(CFG / "node.env"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--print-heartbeat", action="store_true", help="print one heartbeat and exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = NodeConfig.from_file(Path(os.path.expanduser(args.env)))
    w = MeshWorker(cfg, Executor(cfg.role, args.dry_run))
    if args.print_heartbeat:
        print(json.dumps(w.heartbeat(), indent=1))
        return 0
    try:
        asyncio.run(w.run())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
