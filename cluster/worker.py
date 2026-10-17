#!/usr/bin/env python3
"""Cluster worker — pull jobs, execute, publish results and metrics."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from cluster.job_exec import prepare_worktree, run_cmd
from cluster.metrics_collect import (
    bump_jobs_done,
    bump_test_runs,
    collect_metrics,
    metrics_subject,
    post_node_v1,
    to_node_v1,
)
from cluster.worker_logic import (
    JobAction,
    build_result,
    check_load_ceiling,
    classify_run_outcome,
    fetch_or_empty,
    parse_job_payload,
)

STREAM_JOBS = "JOBS"
CONSUMER_WORKERS = "workers"


def load_token(node_id: str, token_file: str) -> str:
    tok = os.environ.get("BOB_NODE_TOKEN", "")
    if tok:
        return tok
    if token_file and os.path.isfile(token_file):
        key = f"{node_id.upper()}_TOKEN"
        with open(token_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"')
    key = f"{node_id.upper()}_TOKEN"
    raise SystemExit(f"Set BOB_NODE_TOKEN or {key} in {token_file}")


async def metrics_loop(
    nc,
    node_id: str,
    active_jobs: dict[str, int],
    light: bool,
    interval: float,
    also_post: str | None,
) -> None:
    subj = metrics_subject(node_id)
    while True:
        m = collect_metrics(node_id, active_jobs.get("n", 0), light=light)
        payload = json.dumps(m).encode("utf-8")
        await nc.publish(subj, payload)
        if also_post:
            post_node_v1(also_post, to_node_v1(m))
        await asyncio.sleep(interval)


async def handle_message(
    msg,
    js,
    nc,
    supported: set[str],
    max_parallel: int,
    active_jobs: dict[str, int],
    sem: asyncio.Semaphore,
    dry_run: bool,
    node_id: str = "nucbox",
) -> None:
    delivery = msg.metadata.num_delivered if msg.metadata else 1
    spec, decision = parse_job_payload(msg.data, supported)
    if decision.action == JobAction.TERM:
        await msg.term()
        return
    if decision.action == JobAction.SKIP:
        await msg.nak()
        return

    assert spec is not None
    load_dec = check_load_ceiling(spec["load_ceiling"])
    if load_dec is not None:
        await msg.nak(delay=load_dec.delay_s)
        return

    if active_jobs.get("n", 0) >= max_parallel:
        await msg.nak(delay=5.0)
        return

    async with sem:
        active_jobs["n"] = active_jobs.get("n", 0) + 1
        hb_stop = asyncio.Event()

        async def heartbeat() -> None:
            while not hb_stop.is_set():
                try:
                    await msg.in_progress()
                except Exception:
                    pass
                await asyncio.sleep(30.0)

        hb_task = asyncio.create_task(heartbeat())
        import time

        t0 = time.time()
        try:
            wd = await asyncio.to_thread(prepare_worktree, spec, dry_run)
            rc, tail, timed_out = await asyncio.to_thread(run_cmd, spec, wd, dry_run)
            status, action = classify_run_outcome(rc, timed_out)
            if delivery >= 3 and action == JobAction.NAK:
                action = JobAction.TERM
                status = "exhausted"
            duration = time.time() - t0
            result = build_result(spec["id"], status, rc, duration, tail)
            await js.publish(f"results.{node_id}.{spec['id']}", json.dumps(result).encode("utf-8"))
            if spec["class"] == "test":
                bump_test_runs()
            bump_jobs_done()
            if action == JobAction.ACK:
                await msg.ack()
            elif action == JobAction.NAK:
                await msg.nak(delay=15.0)
            else:
                await msg.term()
        except Exception as e:
            duration = time.time() - t0
            result = build_result(spec["id"], "error", -1, duration, str(e))
            await js.publish(f"results.{node_id}.{spec['id']}", json.dumps(result).encode("utf-8"))
            if delivery >= 3:
                await msg.term()
            else:
                await msg.nak(delay=15.0)
        finally:
            hb_stop.set()
            hb_task.cancel()
            active_jobs["n"] = max(0, active_jobs.get("n", 1) - 1)


async def run_worker(args: argparse.Namespace) -> None:
    import nats

    supported = set(c.strip() for c in args.classes.split(",") if c.strip())
    token = load_token(args.node_id, args.token_file)
    user = args.node_id if args.node_id in {"mini", "nucbox", "macbook", "dashboard"} else args.node_id

    nc = await nats.connect(args.nats, user=user, password=token, inbox_prefix=f"_INBOX_{user}".encode())
    js = nc.jetstream()
    psub = await js.pull_subscribe("", durable=f"workers_{user}", stream=STREAM_JOBS)

    active_jobs: dict[str, int] = {"n": 0}
    sem = asyncio.Semaphore(args.max_parallel)
    interval = 10.0 if args.light else 2.0
    metrics_task = asyncio.create_task(
        metrics_loop(nc, args.node_id, active_jobs, args.light, interval, args.also_post)
    )

    try:
        while True:
            msgs = await fetch_or_empty(psub, args.max_parallel, timeout=5)
            tasks = [
                asyncio.create_task(
                    handle_message(
                        m, js, nc, supported, args.max_parallel, active_jobs, sem, args.dry_run, node_id=user
                    )
                )
                for m in msgs
            ]
            if tasks:
                await asyncio.gather(*tasks)
    finally:
        metrics_task.cancel()
        await nc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bob cluster worker")
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--nats", default=os.environ.get("BOB_NATS_URL", "nats://127.0.0.1:4222"))
    parser.add_argument("--token-file", default=os.path.expanduser("~/.config/bob-cluster/tokens.env"))
    parser.add_argument("--classes", default="test,build,render")
    parser.add_argument("--max-parallel", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--light", action="store_true", help="Low-overhead metrics (Mini)")
    parser.add_argument("--also-post", help="Also POST node/v1 metrics to this URL")
    args = parser.parse_args()
    try:
        asyncio.run(run_worker(args))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
