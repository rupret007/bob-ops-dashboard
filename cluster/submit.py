#!/usr/bin/env python3
"""Enqueue a validated job spec; optionally wait for result."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from cluster.spec import SpecError, job_subject, validate_job_spec
from cluster.worker_logic import idle_timeout_exceptions


def load_token(user: str, token_file: str) -> str:
    tok = os.environ.get("BOB_NODE_TOKEN", "")
    if tok:
        return tok
    if token_file and os.path.isfile(token_file):
        key = f"{user.upper()}_TOKEN"
        with open(token_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("Set BOB_NODE_TOKEN or provide --token-file with user token")


async def publish_job(nc_url: str, user: str, token: str, spec: dict) -> None:
    import nats

    nc = await nats.connect(nc_url, user=user, password=token)
    js = nc.jetstream()
    subj = job_subject(spec)
    payload = json.dumps(spec).encode("utf-8")
    await js.publish(subj, payload)
    await nc.close()
    print(f"enqueued {subj}")


async def wait_result_from_sub(sub, job_id: str, timeout: float) -> dict | None:
    """Wait for a results message matching job_id; idle per-iteration timeouts do not end the wait."""
    deadline = time.monotonic() + timeout
    idle_timeout = idle_timeout_exceptions()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            msg = await sub.next_msg(timeout=min(5.0, remaining))
        except idle_timeout:
            continue
        data = json.loads(msg.data.decode("utf-8"))
        if data.get("id") == job_id:
            return data


async def wait_result(nc_url: str, user: str, token: str, job_id: str, timeout: float) -> dict | None:
    import nats

    nc = await nats.connect(nc_url, user=user, password=token)
    sub = await nc.subscribe(f"results.{job_id}")
    try:
        return await wait_result_from_sub(sub, job_id, timeout)
    finally:
        await nc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit a cluster job")
    parser.add_argument("--nats", default=os.environ.get("BOB_NATS_URL", "nats://127.0.0.1:4222"))
    parser.add_argument("--user", default=os.environ.get("BOB_NATS_USER", "nucbox"))
    parser.add_argument("--token-file", default=os.path.expanduser("~/.config/bob-cluster/tokens.env"))
    parser.add_argument("--spec", help="JSON job spec string")
    parser.add_argument("--spec-file", help="Path to JSON job spec file")
    parser.add_argument("--wait", action="store_true", help="Wait for results.<id>")
    parser.add_argument("--wait-timeout", type=float, default=3600.0)
    args = parser.parse_args()

    raw: str | None = args.spec
    if args.spec_file:
        with open(args.spec_file, encoding="utf-8") as f:
            raw = f.read()
    if not raw:
        raw = sys.stdin.read()
    try:
        spec = validate_job_spec(json.loads(raw))
    except (SpecError, json.JSONDecodeError) as e:
        print(f"invalid spec: {e}", file=sys.stderr)
        return 1

    token = load_token(args.user, args.token_file)
    asyncio.run(publish_job(args.nats, args.user, token, spec))

    if args.wait:
        result = asyncio.run(wait_result(args.nats, args.user, token, spec["id"], args.wait_timeout))
        if result is None:
            print("timed out waiting for result", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2))
        return 0 if result.get("status") == "ok" else 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
