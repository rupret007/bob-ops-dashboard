import asyncio
import json
import unittest

from cluster import lanes
from cluster.tests.nats_test_util import EphemeralNats, find_nats_binary


class TestLaneNames(unittest.TestCase):
    def test_lease_key(self):
        self.assertEqual(lanes.lease_key("rupret007/webjam", "pr", "390"), "rupret007.webjam.pr.390")
        self.assertEqual(lanes.lease_key("o/My.Repo", "issue", 7), "o.My-Repo.issue.7")

    def test_lease_key_rejects_bad_input(self):
        for args in (("nope", "pr", "1"), ("o/r", "branch", "1"), ("o/r", "pr", "")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                lanes.lease_key(*args)

    def test_tokens_cannot_inject_wildcards(self):
        self.assertEqual(lanes.subject("gemini", "auto-x.>*"), "lanes.gemini.auto-x")
        for bad in ("", "Gemini", "a.b", ">", "*"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                lanes.subject(bad, "j")

    def test_dedupe_key(self):
        self.assertEqual(lanes.dedupe_key("gemrev-x-pr1", "a" * 50), "gemrev-x-pr1@" + "a" * 40)
        self.assertNotEqual(lanes.dedupe_key("j", "a"), lanes.dedupe_key("j", "b"))
        self.assertEqual(lanes.dedupe_key("j"), "j@nohead")


@unittest.skipUnless(find_nats_binary(), "nats-server binary not installed")
class TestLanesOnJetStream(unittest.TestCase):
    def setUp(self):
        self.srv = EphemeralNats()
        self.srv.start()

    def tearDown(self):
        self.srv.stop()

    def run_async(self, coro):
        return asyncio.run(coro)

    async def _js(self):
        import nats

        nc = await nats.connect(self.srv.url)
        return nc, nc.jetstream()

    def test_lease_is_exclusive_and_fenced(self):
        async def go():
            nc, js = await self._js()
            try:
                kv = await lanes.lease_bucket(js, ttl_s=30)
                key = lanes.lease_key("o/r", "pr", "1")
                a = await lanes.acquire(kv, key, "mini-lane-a", {"job": "auto-r-pr1"})
                with self.assertRaises(lanes.LeaseHeld) as held:
                    await lanes.acquire(kv, key, "nuc-lane-b")
                self.assertEqual(held.exception.holder["owner"], "mini-lane-a")
                a2 = await lanes.renew(kv, a)
                self.assertGreater(a2.fence, a.fence)
                with self.assertRaises(lanes.LeaseLost):
                    await lanes.renew(kv, a)  # stale revision cannot renew
                self.assertFalse(await lanes.release(kv, a))  # stale revision cannot release
                self.assertEqual((await lanes.holder(kv, key))["owner"], "mini-lane-a")
                self.assertTrue(await lanes.release(kv, a2))
                self.assertIsNone(await lanes.holder(kv, key))
                b = await lanes.acquire(kv, key, "nuc-lane-b")
                self.assertGreater(b.fence, a2.fence)
            finally:
                await nc.close()

        self.run_async(go())

    def test_lease_expires_without_heartbeat(self):
        async def go():
            nc, js = await self._js()
            try:
                kv = await lanes.lease_bucket(js, ttl_s=1)
                key = lanes.lease_key("o/r", "issue", "9")
                a = await lanes.acquire(kv, key, "dead-lane")
                await asyncio.sleep(2.5)
                b = await lanes.acquire(kv, key, "next-lane")
                self.assertEqual(b.owner, "next-lane")
                with self.assertRaises(lanes.LeaseLost):
                    await lanes.renew(kv, a)
            finally:
                await nc.close()

        self.run_async(go())

    def test_parallel_acquire_has_one_winner(self):
        async def go():
            nc, js = await self._js()
            try:
                kv = await lanes.lease_bucket(js, ttl_s=30)
                key = lanes.lease_key("o/r", "pr", "77")

                async def try_take(i):
                    try:
                        await lanes.acquire(kv, key, f"lane-{i}")
                        return 1
                    except lanes.LeaseHeld:
                        return 0

                wins = await asyncio.gather(*(try_take(i) for i in range(20)))
                self.assertEqual(sum(wins), 1)
            finally:
                await nc.close()

        self.run_async(go())

    def test_queue_dedupes_and_caps_in_flight(self):
        async def go():
            nc, js = await self._js()
            try:
                await lanes.ensure_lanes(js, {"gemini": 2, "codex": 1}, ack_wait_s=30)
                self.assertTrue(await lanes.enqueue(js, "gemini", "gemrev-r-pr1", {"repo": "o/r"}, "a" * 40))
                self.assertFalse(await lanes.enqueue(js, "gemini", "gemrev-r-pr1", {"repo": "o/r"}, "a" * 40))
                self.assertTrue(await lanes.enqueue(js, "gemini", "gemrev-r-pr1", {"repo": "o/r"}, "b" * 40))
                self.assertTrue(await lanes.enqueue(js, "gemini", "auto-r-pr2", {"repo": "o/r"}))
                self.assertTrue(await lanes.enqueue(js, "codex", "auto-r-pr3", {"repo": "o/r"}))
                sub = await lanes.subscriber(js, "gemini")
                got = await lanes.pull(sub, batch=3, timeout=1)
                self.assertEqual(len(got), 2, "max_ack_pending=2 must cap gemini in flight")
                self.assertEqual(await lanes.pull(sub, batch=1, timeout=0.5), [])
                await got[0].ack()
                more = await lanes.pull(sub, batch=3, timeout=1)
                self.assertEqual(len(more), 1)
                jobs = sorted(json.loads(m.data)["job"] for m in got + more)
                self.assertEqual(jobs, ["auto-r-pr2", "gemrev-r-pr1", "gemrev-r-pr1"])
                csub = await lanes.subscriber(js, "codex")
                c = await lanes.pull(csub, batch=5, timeout=1)
                self.assertEqual([json.loads(m.data)["provider"] for m in c], ["codex"])
            finally:
                await nc.close()

        self.run_async(go())

    def test_ensure_lanes_is_idempotent_and_retargets(self):
        async def go():
            nc, js = await self._js()
            try:
                await lanes.ensure_lanes(js, {"cursor": 4})
                await lanes.ensure_lanes(js, {"cursor": 5})
                info = await js.consumer_info(lanes.LANES_STREAM, "lane_cursor")
                self.assertEqual(info.config.max_ack_pending, 5)
                with self.assertRaises(ValueError):
                    await lanes.ensure_lanes(js, {"cursor": 0})
            finally:
                await nc.close()

        self.run_async(go())

    def test_unacked_job_is_redelivered_then_dropped(self):
        async def go():
            nc, js = await self._js()
            try:
                await lanes.ensure_lanes(js, {"minimax": 1}, ack_wait_s=1, max_deliver=2)
                await lanes.enqueue(js, "minimax", "crit-1", {})
                sub = await lanes.subscriber(js, "minimax")
                first = await lanes.pull(sub, timeout=1)
                self.assertEqual(len(first), 1)
                await asyncio.sleep(1.6)
                second = await lanes.pull(sub, timeout=2)
                self.assertEqual(len(second), 1)
                self.assertEqual(second[0].metadata.num_delivered, 2)
                await asyncio.sleep(1.6)
                self.assertEqual(await lanes.pull(sub, timeout=1), [], "max_deliver=2 stops retries")
            finally:
                await nc.close()

        self.run_async(go())


if __name__ == "__main__":
    unittest.main()
