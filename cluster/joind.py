#!/usr/bin/env python3
"""Hub-side join responder (runs on the NucBox in the JOIN account).

Serves join.redeem: validates a single-use join code against the registry,
mints the node's permanent token, re-renders the mesh config and reloads
nats-server *after* replying (the reload drops the pending user).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Callable

from cluster.registry import Registry, RegistryError, render_conf

log = logging.getLogger("bob-joind")
MAX_REQ_BYTES = 2048


class Hub:
    """Registry + config output + reload hook, shared by bobctl and joind."""

    def __init__(self, config_dir: Path, conf_out: Path, store_dir: str, reload: Callable[[], None],
                 port: int = 4222, http: str = "127.0.0.1:8232", clock=None):
        import time

        self.reg = Registry(config_dir, clock=clock or time.time)
        self.conf_out = Path(conf_out)
        self.store_dir = store_dir
        self.reload_cb = reload
        self.port = port
        self.http = http

    def render(self) -> None:
        from cluster.registry import _atomic_write

        _atomic_write(self.conf_out, render_conf(self.reg.read(), self.store_dir, port=self.port, http=self.http))

    def apply(self) -> None:
        self.render()
        self.reload_cb()


def handle_redeem(hub: Hub, raw: bytes) -> tuple[dict, bool]:
    """Return (reply, changed). Never echoes secrets back except the new token."""
    if len(raw) > MAX_REQ_BYTES:
        return {"ok": False, "error": "request too large"}, False
    try:
        req = json.loads(raw)
        user, secret, name = req["user"], req["secret"], req["name"]
        if not all(isinstance(x, str) for x in (user, secret, name)):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        return {"ok": False, "error": "malformed request"}, False
    try:
        out = hub.reg.redeem(user, secret, name)
    except RegistryError as e:
        return {"ok": False, "error": str(e)}, False
    log.info("redeemed join code for node %s role %s", out["user"], out["role"])
    return {"ok": True, **out}, True


async def serve(hub: Hub, url: str, password: str, reload_delay: float = 1.0,
                stop: asyncio.Event | None = None) -> None:
    import nats

    nc = await nats.connect(url, user="joind", password=password, max_reconnect_attempts=-1)
    pending_reload: list[asyncio.Task] = []

    async def on_msg(msg):
        reply, changed = await asyncio.to_thread(handle_redeem, hub, msg.data)
        if changed:
            await asyncio.to_thread(hub.render)
        await msg.respond(json.dumps(reply).encode())
        await nc.flush()
        if changed:
            async def later():
                await asyncio.sleep(reload_delay)
                await asyncio.to_thread(hub.reload_cb)
            pending_reload.append(asyncio.create_task(later()))

    await nc.subscribe("join.redeem", cb=on_msg)
    await nc.flush()
    log.info("joind listening on join.redeem")
    try:
        if stop is None:
            await asyncio.Event().wait()
        else:
            await stop.wait()
    finally:
        for t in pending_reload:
            if not t.done():
                await t
        await nc.drain()


def signal_reload_from_pidfile(pidfile: Path) -> Callable[[], None]:
    def _reload() -> None:
        import signal

        try:
            pid = int(Path(pidfile).read_text().strip())
            os.kill(pid, signal.SIGHUP)
        except (OSError, ValueError) as e:
            log.error("reload failed: %s", e)
    return _reload


def main() -> int:
    from cluster.bobctl import hub_from_args, add_hub_args

    ap = argparse.ArgumentParser(description="Bob cluster join responder")
    add_hub_args(ap)
    ap.add_argument("--nats", default=os.environ.get("BOB_NATS_URL", "nats://127.0.0.1:4222"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    hub = hub_from_args(args)
    pw = hub.reg.service_password("joind")
    asyncio.run(serve(hub, args.nats, pw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
