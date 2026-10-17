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
import ipaddress
import time
from pathlib import Path
from typing import Callable

from cluster.registry import PENDING_USER_PATTERN, Registry, RegistryError, inbox_prefix, render_conf

log = logging.getLogger("bob-joind")
MAX_REQ_BYTES = 2048
FAIL_DELAY_S = 1.0          # slows online guessing; codes are 192-bit anyway
MAX_ATTEMPTS_PER_USER = 30
LOCAL_ATTEMPTS_PER_USER = 3
LOCAL_ATTEMPTS_PER_MIN = 6
MAX_ATTEMPTS_PER_MIN = 30   # per source IP (all join users at that IP share a bucket)
SWEEP_S = 60.0              # drop expired codes from the live config


class Hub:
    """Registry + config output + reload hook, shared by bobctl and joind."""

    def __init__(self, config_dir: Path, conf_out: Path, store_dir: str, reload: Callable[[], None],
                 port: int = 4222, http: str = "127.0.0.1:8232", clock=None, host: str = "127.0.0.1"):
        import time

        self.reg = Registry(config_dir, clock=clock or time.time)
        self.conf_out = Path(conf_out)
        self.store_dir = store_dir
        self.reload_cb = reload
        self.port = port
        self.http = http
        self.host = str(ipaddress.ip_address(host))

    def render(self) -> None:
        from cluster.registry import _atomic_write

        _atomic_write(self.conf_out, render_conf(self.reg.read(), self.store_dir, port=self.port, http=self.http, host=self.host))

    def apply(self) -> None:
        self.render()
        self.reload_cb()


def reply_ok_for(user: str, reply: str | None) -> bool:
    """The token may only go to the requesting pending user's private inbox."""
    if not reply or not PENDING_USER_PATTERN.fullmatch(user):
        return False
    pre = inbox_prefix(user) + "."
    rest = reply[len(pre):]
    return reply.startswith(pre) and bool(rest) and all(c.isalnum() or c in "._-" for c in rest) \
        and "*" not in reply and ">" not in reply


def handle_redeem(hub: Hub, raw: bytes, reply: str | None = "") -> tuple[dict, bool]:
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
    if reply is not None and reply != "" and not reply_ok_for(user, reply):
        # Checked before redeem so a bad reply address cannot burn the code.
        return {"ok": False, "error": "reply must be the joiner's private inbox"}, False
    try:
        out = hub.reg.redeem(user, secret, name)
    except RegistryError as e:
        return {"ok": False, "error": str(e)}, False
    log.info("redeemed join code for node %s role %s", out["user"], out["role"])
    return {"ok": True, **out}, True


def monitor_json(http: str, path: str) -> dict:
    from urllib.request import ProxyHandler, build_opener

    host, port = http.rsplit(":", 1)
    if host != "127.0.0.1" or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("monitor must bind to 127.0.0.1")
    # Never send monitoring traffic (including authenticated usernames) to a proxy.
    with build_opener(ProxyHandler({})).open(f"http://{http}/{path}", timeout=1) as response:
        return json.load(response)


def source_address(http: str, user: str) -> str:
    """Resolve a broker-authorized subject's user to its connected source IP.

    Refuse ambiguous users connected from multiple IPs, or missing monitoring
    data. A client-supplied body/header/hostname is never an address authority.
    """
    addresses = set()
    offset = 0
    while True:
        data = monitor_json(http, f"connz?auth=1&offset={offset}&limit=1024")
        connections = data["connections"]
        for conn in connections:
            if conn.get("authorized_user") == user:
                ip = ipaddress.ip_address(conn["ip"])
                addresses.add(str(getattr(ip, "ipv4_mapped", None) or ip))
        offset += len(connections)
        if offset >= data["total"]:
            break
        if not connections:
            raise RuntimeError("incomplete connection monitoring data")
    if len(addresses) != 1:
        raise RuntimeError("join source cannot be established uniquely")
    return addresses.pop()


class SourceRateLimiter:
    def __init__(self, clock=time.monotonic, limit=MAX_ATTEMPTS_PER_MIN):
        self.clock = clock
        self.limit = limit
        self.windows: dict[str, list[float]] = {}

    def allow(self, source: str) -> bool:
        now = self.clock()
        self.windows = {key: [t for t in times if now - t < 60]
                        for key, times in self.windows.items() if times[-1] > now - 60}
        window = self.windows.setdefault(source, [])
        if len(window) >= self.limit:
            return False
        window.append(now)
        return True


async def serve(hub: Hub, url: str, password: str, reload_delay: float = 1.0,
                stop: asyncio.Event | None = None, allow_loopback: bool = False) -> None:
    import nats

    nc = await nats.connect(url, user="joind", password=password, max_reconnect_attempts=-1)
    pending_reload: list[asyncio.Task] = []
    serial = asyncio.Lock()   # one bcrypt-heavy redeem at a time
    limiter = SourceRateLimiter()
    users = SourceRateLimiter(limit=MAX_ATTEMPTS_PER_USER)
    local_users = SourceRateLimiter(limit=LOCAL_ATTEMPTS_PER_USER)
    local = SourceRateLimiter(limit=LOCAL_ATTEMPTS_PER_MIN)

    async def on_msg(msg):
        user = msg.subject.removeprefix("join.redeem.")
        if not reply_ok_for(user, msg.reply) or len(msg.data) > MAX_REQ_BYTES:
            return
        try:
            source = await asyncio.to_thread(source_address, hub.http, user)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            log.warning("join source lookup failed; refusing request")
            return
        loopback = ipaddress.ip_address(source).is_loopback
        if loopback and not allow_loopback:
            return  # uniform refusal: no response, as for missing/ambiguous sources
        # Charge the authenticated user first: exhausted codes cannot drain the
        # shared source bucket. Local forwarding still has a small aggregate cap.
        if not users.allow(user):
            return
        if loopback and (not local_users.allow(user) or not local.allow("loopback")):
            return
        if not limiter.allow(source):
            log.warning("redeem rate limit hit")
            return  # silent drop; the client times out
        try:
            if json.loads(msg.data).get("user") != user:
                return
        except (ValueError, AttributeError):
            return
        async with serial:
            reply, changed = await asyncio.to_thread(handle_redeem, hub, msg.data, msg.reply)
            if not reply.get("ok"):
                await asyncio.sleep(FAIL_DELAY_S)
        if not reply.get("ok") and "private inbox" in reply.get("error", ""):
            return  # never send anything to a foreign reply subject
        if changed:
            await asyncio.to_thread(hub.render)
        await msg.respond(json.dumps(reply).encode())
        await nc.flush()
        if changed:
            async def later():
                await asyncio.sleep(reload_delay)
                await asyncio.to_thread(hub.reload_cb)
            pending_reload.append(asyncio.create_task(later()))

    async def sweep():
        while True:
            await asyncio.sleep(SWEEP_S)
            try:
                if await asyncio.to_thread(hub.reg.expire):
                    await asyncio.to_thread(hub.apply)
                    log.info("expired join codes removed from config")
            except Exception as e:  # noqa: BLE001
                log.error("sweep failed: %s", type(e).__name__)

    await nc.subscribe("join.redeem.*", cb=on_msg)
    await nc.flush()
    sweeper = asyncio.create_task(sweep())
    log.info("joind listening on join.redeem")
    try:
        if stop is None:
            await asyncio.Event().wait()
        else:
            await stop.wait()
    finally:
        sweeper.cancel()
        for t in pending_reload:
            if not t.done():
                await t
        await nc.drain()


def signal_reload_from_pidfile(pidfile: Path, http: str = "127.0.0.1:8232",
                               conf_out: Path | None = None, timeout: float = 5.0) -> Callable[[], None]:
    def _reload() -> None:
        import signal
        import shlex
        import subprocess

        try:
            pid = int(Path(pidfile).read_text().strip())
            if pid <= 1:
                raise ValueError("invalid server PID")
            if Path(os.readlink(f"/proc/{pid}/exe")).name != "nats-server":
                raise RuntimeError("PID executable is not nats-server")
            before = monitor_json(http, "varz")
            # nats-server /varz exposes no PID; the target is bound by its config
            # path (below) and a stable server_id whose config_load_time changes
            # only after our SIGHUP.
            if not before.get("server_id") or not before.get("config_load_time"):
                raise RuntimeError("monitor did not report server identity")
            if conf_out is not None:
                # /varz has no config_file field. Check the target process's
                # config argument without logging its command line.
                command = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                         capture_output=True, text=True, check=True, timeout=2)
                argv = shlex.split(command.stdout)
                configs = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg in ("-c", "--config")]
                configs += [arg.split("=", 1)[1] for arg in argv if arg.startswith(("--config=", "-c="))]
                if len(configs) != 1 or not Path(configs[0]).is_absolute() or Path(configs[0]).resolve() != conf_out.resolve():
                    raise RuntimeError("server must use the rendered configuration's absolute path")
            os.kill(pid, signal.SIGHUP)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                after = monitor_json(http, "varz")
                if (after.get("server_id") == before.get("server_id")
                        and after.get("config_load_time")
                        and after["config_load_time"] != before["config_load_time"]):
                    return
                time.sleep(0.05)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as e:
            raise RuntimeError("live NATS reload could not be confirmed") from e
        raise RuntimeError("live NATS reload was not confirmed before timeout")
    return _reload


def main() -> int:
    from cluster.bobctl import hub_from_args, add_hub_args

    ap = argparse.ArgumentParser(description="Bob cluster join responder")
    add_hub_args(ap)
    ap.add_argument("--nats", default=os.environ.get("BOB_NATS_URL", "nats://127.0.0.1:4222"))
    ap.add_argument("--allow-loopback", action="store_true",
                    help="allow local/userspace joins with strict per-code and aggregate limits")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    hub = hub_from_args(args)
    pw = hub.reg.service_password("joind")
    asyncio.run(serve(hub, args.nats, pw, allow_loopback=args.allow_loopback))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
