#!/usr/bin/env python3
"""Client side of the join flow: redeem a single-use join code for a node token.

    python3 -m cluster.join --code 'bobjoin1:...' [--env-out ~/.config/bob-cluster/node.env]

Connects to the hub as the code's pending JOIN user, sends join.redeem, and
writes the returned per-node credential to a 0600 env file. The token is never
printed. The join code is dead afterwards (the hub deletes the pending user).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

from cluster.registry import RegistryError, _atomic_write, decode_join_code, inbox_prefix

DEFAULT_ENV = Path.home() / ".config" / "bob-cluster" / "node.env"


def hub_reachable(url: str, timeout: float = 4.0) -> bool:
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname, u.port or 4222), timeout=timeout):
            return True
    except OSError:
        return False


def render_node_env(url: str, reply: dict, max_lanes: int) -> str:
    lines = [
        "# Bob cluster node credential. Never commit or share. Written by cluster.join.",
        f"BOB_NATS_URL={url}",
        f"BOB_NODE_USER={reply['user']}",
        f"BOB_NODE_TOKEN={reply['token']}",
        f"BOB_ROLE={reply['role']}",
        f"BOB_GRANTED_CAPS={','.join(reply['caps'])}",
        f"BOB_MAX_LANES={max_lanes}",
    ]
    return "\n".join(lines) + "\n"


async def redeem(code: str, timeout: float = 15.0) -> tuple[str, dict]:
    import nats

    data = decode_join_code(code)
    nc = await nats.connect(data["u"], user=data["user"], password=data["s"],
                            inbox_prefix=inbox_prefix(data["user"]).encode(),
                            allow_reconnect=False, connect_timeout=8)
    try:
        req = {"user": data["user"], "secret": data["s"], "name": data["n"],
               "os": platform.system().lower(), "host": socket.gethostname()[:63]}
        msg = await nc.request("join.redeem", json.dumps(req).encode(), timeout=timeout)
    finally:
        await nc.close()
    reply = json.loads(msg.data)
    if not reply.get("ok"):
        raise RegistryError(reply.get("error") or "join refused")
    return data["u"], reply


def default_lanes() -> int:
    n = os.cpu_count() or 2
    return max(1, min(4, n // 4))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Redeem a Bob cluster join code")
    ap.add_argument("--code", required=True)
    ap.add_argument("--env-out", default=str(DEFAULT_ENV))
    ap.add_argument("--max-lanes", type=int, default=default_lanes())
    ap.add_argument("--check-only", action="store_true", help="decode code + test hub reachability")
    args = ap.parse_args(argv)
    try:
        data = decode_join_code(args.code)
    except RegistryError as e:
        print(f"join: {e}", file=sys.stderr)
        return 2
    if not hub_reachable(data["u"]):
        print("join: cannot reach the hub over Tailscale. Install Tailscale and sign in to Jeff's "
              "tailnet (or accept the shared NucBox), then re-run.", file=sys.stderr)
        return 3
    if args.check_only:
        print(f"join: code for node '{data['n']}' role={data['r']} hub reachable")
        return 0
    try:
        url, reply = asyncio.run(redeem(args.code))
    except Exception as e:  # noqa: BLE001 - surface any failure as a short message
        print(f"join: redeem failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 4
    out = Path(os.path.expanduser(args.env_out))
    _atomic_write(out, render_node_env(url, reply, args.max_lanes))
    print(f"join: node '{reply['user']}' joined as {reply['role']} with caps "
          f"{','.join(reply['caps'])}; credential saved to {out} (0600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
