#!/usr/bin/env python3
"""Forward mesh capability heartbeats (caps.> and sandbox.caps.>) to the live dashboard.

Runs on the NucBox as the read-only `dashboard` NATS user and POSTs each
caps/v1 heartbeat to bob-live's /api/cluster (same LIVE_TOKEN as /api/node).

    python3 -m cluster.caps_bridge --post http://127.0.0.1:8787/api/cluster
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from cluster.registry import _read_env

MAX_HEARTBEAT_BYTES = 8192


def post(url: str, body: bytes, token: str | None) -> bool:
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError):
        return False


async def run(nats_url: str, dash_token: str, post_url: str, live_token: str | None,
              stop: asyncio.Event | None = None) -> None:
    import nats

    nc = await nats.connect(nats_url, user="dashboard", password=dash_token, max_reconnect_attempts=-1)

    async def cb(msg):
        if len(msg.data) > MAX_HEARTBEAT_BYTES:
            return
        try:
            d = json.loads(msg.data)
        except ValueError:
            return
        if not isinstance(d, dict) or d.get("v") != "caps/v1":
            return
        # The subject is authoritative for the node name (NATS permissions bind
        # caps.<name> to that node's user), never the body.
        d["node"] = msg.subject.rsplit(".", 1)[-1]
        d["role"] = "guest" if msg.subject.startswith("sandbox.") else d.get("role", "member")
        await asyncio.to_thread(post, post_url, json.dumps(d).encode(), live_token)

    await nc.subscribe("caps.*", cb=cb)
    await nc.subscribe("sandbox.caps.*", cb=cb)
    try:
        await (stop.wait() if stop else asyncio.Event().wait())
    finally:
        await nc.drain()


def main() -> int:
    cfg = Path.home() / ".config" / "bob-cluster"
    ap = argparse.ArgumentParser(description="caps.> -> bob-live /api/cluster")
    ap.add_argument("--nats", default=os.environ.get("BOB_NATS_URL", "nats://127.0.0.1:4222"))
    ap.add_argument("--post", default=os.environ.get("BOB_LIVE_CLUSTER", "http://127.0.0.1:8787/api/cluster"))
    args = ap.parse_args()
    dash = os.environ.get("BOB_DASHBOARD_TOKEN") or _read_env(cfg / "tokens.env").get("DASHBOARD_TOKEN")
    if not dash:
        raise SystemExit("need DASHBOARD_TOKEN in ~/.config/bob-cluster/tokens.env")
    asyncio.run(run(args.nats, dash, args.post, os.environ.get("LIVE_TOKEN")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
