"""Karen L1: prove the stage-2 permission delta (cluster/lanes_perms.py) on a real nats-server.

The delta is NOT applied to the live hub; this renders it into a throwaway server config only.
"""

import asyncio
import json
import secrets
import unittest

from cluster import lanes, lanes_perms
from cluster.tests.nats_test_util import EphemeralNats, find_nats_binary


def _users():
    pw = {u: secrets.token_urlsafe(18) for u in ("hub", "conductor", "nuc", "mini")}
    users = {
        "hub": (pw["hub"], lanes_perms.operator()),
        "conductor": (pw["conductor"], lanes_perms.conductor()),
        "nuc": (pw["nuc"], lanes_perms.host("nuc", ["codex", "gemini"])),
        "mini": (pw["mini"], lanes_perms.host("mini", ["codex"])),
    }
    return pw, lanes_perms.render_users(users)


@unittest.skipUnless(find_nats_binary(), "nats-server binary not installed")
class TestStage2Permissions(unittest.TestCase):
    def setUp(self):
        self.pw, conf = _users()
        self.srv = EphemeralNats(extra_conf=conf)
        self.srv.start()

    def tearDown(self):
        self.srv.stop()

    async def _connect(self, user, inbox):
        import nats

        errs = []

        async def on_err(e):
            errs.append(str(e))

        nc = await nats.connect(self.srv.url, user=user, password=self.pw[user], inbox_prefix=inbox,
                                error_cb=on_err, allow_reconnect=False)
        return nc, nc.jetstream(), errs

    async def _denied(self, nc, errs, subject, what="publish"):
        await nc.flush()
        for _ in range(30):
            if any("permissions violation" in e.lower() and subject.lower() in e.lower() and what in e.lower() for e in errs):
                return
            await asyncio.sleep(0.05)
        self.fail(f"expected {what} permission violation for {subject}; errors: {errs}")

    def test_member_cannot_raw_put_or_delete_leases_and_cannot_touch_other_hosts(self):
        async def go():
            hub, hjs, herrs = await self._connect("hub", "_INBOX_hub")
            cond, cjs, _ = await self._connect("conductor", lanes.role_inbox_prefix("conductor"))
            nuc, njs, nerrs = await self._connect("nuc", lanes.inbox_prefix("nuc"))
            mini, mjs, merrs = await self._connect("mini", lanes.inbox_prefix("mini"))
            try:
                kv = await lanes.lease_bucket(hjs, ttl_s=60)
                svc = await lanes.LeaseService(hub, kv).start()
                await lanes.ensure_lanes(hjs, {"codex": {"nuc": 2, "mini": 2}, "gemini": {"nuc": 1}}, ack_wait_s=30)
                key = lanes.lease_key("rupret007/webjam", "pr", "390")
                mini_leases, nuc_leases = lanes.LeaseClient(mini, "mini"), lanes.LeaseClient(nuc, "nuc")

                # Legit path still works for a host: lease via the service.
                held = await mini_leases.acquire(key, "mini/lane-1", {"job": "auto-webjam-pr390"})
                rev0 = held.revision
                with self.assertRaises(lanes.LeaseHeld):
                    await nuc_leases.acquire(key, "nuc/lane-2")  # cross-host exclusivity kept

                # 1. Raw KV put (steal) and raw KV delete (drop) are denied by the server.
                steal = json.dumps({"owner": "nuc/attacker"}).encode()
                await nuc.publish(f"$KV.{lanes.LEASE_BUCKET}.{key}", steal)
                await self._denied(nuc, nerrs, f"$KV.{lanes.LEASE_BUCKET}.{key}")
                nerrs.clear()
                await nuc.publish(f"$KV.{lanes.LEASE_BUCKET}.{key}", b"", headers={"KV-Operation": "DEL"})
                await self._denied(nuc, nerrs, f"$KV.{lanes.LEASE_BUCKET}.{key}")
                nerrs.clear()
                with self.assertRaises(Exception):  # through the JetStream client too: no ack ever comes back
                    await njs.publish(f"$KV.{lanes.LEASE_BUCKET}.{key}", steal, timeout=0.5)
                for api in (f"$JS.API.STREAM.MSG.DELETE.{lanes.LEASE_STREAM}", f"$JS.API.STREAM.PURGE.{lanes.LEASE_STREAM}",
                            f"$JS.API.STREAM.DELETE.{lanes.LEASE_STREAM}"):
                    nerrs.clear()
                    await nuc.publish(api, b"{}")
                    await self._denied(nuc, nerrs, api)

                # 2. The service binds the owner to the requesting host; revisions are not enough.
                with self.assertRaises(lanes.LeaseForbidden):
                    await nuc_leases.acquire(lanes.lease_key("o/r", "pr", "1"), "mini/lane-9")
                with self.assertRaises(lanes.LeaseForbidden):
                    await nuc_leases.renew(lanes.Lease(key, "mini/lane-1", rev0))
                with self.assertRaises(lanes.LeaseForbidden):
                    await nuc_leases.release(lanes.Lease(key, "mini/lane-1", rev0))
                with self.assertRaises(lanes.LeaseLost):
                    await nuc_leases.renew(lanes.Lease(key, "nuc/lane-2", rev0))
                self.assertFalse(await nuc_leases.release(lanes.Lease(key, "nuc/lane-2", rev0)))
                nerrs.clear()
                await nuc.publish(lanes.lease_subject("mini", "release"),
                                  json.dumps({"key": key, "owner": "mini/lane-1", "revision": rev0}).encode())
                await self._denied(nuc, nerrs, lanes.lease_subject("mini", "release"))

                # The lease is untouched: same owner, same revision, and the real holder can still renew.
                entry = await kv.get(key)
                self.assertEqual(entry.revision, rev0)
                self.assertEqual(json.loads(entry.value)["owner"], "mini/lane-1")
                renewed = await mini_leases.renew(held)
                self.assertGreater(renewed.revision, rev0)
                self.assertTrue(await mini_leases.release(renewed))

                # 3. Per-host consumers: nuc cannot pull or ack mini's work, or enqueue, or sniff inboxes.
                self.assertTrue(await lanes.enqueue(cjs, "codex", "auto-webjam-pr391", {}, "a" * 40, host="mini"))
                self.assertTrue(await lanes.enqueue(cjs, "codex", "auto-webjam-pr392", {}, "b" * 40, host="nuc"))
                mjob = await lanes.pull(await lanes.subscriber(mjs, "codex", "mini"), timeout=2)
                self.assertEqual(len(mjob), 1)
                forged_ack = mjob[0].reply
                self.assertTrue(forged_ack.startswith("$JS.ACK.LANES.lane_codex_mini."))
                nerrs.clear()
                await nuc.publish(forged_ack, b"+TERM")
                await self._denied(nuc, nerrs, "$JS.ACK.LANES.lane_codex_mini.")
                nerrs.clear()
                await nuc.publish("$JS.API.CONSUMER.MSG.NEXT.LANES.lane_codex_mini", b'{"batch":1}')
                await self._denied(nuc, nerrs, "$JS.API.CONSUMER.MSG.NEXT.LANES.lane_codex_mini")
                nerrs.clear()
                await nuc.publish("lanes.codex.mini.injected", b"{}")
                await self._denied(nuc, nerrs, "lanes.codex.mini.injected")
                for sniff in ("_INBOX.>", "_INBOX_mini.>", ">"):
                    nerrs.clear()
                    await nuc.subscribe(sniff)
                    await self._denied(nuc, nerrs, sniff, what="subscription")
                info = await hjs.consumer_info(lanes.LANES_STREAM, "lane_codex_mini")
                self.assertEqual(info.num_ack_pending, 1, "forged +TERM must not settle mini's job")

                # nuc still does its own work end to end.
                njob = await lanes.pull(await lanes.subscriber(njs, "codex", "nuc"), timeout=2)
                self.assertEqual([json.loads(m.data)["job"] for m in njob], ["auto-webjam-pr392"])
                mine = await lanes.take(njob[0], nuc_leases, lanes.lease_key("rupret007/webjam", "pr", "392"), "nuc/lane-3")
                self.assertIsNotNone(mine)
                await njob[0].ack()
                await mjob[0].ack()
                await nuc.flush()
                await mini.flush()
                await asyncio.sleep(0.2)
                self.assertEqual((await hjs.stream_info(lanes.LANES_STREAM)).state.messages, 0)
                self.assertEqual([e for e in merrs if "permissions" in e.lower()], [], "legit host path needs no extra grants")
                await svc.stop()
            finally:
                for nc in (nuc, mini, cond, hub):
                    await nc.close()

        asyncio.run(go())


class TestStage2PermissionShape(unittest.TestCase):
    def test_no_member_kv_or_shared_ack_grants(self):
        for host, provs in (("nuc", ["codex", "gemini"]), ("mini", ["codex"])):
            perms = lanes_perms.host(host, provs)
            allow = perms["publish"]["allow"]
            self.assertFalse([s for s in allow if s.startswith("$KV")], allow)
            self.assertFalse([s for s in allow if s.startswith("lanes.")], allow)
            self.assertFalse([s for s in allow if s.startswith("$JS.API.STREAM")], allow)
            for s in allow:
                if s.startswith("$JS.ACK.") or s.startswith("$JS.API.CONSUMER."):
                    self.assertTrue(s.split(".")[3 if s.startswith("$JS.ACK.") else 6].endswith("_" + host), s)
            self.assertEqual(perms["subscribe"]["allow"], [f"_INBOX_{host}.>"])
            self.assertIn("$KV.>", perms["publish"]["deny"])

    def test_lanes_md_matches_module(self):
        from pathlib import Path

        doc = (Path(__file__).resolve().parents[1] / "LANES.md").read_text()
        self.assertNotIn("$KV.LANE_LEASES.>` for members", doc)
        for needle in ("lane_<provider>_<host>", "$JS.ACK.LANES.lane_<provider>_<host>.>",
                       "lanesvc.lease.<host>.<op>", "_INBOX_<host>.>", "lanes_perms.py"):
            self.assertIn(needle, doc)


if __name__ == "__main__":
    unittest.main()
