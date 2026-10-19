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

import nats  # noqa: E402, F401
from nats.errors import NoServersError, TimeoutError as NatsTimeout  # noqa: E402, F401

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
                                         reload_delay=0.2, stop=stop, allow_loopback=True))
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
            self.assertFalse(await perm_violation(nc, lambda: nc.publish(nxt + "cap_llm_composer_n_cheapbox", b"1", reply=inbox)))
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
                nc, lambda: nc.publish("$JS.API.CONSUMER.MSG.NEXT.SANDBOX.guests_n_friend", b"1", reply=inbox)))
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

    def test_cli_revoke_without_live_flag_confirms_reload_and_disconnect(self):
        from cluster.bobctl import main
        from cluster.joind import monitor_json

        rep = self.member("cli-doomed", ["shell.build"])
        pidfile = self.m.cfg_dir / "nats.pid"
        pidfile.write_text(str(self.m.proc.pid))

        async def run():
            nc = await connect(self.m.url, "cli-doomed", rep["token"])
            try:
                before = await asyncio.to_thread(monitor_json, self.m.http, "varz")
                args = ["revoke", "cli-doomed", "--config-dir", str(self.m.cfg_dir),
                        "--conf-out", str(self.m.conf), "--pidfile", str(pidfile),
                        "--store-dir", self.m.store, "--port", str(self.m.port), "--http", self.m.http]
                self.assertEqual(await asyncio.to_thread(main, args), 0)
                after = await asyncio.to_thread(monitor_json, self.m.http, "varz")
                self.assertNotEqual(before["config_load_time"], after["config_load_time"])
                for _ in range(20):
                    if not nc.is_connected:
                        break
                    await asyncio.sleep(0.05)
                self.assertFalse(nc.is_connected)
                with self.assertRaises(Exception):
                    await connect(self.m.url, "cli-doomed", rep["token"])
            finally:
                await nc.close()
        asyncio.run(run())

    def test_pending_user_cannot_spoof_other_source_subject(self):
        first = decode_join_code(self.join("first", "member"))
        second = decode_join_code(self.join("second", "member"))

        async def run():
            nc = await connect(self.m.url, first["user"], first["s"],
                               inbox_prefix=inbox_prefix(first["user"]).encode())
            try:
                self.assertTrue(await perm_violation(nc, lambda: nc.publish(
                    f"join.redeem.{second['user']}", b"{}")))
                self.assertFalse(await perm_violation(nc, lambda: nc.publish(
                    f"join.redeem.{first['user']}", b"{}")))
            finally:
                await nc.close()
        asyncio.run(run())

    def test_guestops_cannot_delete_or_read_streams_and_legacy_revoke_disconnects(self):
        async def run():
            go = await connect(self.m.url, "guestops", self.hub.reg.service_password("guestops"),
                               inbox_prefix=b"_INBOX_guestops")
            ib = "_INBOX_guestops.x"
            for subj in ("$JS.API.STREAM.DELETE.SANDBOX", "$JS.API.STREAM.MSG.GET.SANDBOX_RESULTS",
                         "$JS.API.STREAM.PURGE.SANDBOX", "$JS.API.CONSUMER.DELETE.SANDBOX.guests"):
                self.assertTrue(await perm_violation(go, lambda s=subj: go.publish(s, b"{}", reply=ib)), subj)
            await go.close()
            mini = await connect(self.m.url, "mini", LEGACY["MINI_TOKEN"])
            self.hub.reg.revoke("mini")
            await asyncio.to_thread(self.hub.apply)
            await asyncio.sleep(0.8)
            dropped = not mini.is_connected
            await mini.close()
            with self.assertRaises(Exception):
                await connect(self.m.url, "mini", LEGACY["MINI_TOKEN"])
            return dropped
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

    def check_cross_node_ack_isolation(self, role):
        from cluster.bobctl import delivery_consumer
        from cluster.routing import subject_for
        cap = 'sandbox.test' if role == 'guest' else 'shell.test'
        replies = {}
        # Direct registry redemption keeps this test about broker authorization.
        for name in ('victim', 'attacker'):
            code, _ = self.hub.reg.mint(name, role, [cap], self.m.url)
            d = decode_join_code(code)
            replies[name] = self.hub.reg.redeem(d['user'], d['s'], name)
        self.hub.apply()
        asyncio.run(setup_streams(self.m.url, 'nucbox', LEGACY['NUCBOX_TOKEN'],
                                  self.hub.reg.service_password('guestops'), self.hub.reg.read()['nodes']))

        async def run():
            guest = role == 'guest'
            stream = 'SANDBOX' if guest else 'CAPJOBS'
            admin_user = 'guestops' if guest else 'nucbox'
            password = self.hub.reg.service_password('guestops') if guest else LEGACY['NUCBOX_TOKEN']
            admin = await connect(self.m.url, admin_user, password, inbox_prefix=inbox_prefix(admin_user).encode())
            victim = await connect(self.m.url, 'victim', replies['victim']['token'], inbox_prefix=b'_INBOX_victim')
            attacker = await connect(self.m.url, 'attacker', replies['attacker']['token'], inbox_prefix=b'_INBOX_attacker')
            try:
                js = admin.jetstream()
                consumer = delivery_consumer(cap, 'victim').durable_name
                pull = await victim.jetstream().pull_subscribe_bind(stream=stream, durable=consumer)
                for i, payload in enumerate((b'+ACK', b'-NAK', b'+TERM', b'+WPI')):
                    job = spec('isolation' + str(i), cap)
                    await js.publish(subject_for(job, 'victim'), json.dumps(job).encode())
                    msg, = await pull.fetch(1, timeout=3)
                    self.assertEqual((await js.consumer_info(stream, consumer)).num_ack_pending, 1)
                    # Test both an exact stolen reply and Karen's forged ts/pending tokens.
                    tokens = msg.reply.split('.')
                    tokens[-2:] = ['1', '999']
                    for ack_subject in (msg.reply, '.'.join(tokens)):
                        self.assertTrue(await perm_violation(attacker, lambda: attacker.publish(ack_subject, payload)))
                        info = await js.consumer_info(stream, consumer)
                        self.assertEqual(info.num_ack_pending, 1)
                        self.assertEqual(info.num_redelivered, 0)
                        self.assertEqual((await js.stream_info(stream)).state.messages, 1)
                    await msg.ack_sync(timeout=2)
                    self.assertEqual((await js.consumer_info(stream, consumer)).num_ack_pending, 0)
                    self.assertEqual((await js.stream_info(stream)).state.messages, 0)
                # Peer cannot pull victim's private consumer or read its inbox either.
                self.assertTrue(await perm_violation(attacker, lambda: attacker.publish(
                    f'$JS.API.CONSUMER.MSG.NEXT.{stream}.{consumer}', b'1', reply='_INBOX_attacker.x')))
                self.assertTrue(await perm_violation(attacker, lambda: attacker.subscribe('_INBOX_victim.>')))
            finally:
                await attacker.close()
                await victim.close()
                await admin.close()
        asyncio.run(run())

    def test_guest_peer_ack_nak_term_wpi_denied_and_owner_ack_works(self):
        self.check_cross_node_ack_isolation('guest')

    def test_member_peer_ack_nak_term_wpi_denied_and_owner_ack_works(self):
        self.check_cross_node_ack_isolation('member')

    def test_legacy_worker_cannot_sniff_inbox_or_ack_another_consumer(self):
        async def run():
            mini = await connect(self.m.url, 'mini', LEGACY['MINI_TOKEN'], inbox_prefix=b'_INBOX_mini')
            try:
                for subject in ('_INBOX_macbook.>', '_INBOX_nucbox.>', '_INBOX.>', '$JS.API.>', '$JS.ACK.>'):
                    self.assertTrue(await perm_violation(mini, lambda: mini.subscribe(subject)))
                for payload in (b'+ACK', b'-NAK', b'+TERM', b'+WPI'):
                    for consumer in ('workers_macbook', 'workers'):
                        self.assertTrue(await perm_violation(mini, lambda: mini.publish(
                            f'$JS.ACK.JOBS.{consumer}.1.1.1.1.1', payload)))
                self.assertTrue(await perm_violation(mini, lambda: mini.publish('results.macbook.job', b'{}')))
                self.assertFalse(await perm_violation(mini, lambda: mini.subscribe('_INBOX_mini.>')))
            finally:
                await mini.close()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
