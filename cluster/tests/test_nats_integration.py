import asyncio
import json
import unittest

from cluster.spec import job_subject, validate_job_spec
from cluster.tests.nats_test_util import EphemeralNats, find_nats_binary
from cluster.worker_logic import JobAction, parse_job_payload


class FakeMsg:
    def __init__(self, data: bytes, delivery: int = 1):
        self.data = data
        self.metadata = type("M", (), {"num_delivered": delivery})()
        self.acks = []
        self.naks = []
        self.terms = []

    async def ack(self):
        self.acks.append(True)

    async def nak(self, delay=0):
        self.naks.append(delay)

    async def term(self):
        self.terms.append(True)

    async def in_progress(self):
        pass


class TestFakeJobSource(unittest.TestCase):
    def test_redelivery_path(self):
        raw = json.dumps(
            {
                "v": 1,
                "id": "x",
                "class": "test",
                "repo": "rupret007/bob-ops-dashboard",
                "ref": "main",
                "cmd": ["npm", "test"],
                "timeout_s": 1,
                "load_ceiling": 9,
            }
        ).encode()
        deliveries = []
        for n in range(1, 4):
            spec, d = parse_job_payload(raw, {"test"})
            self.assertEqual(d.action, JobAction.ACK)
            deliveries.append(n)
        self.assertEqual(len(deliveries), 3)


@unittest.skipUnless(find_nats_binary(), "nats-server binary not installed")
class TestNatsIntegration(unittest.TestCase):
    def test_setup_and_publish(self):
        nats_srv = EphemeralNats()
        nats_srv.start()
        try:
            asyncio.run(self._run(nats_srv.url))
        finally:
            nats_srv.stop()

    async def _run(self, url: str):
        import nats
        from nats.js.api import (
            AckPolicy,
            ConsumerConfig,
            RetentionPolicy,
            StorageType,
            StreamConfig,
        )

        nc = await nats.connect(url)
        js = nc.jetstream()
        await js.add_stream(
            StreamConfig(
                name="JOBS",
                subjects=["jobs.>"],
                retention=RetentionPolicy.WORK_QUEUE,
                storage=StorageType.MEMORY,
            )
        )
        await js.add_stream(
            StreamConfig(
                name="RESULTS",
                subjects=["results.>"],
                retention=RetentionPolicy.LIMITS,
                storage=StorageType.MEMORY,
            )
        )
        await js.add_consumer(
            "JOBS",
            ConsumerConfig(
                durable_name="workers",
                ack_policy=AckPolicy.EXPLICIT,
                ack_wait=120,
                max_deliver=3,
            ),
        )
        spec = validate_job_spec(
            {
                "v": 1,
                "id": "it-1",
                "class": "test",
                "repo": "rupret007/bob-ops-dashboard",
                "ref": "deadbeef",
                "cmd": ["npm", "test"],
                "timeout_s": 30,
                "load_ceiling": 8,
            }
        )
        await js.publish(job_subject(spec), json.dumps(spec).encode())
        psub = await js.pull_subscribe("", durable="workers", stream="JOBS")
        msgs = await psub.fetch(1, timeout=5)
        self.assertEqual(len(msgs), 1)
        await msgs[0].ack()
        await nc.close()


@unittest.skipUnless(find_nats_binary(), "nats-server binary not installed")
class TestWorkerPermissions(unittest.TestCase):
    def test_template_allows_work_and_denies_administration(self):
        server = EphemeralNats(secured=True)
        server.start()
        try:
            asyncio.run(self.check_permissions(server))
        finally:
            server.stop()

    async def check_permissions(self, server):
        import nats
        from cluster.setup_streams import setup

        await setup(server.url, server.passwords["nucbox"], "nucbox")
        admin = await nats.connect(server.url, user="nucbox", password=server.passwords["nucbox"])
        try:
            for node in ("mini", "macbook"):
                errors = asyncio.Queue()

                async def error_cb(error):
                    await errors.put(error)

                worker = await nats.connect(server.url, user=node, password=server.passwords[node], error_cb=error_cb)
                try:
                    js = worker.jetstream()
                    sub = await js.pull_subscribe("", durable="workers", stream="JOBS")
                    await admin.jetstream().publish(f"jobs.test.{node}", b"test-only-job")
                    messages = await sub.fetch(1, timeout=3)
                    await messages[0].in_progress()
                    await messages[0].nak()
                    messages = await sub.fetch(1, timeout=3)
                    await messages[0].ack_sync()
                    await admin.jetstream().publish(f"jobs.test.{node}-term", b"test-only-job")
                    messages = await sub.fetch(1, timeout=3)
                    await messages[0].term()
                    await js.publish(f"results.{node}", b"test-only-result")
                    await worker.publish(f"metrics.{node}", b"{}")
                    await worker.flush()
                    self.assertTrue(errors.empty())
                    for subject in ("$JS.API.STREAM.LIST", "$JS.API.STREAM.DELETE.JOBS",
                                    "$JS.API.STREAM.PURGE.JOBS", "$JS.API.CONSUMER.DELETE.JOBS.workers",
                                    "$JS.API.CONSUMER.CREATE.JOBS.untrusted",
                                    "$JS.API.CONSUMER.MSG.NEXT.OTHER.workers",
                                    "jobs.test.injected", "metrics.nucbox"):
                        await worker.publish(subject, b"{}")
                        await worker.flush()
                        error = await asyncio.wait_for(errors.get(), timeout=3)
                        self.assertIn("permissions violation", str(error).lower())
                    self.assertEqual((await admin.jetstream().stream_info("JOBS")).config.name, "JOBS")
                    await admin.jetstream().consumer_info("JOBS", "workers")
                finally:
                    await worker.close()
        finally:
            await admin.close()


if __name__ == "__main__":
    unittest.main()
