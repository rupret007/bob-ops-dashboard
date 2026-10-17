"""Lease reply subjects must never turn the hub into a member's publish proxy."""

import asyncio
import json
import secrets
import unittest
from unittest.mock import patch

from cluster import lanes, lanes_perms
from cluster.tests.nats_test_util import EphemeralNats, find_nats_binary, require_or_skip


@require_or_skip(find_nats_binary(), "nats-server binary not installed")
class TestLeaseReplyGuard(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import nats

        passwords = {u: secrets.token_urlsafe(18) for u in ("hub", "nuc", "mini", "observer")}
        users = {
            "hub": (passwords["hub"], lanes_perms.operator()),
            "nuc": (passwords["nuc"], lanes_perms.host("nuc", ["codex"])),
            "mini": (passwords["mini"], lanes_perms.host("mini", ["codex"])),
            # Test-only wire observer: no publish privileges, all subjects visible.
            "observer": (passwords["observer"], {
                "publish": {"deny": [">"]}, "subscribe": {"allow": [">"]},
            }),
        }
        self.srv = EphemeralNats(extra_conf=lanes_perms.render_users(users))
        self.addCleanup(self.srv.stop)
        self.srv.start()
        self.errors = []
        self.error_received = asyncio.Event()

        for user in users:
            async def on_error(error, user=user):
                self.errors.append((user, str(error)))
                self.error_received.set()

            nc = await nats.connect(
                self.srv.url, user=user, password=passwords[user], inbox_prefix=f"_INBOX_{user}",
                error_cb=on_error, allow_reconnect=False,
            )
            self.addAsyncCleanup(nc.close)
            setattr(self, user, nc)
        self.js = self.hub.jetstream()
        await lanes.ensure_lanes(self.js, {"codex": {"nuc": 2, "mini": 2}}, ack_wait_s=60)
        self.kv = await lanes.lease_bucket(self.js)
        self.svc = await lanes.LeaseService(self.hub, self.kv).start()
        self.addAsyncCleanup(self.svc.stop)
        await self.hub.flush()
        self.seed_value = json.dumps({"owner": "mini", "meta": {"host": "mini"}}).encode()
        self.seed_revision = await self.kv.create("o.seed.pr.1", self.seed_value)
        await lanes.enqueue(self.js, "codex", "pending", {}, "a" * 40, host="mini")
        deliveries = await lanes.pull(
            await lanes.subscriber(self.mini.jetstream(), "codex", "mini"), timeout=2,
        )
        self.assertEqual(len(deliveries), 1)
        self.ack_subject = deliveries[0].reply
        self.assertTrue(self.ack_subject.startswith("$JS.ACK.LANES.lane_codex_mini."))

    async def _state(self):
        state = {}
        for name in (lanes.LEASE_STREAM, lanes.LANES_STREAM, lanes.DLQ_STREAM):
            info = await self.js.stream_info(name)
            state[name] = (info.state.messages, info.state.last_seq)
        return state

    async def test_no_publication_to_any_foreign_reply_target(self):
        targets = (
            "$KV.LANE_LEASES.o.stolen.pr.9",
            "$KV.LANE_LEASES.o.seed.pr.1",
            "lanes.codex.mini.evil",
            "lanes_dlq.codex.nuc",
            "_INBOX_conductor.probe",
            "_INBOX_mini.probe",
            "_INBOX_hub.probe",
            "$JS.API.STREAM.INFO.LANES",
            "$JS.API.STREAM.DELETE.LANES",
            "lanesvc.lease.mini.release",
            "lanesvc.lease.nuc.acquire",
            "_INBOX_nuc",  # The namespace separator is mandatory.
            "_INBOX_nucbox.probe",
            "_INBOX_nuc-evil.probe",
            "_INBOX.probe",
        )
        bodies = (
            # A successful acquire, an owner mismatch, invalid JSON, and an oversized request.
            json.dumps({"key": "o.request.pr.1", "owner": "nuc"}).encode(),
            json.dumps({"key": "o.request.pr.1", "owner": "mini", "host": "mini",
                        "meta": {"host": "mini"}}).encode(),
            b"not-json-synthetic-private-data",
            b"x" * (lanes.MAX_META_BYTES + 513),
        )
        initial = await self._state()
        for target in targets:
            # Keep the attack request itself off the observed reply target.
            op = "renew" if target == lanes.lease_subject("nuc", "acquire") else "acquire"
            for body in bodies:
                with self.subTest(target=target, body_size=len(body)):
                    capture = await self.observer.subscribe(target)
                    await self.observer.flush()
                    try:
                        with self.assertLogs("cluster.lanes", level="WARNING") as logs:
                            await self.nuc.publish(lanes.lease_subject("nuc", op), body, reply=target)
                            # The same subscription processes callbacks serially. This valid request
                            # is a barrier proving the preceding malicious request has been handled.
                            response = await self.nuc.request(
                                lanes.lease_subject("nuc", "holder"),
                                b'{"key":"o.seed.pr.1"}', timeout=2,
                            )
                            self.assertTrue(json.loads(response.data)["ok"])
                            await self.hub.flush()
                            await self.observer.flush()
                            self.assertEqual(capture.pending_msgs, 0, "no reply bytes may land on the wire")
                        self.assertEqual([record.getMessage() for record in logs.records], [
                            "Dropped lease request with reply outside the requesting host's inbox",
                        ])
                        self.assertEqual(self.errors, [])
                    finally:
                        await capture.unsubscribe()
                    # Stop observing before our own stream-info validation calls.
                    self.assertEqual(await self._state(), initial)
                    seed = await self.kv.get("o.seed.pr.1")
                    self.assertEqual((seed.value, seed.revision), (self.seed_value, self.seed_revision))
                    self.assertIsNone(await lanes.holder(self.kv, "o.stolen.pr.9"))
                    self.assertIsNone(await lanes.holder(self.kv, "o.request.pr.1"))
                    consumer = await self.js.consumer_info(lanes.LANES_STREAM, "lane_codex_mini")
                    self.assertEqual(consumer.num_ack_pending, 1)

    async def test_forged_ack_is_blocked_by_broker_and_by_service(self):
        from nats.aio.msg import Msg

        capture = await self.observer.subscribe(self.ack_subject)
        await self.observer.flush()
        initial = await self._state()
        subject = lanes.lease_subject("nuc", "acquire")
        body = b'{"key":"o.request.pr.1","owner":"nuc"}'
        # NATS 2.15.1 itself refuses $JS.ACK reply addresses before delivery to the service.
        await self.nuc.publish(subject, body, reply=self.ack_subject)
        await self.nuc.flush()
        # nats-py dispatches error callbacks separately from the flush/PONG reader.
        await asyncio.wait_for(self.error_received.wait(), timeout=2)
        self.assertEqual(self.errors, [(
            "nuc", f'nats: permissions violation for publish with reply of "{self.ack_subject.lower()}"',
        )])
        # Exercise the service boundary as well, using the real hub and KV, so this test cannot
        # pass solely because of the broker's current ACK protection or the hub's permissions.
        with patch.object(self.hub, "publish", wraps=self.hub.publish) as publish:
            with self.assertLogs("cluster.lanes", level="WARNING"):
                await self.svc._on_msg(Msg(_client=self.hub, subject=subject, data=body, reply=self.ack_subject))
                publish.assert_not_awaited()
        await self.hub.flush()
        await self.observer.flush()
        self.assertEqual(capture.pending_msgs, 0)
        self.assertEqual(await self._state(), initial)
        self.assertIsNone(await lanes.holder(self.kv, "o.request.pr.1"))
        consumer = await self.js.consumer_info(lanes.LANES_STREAM, "lane_codex_mini")
        self.assertEqual(consumer.num_ack_pending, 1)

    async def test_own_inbox_replies_work_for_both_hosts_and_errors(self):
        for host in ("nuc", "mini"):
            with self.subTest(host=host):
                nc = getattr(self, host)
                client = lanes.LeaseClient(nc, host)
                key = f"o.control.pr.{host}"
                lease = await client.acquire(key, f"{host}/lane-1")
                self.assertEqual((await client.holder(key))["owner"], f"{host}/lane-1")
                lease = await client.renew(lease)
                self.assertTrue(await client.release(lease))
                response = await nc.request(lanes.lease_subject(host, "acquire"), b"invalid-json", timeout=2)
                self.assertTrue(response.subject.startswith(f"_INBOX_{host}."))
                self.assertEqual(json.loads(response.data)["error"], "forbidden")
        self.assertEqual(self.errors, [])
