import asyncio
import json
import unittest

from cluster import lanes
from cluster.tests.nats_test_util import EphemeralNats, find_nats_binary


class TestLaneNames(unittest.TestCase):
    def test_lease_key(self):
        self.assertEqual(lanes.lease_key("rupret007/webjam", "pr", "390"), "rupret007.webjam.pr.390")
        dotted = lanes.lease_key("o/My.Repo", "issue", 7)
        self.assertTrue(dotted.startswith("o.My-Repo-") and dotted.endswith(".issue.7"), dotted)
        self.assertEqual(dotted, lanes.lease_key("o/My.Repo", "issue", 7), "must be stable")

    def test_lease_key_rejects_bad_input(self):
        for args in (("nope", "pr", "1"), ("o/r", "branch", "1"), ("o/r", "pr", "")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                lanes.lease_key(*args)

    def test_tokens_cannot_inject_wildcards(self):
        s = lanes.subject("gemini", "nuc", "auto-x.>*")
        self.assertTrue(s.startswith("lanes.gemini.nuc.auto-x-"), s)
        self.assertEqual(s.count("."), 3)
        self.assertNotIn(">", s)
        self.assertNotIn("*", s)
        for bad in ("", "Gemini", "a.b", ">", "*"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                lanes.subject(bad, "nuc", "j")
        for bad in ("", "Nuc", "a.b", ">", "*", "a b", "x" * 25):
            with self.subTest(host=bad), self.assertRaises(ValueError):
                lanes.subject("gemini", bad, "j")

    def test_token_lossy_inputs_do_not_collide(self):  # Karen L6
        self.assertNotEqual(lanes.token("My.Repo"), lanes.token("My-Repo"))
        self.assertEqual(lanes.token("My-Repo"), "My-Repo")
        a, b = lanes.token("x" * 70 + "a"), lanes.token("x" * 70 + "b")
        self.assertNotEqual(a, b)
        self.assertLessEqual(len(a), 60)
        self.assertNotEqual(lanes.lease_key("o/My.Repo", "pr", 1), lanes.lease_key("o/My-Repo", "pr", 1))

    def test_dedupe_key(self):
        self.assertEqual(lanes.dedupe_key("gemini", "gemrev-x-pr1", "a" * 50), "gemini:gemrev-x-pr1@" + "a" * 40)
        self.assertNotEqual(lanes.dedupe_key("gemini", "j", "a"), lanes.dedupe_key("gemini", "j", "b"))

    def test_dedupe_key_includes_provider_and_attempt(self):
        self.assertNotEqual(lanes.dedupe_key("gemini", "j", "a"), lanes.dedupe_key("codex", "j", "a"))
        self.assertNotEqual(lanes.dedupe_key("gemini", "j", "a"), lanes.dedupe_key("gemini", "j", "a", attempt=1))
        self.assertEqual(lanes.dedupe_key("gemini", "j", "a", attempt=2), "gemini:j@a#a2")
        with self.assertRaises(ValueError):
            lanes.dedupe_key("gemini", "j", "a", attempt=-1)

    def test_headless_dedupe_is_per_spec(self):  # Karen L5: no shared '@nohead'
        k1 = lanes.dedupe_key("gemini", "j", "", spec={"repo": "o/a"})
        self.assertEqual(k1, lanes.dedupe_key("gemini", "j", "", spec={"repo": "o/a"}))
        self.assertNotEqual(k1, lanes.dedupe_key("gemini", "j", "", spec={"repo": "o/b"}))
        self.assertNotIn("@nohead@", k1)

    def test_provider_allowlist(self):
        for p in lanes.PROVIDERS:
            self.assertEqual(lanes.check_provider(p), p)
        for bad in ("zzznoprovider", "grok", "openai", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                lanes.check_provider(bad)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                lanes.dedupe_key(bad, "j", "a")
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                lanes.consumer_name(bad, "nuc")


class JSCase(unittest.TestCase):
    extra_conf = ""

    def setUp(self):
        self.srv = EphemeralNats(extra_conf=self.extra_conf)
        self.srv.start()

    def tearDown(self):
        self.srv.stop()

    def run_async(self, coro):
        return asyncio.run(coro)

    async def _js(self, **kw):
        import nats

        nc = await nats.connect(self.srv.url, **kw)
        return nc, nc.jetstream()


@unittest.skipUnless(find_nats_binary(), "nats-server binary not installed")
class TestLanesOnJetStream(JSCase):
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

    def test_lease_bucket_applies_new_ttl(self):  # Karen L6
        async def go():
            nc, js = await self._js()
            try:
                await lanes.lease_bucket(js, ttl_s=30)
                await lanes.lease_bucket(js, ttl_s=45)
                info = await js.stream_info(lanes.LEASE_STREAM)
                self.assertAlmostEqual(info.config.max_age, 45.0, places=3)
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
                await lanes.ensure_lanes(js, {"gemini": {"nuc": 2}, "codex": {"nuc": 1}}, ack_wait_s=30)
                self.assertTrue(await lanes.enqueue(js, "gemini", "gemrev-r-pr1", {"repo": "o/r"}, "a" * 40, host="nuc"))
                self.assertFalse(await lanes.enqueue(js, "gemini", "gemrev-r-pr1", {"repo": "o/r"}, "a" * 40, host="nuc"))
                self.assertTrue(await lanes.enqueue(js, "gemini", "gemrev-r-pr1", {"repo": "o/r"}, "b" * 40, host="nuc"))
                self.assertTrue(await lanes.enqueue(js, "gemini", "auto-r-pr2", {"repo": "o/r"}, host="nuc"))
                self.assertTrue(await lanes.enqueue(js, "codex", "auto-r-pr3", {"repo": "o/r"}, host="nuc"))
                sub = await lanes.subscriber(js, "gemini", "nuc")
                got = await lanes.pull(sub, batch=3, timeout=1)
                self.assertEqual(len(got), 2, "max_ack_pending=2 must cap gemini in flight")
                self.assertEqual(await lanes.pull(sub, batch=1, timeout=0.5), [])
                await got[0].ack()
                more = await lanes.pull(sub, batch=3, timeout=1)
                self.assertEqual(len(more), 1)
                jobs = sorted(json.loads(m.data)["job"] for m in got + more)
                self.assertEqual(jobs, ["auto-r-pr2", "gemrev-r-pr1", "gemrev-r-pr1"])
                csub = await lanes.subscriber(js, "codex", "nuc")
                c = await lanes.pull(csub, batch=5, timeout=1)
                self.assertEqual([json.loads(m.data)["provider"] for m in c], ["codex"])
            finally:
                await nc.close()

        self.run_async(go())

    def test_same_job_for_two_providers_reaches_both(self):  # Karen: cross-provider dedupe
        async def go():
            nc, js = await self._js()
            try:
                await lanes.ensure_lanes(js, {"gemini": {"nuc": 2}, "codex": {"nuc": 2}}, ack_wait_s=30)
                self.assertTrue(await lanes.enqueue(js, "gemini", "sharedjob", {}, "deadbeef", host="nuc"))
                self.assertTrue(await lanes.enqueue(js, "codex", "sharedjob", {}, "deadbeef", host="nuc"))
                g = await lanes.pull(await lanes.subscriber(js, "gemini", "nuc"), batch=5, timeout=1)
                c = await lanes.pull(await lanes.subscriber(js, "codex", "nuc"), batch=5, timeout=1)
                self.assertEqual([json.loads(m.data)["job"] for m in g], ["sharedjob"])
                self.assertEqual([json.loads(m.data)["job"] for m in c], ["sharedjob"])
            finally:
                await nc.close()

        self.run_async(go())

    def test_unknown_provider_is_rejected_before_publish(self):  # Karen: PROVIDERS allowlist
        async def go():
            nc, js = await self._js()
            try:
                await lanes.ensure_lanes(js, {"gemini": {"nuc": 1}})
                before = (await js.stream_info(lanes.LANES_STREAM)).state.messages
                with self.assertRaises(ValueError):
                    await lanes.enqueue(js, "zzznoprovider", "job", {}, "a", host="nuc")
                with self.assertRaises(ValueError):
                    await lanes.ensure_lanes(js, {"zzznoprovider": {"nuc": 1}})
                with self.assertRaises(ValueError):
                    await lanes.subscriber(js, "zzznoprovider", "nuc")
                self.assertEqual((await js.stream_info(lanes.LANES_STREAM)).state.messages, before)
            finally:
                await nc.close()

        self.run_async(go())

    def test_retry_attempt_and_headless_jobs_are_not_swallowed(self):  # Karen L5
        async def go():
            nc, js = await self._js()
            try:
                await lanes.ensure_lanes(js, {"cursor": {"nuc": 5}})
                self.assertTrue(await lanes.enqueue(js, "cursor", "fix-r-pr1", {}, "c" * 40, host="nuc"))
                self.assertFalse(await lanes.enqueue(js, "cursor", "fix-r-pr1", {}, "c" * 40, host="nuc"))
                self.assertTrue(await lanes.enqueue(js, "cursor", "fix-r-pr1", {}, "c" * 40, host="nuc", attempt=1))
                self.assertFalse(await lanes.enqueue(js, "cursor", "fix-r-pr1", {}, "c" * 40, host="nuc", attempt=1))
                self.assertTrue(await lanes.enqueue(js, "cursor", "sweep", {"repo": "o/a"}, host="nuc"))
                self.assertTrue(await lanes.enqueue(js, "cursor", "sweep", {"repo": "o/b"}, host="nuc"))
                self.assertFalse(await lanes.enqueue(js, "cursor", "sweep", {"repo": "o/b"}, host="nuc"))
            finally:
                await nc.close()

        self.run_async(go())

    def test_ensure_lanes_is_idempotent_and_retargets(self):
        async def go():
            nc, js = await self._js()
            try:
                await lanes.ensure_lanes(js, {"cursor": {"nuc": 4}})
                await lanes.ensure_lanes(js, {"cursor": {"nuc": 5}})
                info = await js.consumer_info(lanes.LANES_STREAM, "lane_cursor_nuc")
                self.assertEqual(info.config.max_ack_pending, 5)
                with self.assertRaises(ValueError):
                    await lanes.ensure_lanes(js, {"cursor": {"nuc": 0}})
                with self.assertRaises(ValueError):
                    await lanes.ensure_lanes(js, {"cursor": 4})
            finally:
                await nc.close()

        self.run_async(go())

    def test_retarget_keeps_in_flight_ack_state(self):  # Karen L2 (no delete on editable change)
        async def go():
            nc, js = await self._js()
            try:
                await lanes.ensure_lanes(js, {"cursor": {"nuc": 2}}, ack_wait_s=30)
                for i in range(2):
                    await lanes.enqueue(js, "cursor", f"j{i}", {}, "a", host="nuc")
                got = await lanes.pull(await lanes.subscriber(js, "cursor", "nuc"), batch=2, timeout=1)
                self.assertEqual(len(got), 2)
                await lanes.ensure_lanes(js, {"cursor": {"nuc": 5}}, ack_wait_s=30)
                info = await js.consumer_info(lanes.LANES_STREAM, "lane_cursor_nuc")
                self.assertEqual(info.num_ack_pending, 2)
                self.assertEqual(info.config.max_ack_pending, 5)
            finally:
                await nc.close()

        self.run_async(go())

    def test_divergent_consumer_config_fails_clearly_or_recreates_only_when_idle(self):  # Karen L2
        async def go():
            from nats.js.api import AckPolicy, ConsumerConfig

            nc, js = await self._js()
            try:
                await lanes.ensure_lanes(js, {"claude": {"nuc": 1}})
                await js.delete_consumer(lanes.LANES_STREAM, "lane_claude_nuc")
                await js.add_consumer(lanes.LANES_STREAM, ConsumerConfig(
                    durable_name="lane_claude_nuc", name="lane_claude_nuc", ack_policy=AckPolicy.EXPLICIT,
                    max_waiting=7, filter_subject="lanes.claude.nuc.*", max_ack_pending=1))
                await lanes.enqueue(js, "claude", "busy", {}, "a", host="nuc")
                held = await lanes.pull(await lanes.subscriber(js, "claude", "nuc"), timeout=1)
                self.assertEqual(len(held), 1)
                with self.assertRaises(lanes.LaneConfigConflict):
                    await lanes.ensure_lanes(js, {"claude": {"nuc": 1}})
                with self.assertRaises(lanes.LaneConfigConflict):  # in flight: never recreated
                    await lanes.ensure_lanes(js, {"claude": {"nuc": 1}}, recreate_idle=True)
                info = await js.consumer_info(lanes.LANES_STREAM, "lane_claude_nuc")
                self.assertEqual(info.config.max_waiting, 7)
                self.assertEqual(info.num_ack_pending, 1)
                await held[0].ack()
                await asyncio.sleep(0.2)
                await lanes.ensure_lanes(js, {"claude": {"nuc": 1}}, recreate_idle=True)
                info = await js.consumer_info(lanes.LANES_STREAM, "lane_claude_nuc")
                self.assertNotEqual(info.config.max_waiting, 7)
            finally:
                await nc.close()

        self.run_async(go())

    def test_ensure_lanes_keeps_manual_stream_edits(self):  # Karen L6
        async def go():
            nc, js = await self._js()
            try:
                await lanes.ensure_lanes(js, {"cursor": {"nuc": 1}})
                info = await js.stream_info(lanes.LANES_STREAM)
                info.config.max_msgs = 777
                info.config.description = "manual"
                await js.update_stream(info.config)
                await lanes.ensure_lanes(js, {"cursor": {"nuc": 1}})
                cfg = (await js.stream_info(lanes.LANES_STREAM)).config
                self.assertEqual(cfg.max_msgs, 777)
                self.assertEqual(cfg.description, "manual")
                await lanes.ensure_lanes(js, {"cursor": {"nuc": 1}}, dup_window_s=600)
                cfg = (await js.stream_info(lanes.LANES_STREAM)).config
                self.assertAlmostEqual(cfg.duplicate_window, 600.0, places=3)
                self.assertEqual(cfg.max_msgs, 777)
            finally:
                await nc.close()

        self.run_async(go())

    def test_unacked_job_is_redelivered_then_dropped(self):
        async def go():
            nc, js = await self._js()
            try:
                await lanes.ensure_lanes(js, {"minimax": {"nuc": 1}}, ack_wait_s=1, max_deliver=2)
                await lanes.enqueue(js, "minimax", "crit-1", {}, host="nuc")
                sub = await lanes.subscriber(js, "minimax", "nuc")
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

    def test_lease_held_job_is_nakked_with_delay_not_lost(self):  # Karen L3
        async def go():
            nc, js = await self._js()
            try:
                kv = await lanes.lease_bucket(js, ttl_s=30)
                svc = await lanes.LeaseService(nc, kv).start()
                leases = lanes.LeaseClient(nc, "nuc")
                await lanes.ensure_lanes(js, {"codex": {"nuc": 2}}, ack_wait_s=30, max_deliver=5)
                key = lanes.lease_key("o/r", "pr", "5")
                await lanes.enqueue(js, "codex", "auto-r-pr5", {}, "a", host="nuc")
                await lanes.enqueue(js, "codex", "gemfix-r-pr5", {}, "a", host="nuc")
                sub = await lanes.subscriber(js, "codex", "nuc")
                a, b = await lanes.pull(sub, batch=2, timeout=1)
                lease = await lanes.take(a, leases, key, "nuc/lane-1")
                self.assertIsNotNone(lease)
                self.assertIsNone(await lanes.take(b, leases, key, "nuc/lane-2", nak_delay_s=1.5))
                await nc.flush()
                info = await js.consumer_info(lanes.LANES_STREAM, "lane_codex_nuc")
                # nats-server 2.15.1 keeps a delay-nak'd job in its ack-pending slot until redelivery (see LANES.md)
                self.assertEqual(info.num_ack_pending, 2)
                self.assertEqual(info.num_redelivered, 0)
                self.assertEqual(await lanes.pull(sub, timeout=0.5), [], "delayed, not redelivered at once")
                await asyncio.sleep(1.6)
                again = await lanes.pull(sub, timeout=2)
                self.assertEqual([json.loads(m.data)["job"] for m in again], ["gemfix-r-pr5"])
                self.assertEqual(again[0].metadata.num_delivered, 2)
                self.assertTrue(await leases.release(lease))
                self.assertIsNotNone(await lanes.take(again[0], leases, key, "nuc/lane-2"))
                await svc.stop()
            finally:
                await nc.close()

        self.run_async(go())

    def test_max_deliveries_go_to_dead_letter_queue(self):  # Karen L4
        async def go():
            nc, js = await self._js()
            try:
                seen = []

                async def on_dead(rec):
                    seen.append(rec)

                watcher = await lanes.DeadLetterWatcher(nc, js, on_dead=on_dead).start()
                await lanes.ensure_lanes(js, {"gemini": {"nuc": 1}}, ack_wait_s=1, max_deliver=2)
                self.assertTrue(await lanes.enqueue(js, "gemini", "poison", {"repo": "o/r"}, "a", host="nuc"))
                sub = await lanes.subscriber(js, "gemini", "nuc")
                for _ in range(2):
                    self.assertEqual(len(await lanes.pull(sub, timeout=2)), 1)
                    await asyncio.sleep(1.4)
                for _ in range(40):
                    if seen:
                        break
                    await lanes.pull(sub, timeout=0.2)
                    await asyncio.sleep(0.1)
                self.assertEqual(len(seen), 1, "MAX_DELIVERIES advisory must be handled")
                self.assertEqual(seen[0]["job"]["job"], "poison")
                self.assertEqual(seen[0]["consumer"], "lane_gemini_nuc")
                self.assertEqual(seen[0]["deliveries"], 2)
                self.assertEqual((await js.stream_info(lanes.LANES_STREAM)).state.messages, 0,
                                 "dead job must leave the work queue")
                dl = await lanes.dead_letters(js)
                self.assertEqual([d["job"]["job"] for d in dl], ["poison"])
                self.assertTrue(dl[0]["subject"].startswith("lanes.gemini.nuc."))
                self.assertIsNone(await watcher.move(seen[0]["seq"], "lane_gemini_nuc", 2), "idempotent")
                self.assertEqual(len(await lanes.dead_letters(js)), 1)
                await watcher.stop()
            finally:
                await nc.close()

        self.run_async(go())


if __name__ == "__main__":
    unittest.main()
