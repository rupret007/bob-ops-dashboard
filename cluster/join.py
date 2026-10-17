#!/usr/bin/env python3
"""Client side of the join flow: redeem a single-use join code for a node token.

    BOB_JOIN_CODE='bobjoin1:...' python3 -m cluster.join [--env-out ~/.config/bob-cluster/node.env]

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

from cluster.capabilities import NODE_NAME_PATTERN, allowed_caps_for_role
from cluster.registry import TOKEN_PATTERN, RegistryError, _atomic_write, decode_join_code, inbox_prefix, validate_hub_url

DEFAULT_ENV = Path.home() / ".config" / "bob-cluster" / "node.env"


def hub_reachable(url: str, timeout: float = 4.0) -> bool:
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname, u.port or 4222), timeout=timeout):
            return True
    except OSError:
        return False


def validate_reply(reply, code: dict) -> dict:
    """Never write anything the hub sent without checking it; the env file is parsed by us."""
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise RegistryError("join refused")
    user, token, role, caps = reply.get("user"), reply.get("token"), reply.get("role"), reply.get("caps")
    if user != code["n"] or role != code["r"]:
        raise RegistryError("hub reply does not match the join code")
    if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
        raise RegistryError("hub reply token malformed")
    if not isinstance(caps, list) or not all(isinstance(c, str) for c in caps) \
            or not set(caps) <= allowed_caps_for_role(role):
        raise RegistryError("hub reply caps invalid for role")
    expected = "BOB" if role == "member" else "GUEST"
    if reply.get("account") != expected:
        raise RegistryError("hub reply account mismatch")
    return {"user": user, "token": token, "role": role, "caps": sorted(caps)}


def render_node_env(url: str, reply: dict, max_lanes: int) -> str:
    url = validate_hub_url(url)
    if not NODE_NAME_PATTERN.fullmatch(reply["user"]) or not TOKEN_PATTERN.fullmatch(reply["token"]):
        raise RegistryError("refusing to write malformed credential")
    if not 1 <= int(max_lanes) <= 16:
        raise RegistryError("max lanes must be 1..16")
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
    try:
        reply = json.loads(msg.data)
    except ValueError as e:
        raise RegistryError("hub reply was not JSON") from e
    if isinstance(reply, dict) and reply.get("ok") is False:
        err = reply.get("error")
        raise RegistryError(err if isinstance(err, str) and len(err) < 120 and err.isprintable() else "join refused")
    return data["u"], validate_reply(reply, data)


def default_lanes() -> int:
    n = os.cpu_count() or 2
    return max(1, min(4, n // 4))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Redeem a Bob cluster join code")
    ap.add_argument("--code", default=os.environ.get("BOB_JOIN_CODE", ""),
                    help="join code (default: $BOB_JOIN_CODE, preferred so it stays out of ps)")
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
    except RegistryError as e:
        print(f"join: redeem failed: {e}", file=sys.stderr)
        return 4
    except Exception as e:  # noqa: BLE001 - fixed category, never server text or credentials
        print(f"join: redeem failed ({type(e).__name__}). The code may be used or expired; "
              "ask Jeff for a new one.", file=sys.stderr)
        return 4
    out = Path(os.path.expanduser(args.env_out))
    _atomic_write(out, render_node_env(url, reply, args.max_lanes))
    print(f"join: node '{reply['user']}' joined as {reply['role']} with caps "
          f"{','.join(reply['caps'])}; credential saved to {out} (0600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
