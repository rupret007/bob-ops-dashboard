import asyncio
import json
import secrets
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from cluster import lanes_perms
from cluster.live_metrics import Publisher, aggregate, validate
from cluster.metrics_server import Bridge, private_bind
from cluster.tests.nats_test_util import EphemeralNats, find_nats_binary, require_or_skip


class BridgeReceiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.nc = SimpleNamespace(subscribe=AsyncMock(), flush=AsyncMock())
        self.bridge = await Bridge(port=0).start(self.nc)

    async def asyncTearDown(self):
        await self.bridge.close()

    async def test_receive_requires_matching_metrics_subject(self):
        data = aggregate('mini', {}, {}, {}, now=1)
        payload = json.dumps(data).encode()
        await self.bridge.receive(SimpleNamespace(subject='metrics.nucbox', data=payload))
        self.assertEqual({}, self.bridge.latest)
        await self.bridge.receive(SimpleNamespace(subject='metrics.mini', data=payload))
        self.assertEqual('mini', self.bridge.latest['mini']['host'])

    async def test_wrong_host_returns_403_within_timeout(self):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.bridge.port)
        writer.write(b'GET /events HTTP/1.1\r\nHost: attacker.invalid\r\n\r\n')
        await writer.drain()
        response = await asyncio.wait_for(reader.read(256), timeout=2)
        self.assertIn(b'403', response)
        writer.close()
        await writer.wait_closed()


class SchemaTests(unittest.TestCase):
    def test_no_secrets_and_unknowns(self):
        raw = {'password': 'private', 'load1': 2, 'path': '/home/private', 'ip': '.'.join(('100', '64', '1', '2'))}
        data = aggregate('mini', raw, {'codex': 1, 'grok_bot': 9}, raw, now=1)
        self.assertEqual(validate(data), data)
        self.assertEqual(data['load1'], 2)
        self.assertIsNone(data['tokens_today'])
        self.assertEqual(data['grok_bot']['status'], 'unknown')
        self.assertNotIn('private', json.dumps(data))
        self.assertNotIn('100.64', json.dumps(data))
        for key, value in [('extra', 'secret'), ('load1', 'secret'), ('load1', float('nan'))]:
            with self.assertRaises(ValueError):
                validate(dict(data, **{key: value}))
        with self.assertRaises(ValueError):
            aggregate('.'.join(('100', '64', '1', '2')), {}, {}, {})

    def test_public_bind_refused(self):
        for host in ('0.0.0.0', '::', '8.8.8.8', '192.168.1.2', 'localhost', 'example.com', '::ffff:8.8.8.8'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                Bridge(host)
        for host in ('127.0.0.1', '::1', '.'.join(('100', '64', '1', '2')), 'fd7a:115c:a1e0::1'):
            self.assertEqual(private_bind(host), host)


class PublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_finish_even_on_failure_and_periodic(self):
        nc = SimpleNamespace(publish=AsyncMock(), flush=AsyncMock())
        pub = Publisher(nc, 'nucbox', machine=lambda: {}, usage=lambda: {'queue_depth': 3})
        with self.assertRaises(RuntimeError):
            async with pub.lane('gemini'):
                raise RuntimeError('fixture')
        calls = nc.publish.call_args_list
        self.assertEqual([json.loads(c.args[1])['lanes']['gemini'] for c in calls], [1, 0])
        self.assertEqual(calls[0].args[0], 'metrics.nucbox')
        self.assertEqual(json.loads(calls[0].args[1])['queue_depth'], 3)
        with self.assertRaises(ValueError):
            async with pub.lane('grok_bot'):
                pass
        task = asyncio.create_task(pub.periodic(1))
        await asyncio.sleep(1.1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertGreaterEqual(nc.publish.call_count, 4)


@require_or_skip(find_nats_binary(), 'nats-server required')
class RealStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import nats
        self.passwords = {u: secrets.token_urlsafe(20) for u in ('metrics', 'viewer')}
        self.srv = EphemeralNats(extra_conf=lanes_perms.render_users({
            'metrics': (self.passwords['metrics'], lanes_perms.metrics_publisher()),
            'viewer': (self.passwords['viewer'], lanes_perms.metrics_viewer())}))
        self.srv.start()
        self.errors = {'metrics': [], 'viewer': []}
        self.clients = {}
        for user in self.errors:
            async def error(e, user=user):
                self.errors[user].append(str(e).lower())
            self.clients[user] = await nats.connect(self.srv.url, user=user, password=self.passwords[user],
                                                     error_cb=error, allow_reconnect=False)
        self.bridge = await Bridge(port=0).start(self.clients['viewer'])

    async def asyncTearDown(self):
        await self.bridge.close()
        for nc in self.clients.values():
            await nc.close()
        self.srv.stop()

    async def test_permissions_are_enforced_and_push_reaches_browser(self):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.bridge.port)
        writer.write(f'GET /events HTTP/1.1\r\nHost: 127.0.0.1:{self.bridge.port}\r\n\r\n'.encode())
        await writer.drain()
        header = await reader.readuntil(b'\r\n\r\n')
        self.assertIn(b'text/event-stream', header)
        self.assertEqual(await reader.readuntil(b'\n\n'), b'retry: 2000\n\n')
        pub = Publisher(self.clients['metrics'], 'mini', machine=lambda: {})
        await pub.emit()
        frame = await asyncio.wait_for(reader.readuntil(b'\n\n'), 2)
        self.assertIn(b'event: metrics', frame)
        self.assertIn(b'"host": "mini"', frame)
        for user, subjects in {
            'metrics': ['$KV.LANE_LEASES.key', 'lanes.codex.mini.job', 'lanes_dlq.job', '_INBOX.x', '$JS.API.INFO', 'metrics.mini.extra', 'other'],
            'viewer': ['metrics.mini', '$KV.LANE_LEASES.key', 'lanes.codex.mini.job', 'lanes_dlq.job', '_INBOX.x', '$JS.API.INFO', 'other']
        }.items():
            for subject in subjects:
                self.errors[user].clear()
                await self.clients[user].publish(subject, b'{}')
                await self.clients[user].flush()
                for _ in range(30):
                    if self.errors[user]:
                        break
                    await asyncio.sleep(.01)
                self.assertTrue(any('permissions violation' in e and subject.lower() in e for e in self.errors[user]), (user, subject))
        for user, subject in [('metrics', 'metrics.*'), ('viewer', 'lanes.>'), ('viewer', '_INBOX.>')]:
            self.errors[user].clear()
            await self.clients[user].subscribe(subject)
            await self.clients[user].flush()
            await asyncio.sleep(.05)
            self.assertTrue(any('permissions violation' in e for e in self.errors[user]))
        writer.close()
        await writer.wait_closed()

    async def test_cache_validation_bounded_queues_and_origin(self):
        queue = asyncio.Queue(maxsize=1)
        self.bridge.clients.add(queue)
        for stamp in range(10):
            data = aggregate('mini', {}, {}, {}, now=stamp)
            await self.bridge.receive(SimpleNamespace(subject='metrics.mini', data=json.dumps(data).encode()))
        self.assertEqual(queue.qsize(), 1)
        self.assertEqual((await queue.get())['ts'], 9)
        await self.bridge.receive(SimpleNamespace(subject='metrics.mini', data=b'{"secret":"do not emit"}'))
        self.assertEqual(self.bridge.latest['mini']['ts'], 9)
        self.bridge.clients.discard(queue)
        for host, origin, status in [('attacker', '', b'403'), (f'127.0.0.1:{self.bridge.port}', 'https://attacker', b'403')]:
            reader, writer = await asyncio.open_connection('127.0.0.1', self.bridge.port)
            writer.write(f'GET /events HTTP/1.1\r\nHost: {host}\r\nOrigin: {origin}\r\n\r\n'.encode())
            await writer.drain()
            self.assertIn(status, await reader.read())
            writer.close()
            await writer.wait_closed()
        # New/reconnecting client gets current snapshot without request/reply or a new publish.
        reader, writer = await asyncio.open_connection('127.0.0.1', self.bridge.port)
        writer.write(f'GET /events HTTP/1.1\r\nHost: 127.0.0.1:{self.bridge.port}\r\n\r\n'.encode())
        await writer.drain()
        await reader.readuntil(b'\r\n\r\n')
        await reader.readuntil(b'\n\n')
        frame = await asyncio.wait_for(reader.readuntil(b'\n\n'), 2)
        self.assertIn(b'"ts": 9', frame)
        writer.close()
        await writer.wait_closed()


class FailureAndSourcesTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnected_metrics_does_not_consume_or_block_lane(self):
        from nats.errors import ConnectionClosedError
        nc = SimpleNamespace(publish=AsyncMock(side_effect=ConnectionClosedError()), flush=AsyncMock())
        pub = Publisher(nc, 'mini', machine=lambda: {}, providers=('codex',))
        async with pub.lane('codex'):
            self.assertEqual(pub.lanes['codex'], 1)
        self.assertEqual(pub.lanes['codex'], 0)
        self.assertIsNone(pub.lanes['gemini'])

    async def test_real_weekly_source_or_null(self):
        from datetime import datetime, timezone
        from pathlib import Path
        import tempfile
        from cluster.metrics_sources import weekly_limit
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day = root / now.strftime('%Y/%m/%d')
            day.mkdir(parents=True)
            self.assertIsNone(weekly_limit(root))
            for minutes, reset, percent, expected in ((10080, now.timestamp()+3600, 42, 42),
                                                      (300, now.timestamp()+3600, 42, None),
                                                      (10080, now.timestamp()-1, 42, None),
                                                      (10080, now.timestamp()+3600, 101, None)):
                (day/'session.jsonl').write_text(json.dumps({'type':'event_msg', 'timestamp':now.isoformat(),
                    'payload':{'type':'token_count', 'rate_limits':{'secondary':{
                        'window_minutes':minutes,'used_percent':percent,'resets_at':reset}}}}))
                self.assertEqual(weekly_limit(root), expected)


class QueueDepthTests(unittest.TestCase):
    def test_real_delivery_metadata_and_unknown_empty_fetch(self):
        from cluster.live_metrics import delivery_queue_depth
        for depth in (0, 3):
            msg = SimpleNamespace(metadata=SimpleNamespace(num_pending=depth))
            self.assertEqual(delivery_queue_depth([msg]), depth)
        self.assertIsNone(delivery_queue_depth([]))
        for depth in (None, -1, True, 'secret'):
            self.assertIsNone(delivery_queue_depth([SimpleNamespace(metadata=SimpleNamespace(num_pending=depth))]))
