"""Mesh auth, routing, join and revoke against a real nats-server (accounts config)."""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path

os.environ.setdefault("BOB_BCRYPT_ROUNDS", "4")

from cluster.bobctl import setup_streams, submit_spec  # noqa: E402
from cluster.joind import Hub, serve  # noqa: E402
from cluster.join import redeem  # noqa: E402
from cluster.mesh_worker import Executor, MeshWorker, NodeConfig  # noqa: E402
from cluster.registry import decode_join_code, inbox_prefix  # noqa: E402
from cluster.tests.mesh_util import MeshNats  # noqa: E402

try:
    import nats  # noqa: F401
    from nats.errors import NoServersError, TimeoutError as NatsTimeout  # noqa: F401
    HAVE_NATS = True
except ImportError:  # pragma: no cover
    HAVE_NATS = False

LEGACY = {"NUCBOX_TOKEN": "nucbox-test-token-aaaaaaaa", "DASHBOARD_TOKEN": "dash-test-token-bbbbbbbb",
          "MINI_TOKEN": "mini-test-token-cccccccc"}


def spec(job_id: str, cap: str, **kw) -> dict:
    s = {"v": 2, "id": job_id, "cap": cap, "repo": "rupret007/bob-ops-dashboard", "ref": "main",
         "timeout_s": 60, "load_ceiling": 32}
    if cap.startswith("llm."):
        s["prompt"] = "say hi"
    else:
        s["cmd"] = ["npm", "test"]
    s.update(kw)
    return s


async def connect(url, user, pw, **kw):
    import nats

    return await nats.connect(url, user=user, password=pw, allow_reconnect=False,
                              connect_timeout=3, **kw)


async def perm_violation(nc, coro_fn) -> bool:
    """Run an operation and report whether the server raised a permissions violation."""
    errors: list[str] = []
    orig = nc._error_cb

    async def cb(e):
        errors.append(str(e))
    nc._error_cb = cb
    try:
        await coro_fn()
        await nc.flush(timeout=2)
        for _ in range(10):  # -ERR can trail the PONG slightly
            if errors:
                break
            await asyncio.sleep(0.1)
    except Exception as e:  # noqa: BLE001
        errors.append(str(e))
    finally:
        nc._error_cb = orig
    return any("permissions violation" in e.lower() or "authorization" in e.lower() for e in errors)


@unittest.skipUnless(HAVE_NATS, "nats-py not installed")
class MeshIntegration(unittest.TestCase):
    def setUp(self):
        self.m = MeshNats()
        self.m.cfg_dir.mkdir(parents=True)
        (self.m.cfg_dir / "tokens.env").write_text("".join(f"{k}={v}\n" for k, v in LEGACY.items()))
        self.hub = Hub(self.m.cfg_dir, self.m.conf, self.m.store, self.m.reload,
                       port=self.m.port, http=self.m.http)
        self.hub.reg.ensure_service_users()
        self.hub.reg.import_legacy(self.m.cfg_dir / "tokens.env")
        self.hub.render()
        self.m.start()
        self.addCleanup(self.m.stop)
        asyncio.run(setup_streams(self.m.url, "nucbox", LEGACY["NUCBOX_TOKEN"],
                                  self.hub.reg.service_password("guestops")))

    # helpers -------------------------------------------------------------
    def join(self, name, role, caps=None):
        code, _ = self.hub.reg.mint(name, role, caps, self.m.url)
        self.hub.apply()
        return code

    async def _join_via_joind(self, code):
        stop = asyncio.Event()
        task = asyncio.create_task(serve(self.hub, self.m.url, self.hub.reg.service_password("joind"),
                                         reload_delay=0.2, stop=stop))
        await asyncio.sleep(0.4)
        try:
            return await redeem(code)
        finally:
            await asyncio.sleep(1.2)  # let the post-reply reload happen
            stop.set()
            await task

    def member(self, name, caps):
        code = self.join(name, "member", caps)
        url, reply = asyncio.run(self._join_via_joind(code))
        return reply

    # tests ---------------------------------------------------------------
    def test_join_code_single_use_and_token_works(self):
        code = self.join("box1", "member", ["llm.composer"])
        url, reply = asyncio.run(self._join_via_joind(code))
        self.assertEqual(reply["user"], "box1")
        self.assertEqual(reply["caps"], ["llm.composer"])

        async def check():
            nc = await connect(url, "box1", reply["token"], inbox_prefix=inbox_prefix("box1").encode())
            await nc.close()
            # The pending JOIN user is gone after reload: the code cannot be replayed.
            d = decode_join_code(code)
            with self.assertRaises(Exception):
                await connect(url, d["user"], d["s"])
        asyncio.run(check())
        conf = self.m.conf.read_text()
        self.assertNotIn(reply["token"], conf)
        self.assertNotIn(LEGACY["NUCBOX_TOKEN"], conf)
        self.assertEqual(oct(self.m.conf.stat().st_mode & 0o777), "0o600")

    def test_wrong_secret_rejected_by_joind(self):
        code = self.join("box2", "member", ["llm.composer"])
        d = decode_join_code(code)
        from cluster.registry import encode_join_code
        forged = encode_join_code(d["u"], d["user"], d["s"], "other-name", "member", d["x"])
        with self.assertRaises(Exception):
            asyncio.run(self._join_via_joind(forged))

    def test_capability_routing_and_permission_enforcement(self):
        a = self.member("codexbox", ["llm.codex.high"])
        b = self.member("cheapbox", ["llm.composer"])

        async def run():
            # Each node is only allowed its own capability consumer.
            nc = await connect(self.m.url, "cheapbox", b["token"], inbox_prefix=inbox_prefix("cheapbox").encode())
            nxt = "$JS.API.CONSUMER.MSG.NEXT.CAPJOBS."
            inbox = inbox_prefix("cheapbox") + ".x"
            self.assertTrue(await perm_violation(nc, lambda: nc.publish(nxt + "cap_llm_codex_high", b"1", reply=inbox)))
            # Positive control: its own capability consumer is allowed.
            self.assertFalse(await perm_violation(nc, lambda: nc.publish(nxt + "cap_llm_composer", b"1", reply=inbox)))
            # Members cannot enqueue jobs or spoof another node's heartbeat.
            self.assertTrue(await perm_violation(nc, lambda: nc.publish("cap.llm_codex_high.x1", b"{}")))
            self.assertTrue(await perm_violation(nc, lambda: nc.publish("caps.codexbox", b"{}")))
            await nc.close()

            # End to end: two workers, jobs land only on the node with the capability.
            stop = asyncio.Event()
            workers = []
            for name, rep in (("codexbox", a), ("cheapbox", b)):
                cfg = NodeConfig({"BOB_NATS_URL": self.m.url, "BOB_NODE_USER": name,
                                  "BOB_NODE_TOKEN": rep["token"], "BOB_ROLE": "member",
                                  "BOB_GRANTED_CAPS": ",".join(rep["caps"]), "BOB_MAX_LANES": "2",
                                  "BOB_HEARTBEAT_S": "5"})
                w = MeshWorker(cfg, Executor("member", dry_run=True), providers={},
                               detected={"llm.codex.high", "llm.composer", "shell.build"})
                workers.append(w)
            tasks = [asyncio.create_task(w.run(stop)) for w in workers]
            await asyncio.sleep(0.8)
            r1 = await submit_spec(self.m.url, "nucbox", LEGACY["NUCBOX_TOKEN"], spec("hard1", "llm.codex.high"), wait=8)
            r2 = await submit_spec(self.m.url, "nucbox", LEGACY["NUCBOX_TOKEN"], spec("cheap1", "llm.composer"), wait=8)
            stop.set()
            await asyncio.gather(*tasks)
            return r1, r2
        r1, r2 = asyncio.run(run())
        self.assertEqual(r1["result"]["node"], "codexbox")
        self.assertEqual(r2["result"]["node"], "cheapbox")
        self.assertEqual(r1["result"]["status"], "ok")

    def test_guest_is_walled_off(self):
        code = self.join("friend", "guest")
        url, g = asyncio.run(self._join_via_joind(code))
        self.assertEqual(g["caps"], ["sandbox.test"])

        async def run():
            nc = await connect(self.m.url, "friend", g["token"], inbox_prefix=inbox_prefix("friend").encode())
            inbox = inbox_prefix("friend") + ".x"
            # Cannot touch BOB queues (and they live in another account anyway).
            for subj in ("$JS.API.CONSUMER.MSG.NEXT.JOBS.workers",
                         "$JS.API.CONSUMER.MSG.NEXT.CAPJOBS.cap_llm_codex_high",
                         "$JS.API.STREAM.INFO.SANDBOX", "$JS.API.CONSUMER.CREATE.SANDBOX"):
                self.assertTrue(await perm_violation(nc, lambda s=subj: nc.publish(s, b"1", reply=inbox)), subj)
            self.assertFalse(await perm_violation(
                nc, lambda: nc.publish("$JS.API.CONSUMER.MSG.NEXT.SANDBOX.guests", b"1", reply=inbox)))
            # Cannot read results or heartbeats, cannot publish outside its own subjects.
            self.assertTrue(await perm_violation(nc, lambda: nc.subscribe("sandbox.results.>")))
            self.assertTrue(await perm_violation(nc, lambda: nc.subscribe("_INBOX.>")))
            self.assertTrue(await perm_violation(nc, lambda: nc.publish("sandbox.caps.someoneelse", b"{}")))
            self.assertTrue(await perm_violation(nc, lambda: nc.publish("sandbox.jobs.evil", b"{}")))
            await nc.close()

            # Guest pulls a sandbox job; the dashboard (BOB) sees its heartbeat + result via import.
            dash = await connect(self.m.url, "dashboard", LEGACY["DASHBOARD_TOKEN"])
            seen = {}

            async def cb(msg):
                seen.setdefault(msg.subject.split(".")[1], msg)
            await dash.subscribe("sandbox.caps.>", cb=cb)
            await dash.subscribe("sandbox.results.>", cb=cb)
            await dash.flush()
            stop = asyncio.Event()
            cfg = NodeConfig({"BOB_NATS_URL": self.m.url, "BOB_NODE_USER": "friend", "BOB_NODE_TOKEN": g["token"],
                              "BOB_ROLE": "guest", "BOB_GRANTED_CAPS": "sandbox.test,llm.codex.high",
                              "BOB_HEARTBEAT_S": "5"})
            w = MeshWorker(cfg, Executor("guest", dry_run=True), providers={},
                           detected={"sandbox.test", "llm.codex.high"})
            self.assertEqual(w.refresh_caps(), ["sandbox.test"])  # llm never effective for guests
            t = asyncio.create_task(w.run(stop))
            await asyncio.sleep(0.8)
            r = await submit_spec(self.m.url, "guestops", self.hub.reg.service_password("guestops"),
                                  spec("sbx1", "sandbox.test"), wait=8)
            await asyncio.sleep(0.3)
            stop.set()
            await t
            await dash.close()
            return r, seen
        r, seen = asyncio.run(run())
        self.assertEqual(r["result"]["node"], "friend")
        self.assertIn("caps", seen)
        self.assertIn("results", seen)
        hb = json.loads(seen["caps"].data)
        self.assertEqual(hb["llm"], {})

    def test_revoke_drops_connection_and_blocks_reconnect(self):
        rep = self.member("doomed", ["shell.build"])

        async def run():
            nc = await connect(self.m.url, "doomed", rep["token"], inbox_prefix=inbox_prefix("doomed").encode())
            self.assertTrue(nc.is_connected)
            self.hub.reg.revoke("doomed")
            await asyncio.to_thread(self.hub.apply)
            await asyncio.sleep(0.8)
            closed = not nc.is_connected
            await nc.close()
            with self.assertRaises(Exception):
                await connect(self.m.url, "doomed", rep["token"])
            return closed
        self.assertTrue(asyncio.run(run()))

    def test_pending_users_cannot_sniff_each_other(self):
        c1 = self.join("p-one", "member", ["llm.composer"])
        d1 = decode_join_code(c1)

        async def run():
            nc = await connect(self.m.url, d1["user"], d1["s"], inbox_prefix=inbox_prefix(d1["user"]).encode())
            sniff = await perm_violation(nc, lambda: nc.subscribe("_INBOX.>"))
            sniff2 = await perm_violation(nc, lambda: nc.subscribe("join.redeem"))
            await nc.close()
            return sniff and sniff2
        self.assertTrue(asyncio.run(run()))


if __name__ == "__main__":
    unittest.main()
