#!/usr/bin/env python3
"""Hub operator CLI (run on the NucBox).

  python3 -m cluster.bobctl init                       # service users + import legacy tokens
  python3 -m cluster.bobctl mint --name macbook --role member [--caps llm.codex.high,shell.build]
  python3 -m cluster.bobctl mint --name alex --role guest
  python3 -m cluster.bobctl revoke macbook
  python3 -m cluster.bobctl grant macbook --caps llm.composer,shell.test
  python3 -m cluster.bobctl list
  python3 -m cluster.bobctl setup-streams              # CAPJOBS (BOB) + SANDBOX (GUEST)
  python3 -m cluster.bobctl submit --kind routine --prompt-file p.txt --ref <sha>
  python3 -m cluster.bobctl caps --seconds 12          # live capability heartbeats

Until the rollout is approved this writes ~/.config/bob-cluster/nats.mesh.conf
and does NOT reload the live nats-server unless --live is given.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from cluster.capabilities import CAPABILITIES, GUEST_CAPS, cap_consumer, cap_filter
from cluster.registry import (
    CONSUMER_GUESTS, STREAM_CAPJOBS, STREAM_SANDBOX, STREAM_SANDBOX_RESULTS, RegistryError,
)

CFG = Path.home() / ".config" / "bob-cluster"
REPO_RAW = "https://raw.githubusercontent.com/rupret007/bob-ops-dashboard"


def add_hub_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--config-dir", default=str(CFG))
    ap.add_argument("--conf-out", default=str(CFG / "nats.mesh.conf"))
    ap.add_argument("--store-dir", default=str(Path.home() / ".local/state/bob-cluster/js"))
    ap.add_argument("--pidfile", default=str(CFG / "nats-server.pid"))
    ap.add_argument("--port", type=int, default=4222)
    ap.add_argument("--http", default="127.0.0.1:8232", help="monitoring bind (loopback only)")
    ap.add_argument("--live", action="store_true",
                    help="reload the running nats-server after changes (rollout only)")


def hub_from_args(args):
    from cluster.joind import Hub, signal_reload_from_pidfile

    if getattr(args, "live", False) or os.environ.get("BOB_MESH_LIVE") == "1":
        reload = signal_reload_from_pidfile(Path(args.pidfile))
    else:
        def reload() -> None:
            print("note: not live; rendered config only (rollout pending)", file=sys.stderr)
    if not args.http.startswith("127.0.0.1:"):
        raise SystemExit("--http must stay on 127.0.0.1")
    return Hub(Path(args.config_dir), Path(args.conf_out), args.store_dir, reload, port=args.port, http=args.http)


def hub_url(args) -> str:
    if getattr(args, "url", None):
        return args.url
    if os.environ.get("BOB_HUB_URL"):
        return os.environ["BOB_HUB_URL"]
    f = Path(args.config_dir) / "hub-url"
    if f.is_file():
        return f.read_text().strip()
    raise SystemExit("set --url nats://<nucbox tailscale ip>:4222 (or write it to ~/.config/bob-cluster/hub-url)")


def install_lines(code: str, ref: str) -> dict[str, str]:
    sh = (f"curl -fsSL {REPO_RAW}/{ref}/cluster/install/join.sh | "
          f"bash -s -- --ref {ref} --code '{code}'")
    ps = (f"$env:BOB_JOIN_CODE='{code}'; iwr -useb {REPO_RAW}/{ref}/cluster/install/join.ps1 "
          f"-OutFile $env:TEMP\\bob-join.ps1; powershell -ExecutionPolicy Bypass -File "
          f"$env:TEMP\\bob-join.ps1 -Ref {ref}")
    return {"macos_linux": sh, "windows": ps}


def _git_ref() -> str:
    import subprocess

    try:
        root = Path(__file__).resolve().parents[1]
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        if len(sha) == 40:
            return sha
    except (OSError, subprocess.SubprocessError):
        pass
    return "main"


# ------------------------------------------------------------------ commands

def cmd_init(args) -> int:
    hub = hub_from_args(args)
    hub.reg.ensure_service_users()
    tokens = Path(args.config_dir) / "tokens.env"
    added = hub.reg.import_legacy(tokens) if tokens.is_file() else []
    hub.apply()
    print(f"init: service users ready; legacy users hashed: {', '.join(added) or 'none new'}; "
          f"config at {hub.conf_out}")
    return 0


def cmd_mint(args) -> int:
    hub = hub_from_args(args)
    caps = [c.strip() for c in args.caps.split(",") if c.strip()] if args.caps else None
    try:
        code, info = hub.reg.mint(args.name, args.role, caps, hub_url(args), ttl_s=args.ttl_min * 60)
    except RegistryError as e:
        print(f"mint: {e}", file=sys.stderr)
        return 2
    hub.apply()
    lines = install_lines(code, args.ref or _git_ref())
    exp = time.strftime("%I:%M %p %Z", time.localtime(info["expires"]))
    print(f"Join code for '{info['name']}' ({info['role']}), single use, expires {exp}.")
    print(f"Caps granted: {', '.join(info['caps'])}")
    print("\nOn macOS / Linux, paste:\n  " + lines["macos_linux"])
    print("\nOn Windows (PowerShell), paste:\n  " + lines["windows"])
    return 0


def cmd_revoke(args) -> int:
    hub = hub_from_args(args)
    if not hub.reg.revoke(args.name):
        print(f"revoke: no active node or code named {args.name}", file=sys.stderr)
        return 1
    hub.apply()
    print(f"revoked {args.name}; its user is gone from the config"
          + (" and nats-server was reloaded" if args.live else " (apply live with --live after rollout)"))
    return 0


def cmd_grant(args) -> int:
    hub = hub_from_args(args)
    try:
        caps = hub.reg.set_caps(args.name, [c.strip() for c in args.caps.split(",") if c.strip()])
    except RegistryError as e:
        print(f"grant: {e}", file=sys.stderr)
        return 2
    hub.apply()
    print(f"{args.name} now granted: {', '.join(caps)}")
    return 0


def cmd_list(args) -> int:
    hub = hub_from_args(args)
    reg = hub.reg.read()
    hub.reg.expire()
    for name, n in sorted(reg["nodes"].items()):
        state = "revoked" if n.get("revoked") else "active"
        print(f"{name:20} {n['role']:7} {state:8} {','.join(n['caps'])}")
    for u, p in sorted(reg["pending"].items()):
        print(f"{p['name']:20} {p['role']:7} pending  expires {time.strftime('%I:%M %p', time.localtime(p['expires']))}")
    for u in sorted((reg.get("legacy") or {})):
        print(f"{u:20} legacy  active")
    return 0


async def setup_streams(url: str, bob_user: str, bob_pw: str, guest_pw: str | None) -> list[str]:
    import nats
    from nats.js.api import AckPolicy, ConsumerConfig, RetentionPolicy, StorageType, StreamConfig
    from nats.js.errors import BadRequestError

    done = []

    async def upsert_stream(js, cfg):
        try:
            await js.add_stream(cfg)
        except BadRequestError:
            await js.update_stream(cfg)
        done.append(f"stream {cfg.name}")

    async def upsert_consumer(js, stream, cfg):
        try:
            await js.add_consumer(stream, cfg)
        except BadRequestError:
            pass
        done.append(f"consumer {stream}.{cfg.durable_name}")

    nc = await nats.connect(url, user=bob_user, password=bob_pw)
    js = nc.jetstream()
    await upsert_stream(js, StreamConfig(name=STREAM_CAPJOBS, subjects=["cap.>"],
                                         retention=RetentionPolicy.WORK_QUEUE,
                                         storage=StorageType.FILE, max_age=24 * 3600))
    for cap in sorted(CAPABILITIES):
        if cap in GUEST_CAPS:
            continue
        await upsert_consumer(js, STREAM_CAPJOBS, ConsumerConfig(
            durable_name=cap_consumer(cap), ack_policy=AckPolicy.EXPLICIT, ack_wait=30 * 60,
            max_deliver=3, filter_subject=cap_filter(cap)))
    await nc.close()
    if guest_pw:
        nc = await nats.connect(url, user="guestops", password=guest_pw, inbox_prefix=b"_INBOX_guestops")
        js = nc.jetstream()
        await upsert_stream(js, StreamConfig(name=STREAM_SANDBOX, subjects=["sandbox.jobs.*"],
                                             retention=RetentionPolicy.WORK_QUEUE,
                                             storage=StorageType.FILE, max_age=24 * 3600))
        await upsert_stream(js, StreamConfig(name=STREAM_SANDBOX_RESULTS, subjects=["sandbox.results.>"],
                                             retention=RetentionPolicy.LIMITS,
                                             storage=StorageType.FILE, max_age=7 * 24 * 3600))
        await upsert_consumer(js, STREAM_SANDBOX, ConsumerConfig(
            durable_name=CONSUMER_GUESTS, ack_policy=AckPolicy.EXPLICIT, ack_wait=30 * 60,
            max_deliver=3, filter_subject="sandbox.jobs.*"))
        await nc.close()
    return done


def _nucbox_token(config_dir: Path) -> str:
    from cluster.registry import _read_env

    tok = os.environ.get("BOB_NODE_TOKEN") or _read_env(config_dir / "tokens.env").get("NUCBOX_TOKEN")
    if not tok:
        raise SystemExit("need NUCBOX_TOKEN in tokens.env or BOB_NODE_TOKEN")
    return tok


def cmd_setup_streams(args) -> int:
    hub = hub_from_args(args)
    gp = hub.reg.service_password("guestops")
    for line in asyncio.run(setup_streams(args.nats, "nucbox", _nucbox_token(Path(args.config_dir)), gp)):
        print("ok", line)
    return 0


async def submit_spec(url: str, user: str, pw: str, spec: dict, wait: float = 0.0) -> dict | None:
    import nats

    from cluster.routing import subject_for

    kw = {"inbox_prefix": b"_INBOX_guestops"} if user == "guestops" else {}
    nc = await nats.connect(url, user=user, password=pw, **kw)
    js = nc.jetstream()
    result = None
    sub = None
    if wait > 0:
        subj = (f"sandbox.results.*.{spec['id']}" if spec["cap"] in GUEST_CAPS else f"results.*.{spec['id']}")
        sub = await nc.subscribe(subj)
        await nc.flush()
    ack = await js.publish(subject_for(spec), json.dumps(spec).encode())
    if sub is not None:
        try:
            msg = await sub.next_msg(timeout=wait)
            result = json.loads(msg.data)
        except asyncio.TimeoutError:
            result = None
    await nc.close()
    return {"stream": ack.stream, "seq": ack.seq, "result": result}


def cmd_submit(args) -> int:
    import secrets as _s

    from cluster.routing import pick_capability, validate_cap_spec

    cap = args.cap or pick_capability(args.kind)
    spec = {"v": 2, "id": args.id or f"j{int(time.time())}-{_s.token_hex(3)}", "cap": cap,
            "repo": args.repo, "ref": args.ref, "timeout_s": args.timeout, "load_ceiling": args.load_ceiling}
    if cap.startswith("llm."):
        spec["prompt"] = Path(args.prompt_file).read_text(encoding="utf-8")
    else:
        spec["cmd"] = args.cmd.split() if args.cmd else ["npm", "test"]
    spec = validate_cap_spec(spec, guest=cap in GUEST_CAPS)
    cfg = Path(args.config_dir)
    if cap in GUEST_CAPS:
        from cluster.joind import Hub  # noqa: F401
        from cluster.registry import Registry
        user, pw = "guestops", Registry(cfg).service_password("guestops")
    else:
        user, pw = "nucbox", _nucbox_token(cfg)
    out = asyncio.run(submit_spec(args.nats, user, pw, spec, wait=args.wait))
    print(json.dumps({"id": spec["id"], "cap": cap, **(out or {})}, indent=1))
    return 0


def cmd_caps(args) -> int:
    import nats

    from cluster.registry import _read_env

    async def run():
        tok = _read_env(Path(args.config_dir) / "tokens.env").get("DASHBOARD_TOKEN") or os.environ.get("BOB_DASHBOARD_TOKEN")
        nc = await nats.connect(args.nats, user="dashboard", password=tok)
        seen: dict[str, dict] = {}

        async def cb(msg):
            try:
                d = json.loads(msg.data)
                seen[d.get("node", msg.subject)] = d
            except ValueError:
                pass
        await nc.subscribe("caps.>", cb=cb)
        await nc.subscribe("sandbox.caps.>", cb=cb)
        await asyncio.sleep(args.seconds)
        await nc.close()
        return seen

    seen = asyncio.run(run())
    for node, d in sorted(seen.items()):
        llm = ",".join(p for p, s in (d.get("llm") or {}).items() if s.get("logged_in")) or "-"
        print(f"{node:16} {d.get('role','?'):7} {d.get('os','?'):7} lanes {d.get('active',0)}/{d.get('max_lanes',0)} "
              f"load {d.get('load1')} llm[{llm}] caps[{','.join(d.get('caps') or [])}]")
    if not seen:
        print("no heartbeats seen")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bob cluster hub control")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def p(name, fn, **kw):
        sp = sub.add_parser(name, **kw)
        add_hub_args(sp)
        sp.add_argument("--nats", default=os.environ.get("BOB_NATS_URL", "nats://127.0.0.1:4222"))
        sp.set_defaults(fn=fn)
        return sp

    p("init", cmd_init)
    sp = p("mint", cmd_mint)
    sp.add_argument("--name", required=True)
    sp.add_argument("--role", choices=["member", "guest"], default="member")
    sp.add_argument("--caps")
    sp.add_argument("--ttl-min", type=int, default=30)
    sp.add_argument("--url")
    sp.add_argument("--ref")
    sp = p("revoke", cmd_revoke)
    sp.add_argument("name")
    sp = p("grant", cmd_grant)
    sp.add_argument("name")
    sp.add_argument("--caps", required=True)
    p("list", cmd_list)
    p("setup-streams", cmd_setup_streams)
    sp = p("submit", cmd_submit)
    sp.add_argument("--kind", default="routine")
    sp.add_argument("--cap")
    sp.add_argument("--repo", default="rupret007/bob-ops-dashboard")
    sp.add_argument("--ref", required=True)
    sp.add_argument("--prompt-file")
    sp.add_argument("--cmd")
    sp.add_argument("--id")
    sp.add_argument("--timeout", type=float, default=1800)
    sp.add_argument("--load-ceiling", type=float, default=12)
    sp.add_argument("--wait", type=float, default=0)
    sp = p("caps", cmd_caps)
    sp.add_argument("--seconds", type=float, default=12)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
