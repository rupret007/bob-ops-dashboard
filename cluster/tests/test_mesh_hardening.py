"""Failure-path regressions for the PR #68 gate; fixtures never use live services."""
import asyncio
import contextlib
import hashlib
import io
import json
import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cluster import bobctl, joind

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / 'cluster/install/join.sh'
REF = 'a' * 40
SHA = 'b' * 64
CODE = 'synthetic-join-code-never-a-real-credential'


class InstallerHardening(unittest.TestCase):
    def test_paste_lines_keep_code_out_of_history_and_argv(self):
        lines = bobctl.install_lines(CODE, REF, SHA)
        for line in lines.values():
            self.assertNotIn(CODE, line)
            self.assertIn(REF, line)
            self.assertIn(SHA, line)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Execute the actual generated shell line; capture what bash receives.
            (root / 'curl').write_text('#!/bin/sh\nexit 0\n')
            (root / 'bash').write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            for tool in ('curl', 'bash'):
                (root / tool).chmod(0o755)
            proc = subprocess.run(['/bin/sh'], input=lines['macos_linux'], text=True,
                                  capture_output=True, env={**os.environ, 'PATH': td + ':' + os.environ['PATH']})
            self.assertEqual(proc.returncode, 0)
            self.assertNotIn(CODE, proc.stdout + proc.stderr)
            self.assertNotIn('--code', proc.stdout.splitlines())
            self.assertIn('--prompt-code', proc.stdout.splitlines())

    def run_installer(self, args, env=None):
        return subprocess.run(['/bin/bash', str(INSTALLER), *args], text=True, capture_output=True,
                              env={**os.environ, 'BOB_JOIN_CODE': CODE, **(env or {})})

    def test_missing_branch_short_or_nonhex_ref_refused(self):
        for ref in (None, 'main', 'abc1234', 'g' * 40, 'a' * 41):
            with self.subTest(ref=ref):
                args = ['--dry-run', '--sha256', SHA]
                if ref is not None:
                    args += ['--ref', ref]
                proc = self.run_installer(args)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('40-hex', proc.stderr)

    def test_missing_short_or_nonhex_checksum_refused(self):
        for checksum in (None, 'abc123', 'z' * 64):
            with self.subTest(checksum=checksum):
                args = ['--dry-run', '--ref', REF]
                if checksum is not None:
                    args += ['--sha256', checksum]
                proc = self.run_installer(args)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('checksum', proc.stderr)

    def test_argv_code_rejected_without_echo(self):
        proc = self.run_installer(['--ref', REF, '--sha256', SHA, '--code', CODE])
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn(CODE, proc.stdout + proc.stderr)

    def test_bad_checksum_stops_before_extraction_positive_control_reaches_tar(self):
        payload = b'synthetic archive bytes'
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'curl').write_text("#!/bin/sh\nprintf 'synthetic archive bytes' > \"$3\"\n")
            (root / 'tar').write_text('#!/bin/sh\necho extraction-reached >&2\nexit 73\n')
            (root / 'tailscale').write_text('#!/bin/sh\nexit 0\n')
            for tool in ('curl', 'tar', 'tailscale'):
                (root / tool).chmod(0o755)
            env = {'PATH': td + ':' + os.environ['PATH'], 'HOME': td}
            for checksum, good in ((SHA, False), (hashlib.sha256(payload).hexdigest(), True)):
                proc = self.run_installer(['--ref', REF, '--sha256', checksum, '--no-service'], env)
                self.assertNotEqual(proc.returncode, 0)
                self.assertNotIn(CODE, proc.stdout + proc.stderr)
                if good:
                    self.assertEqual(proc.returncode, 73)
                    self.assertIn('extraction-reached', proc.stderr)
                else:
                    self.assertIn('archive checksum mismatch', proc.stderr)
                    self.assertNotIn('extraction-reached', proc.stderr)
                self.assertFalse((root / '.local/share/bob-cluster/app').exists())

    def test_mint_embeds_digest_of_downloaded_archive(self):
        archive = b'archive at the pinned SHA'
        hub = Mock()
        hub.reg.mint.return_value = (CODE, {'name': 'box', 'role': 'member', 'caps': [], 'expires': 1})
        out = io.StringIO()
        with patch('urllib.request.urlopen', return_value=io.BytesIO(archive)) as download, \
                patch.object(bobctl, 'hub_from_args', return_value=hub), contextlib.redirect_stdout(out):
            rc = bobctl.main(['mint', '--name', 'box', '--ref', REF, '--url', 'nats://127.0.0.1:4222'])
        self.assertEqual(rc, 0)
        self.assertTrue(download.call_args.args[0].endswith('/' + REF))
        self.assertIn('--sha256 ' + hashlib.sha256(archive).hexdigest(), out.getvalue())
        self.assertIn('-Sha256 ' + hashlib.sha256(archive).hexdigest(), out.getvalue())

    def test_mint_unpinned_ref_fails_before_registry_or_download(self):
        for ref in ('main', 'abc1234', 'g' * 40):
            with patch.object(bobctl, 'hub_from_args') as hub, patch('urllib.request.urlopen') as download, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(bobctl.main(['mint', '--name', 'box', '--ref', ref]), 0)
                hub.assert_not_called()
                download.assert_not_called()
        with patch('subprocess.run', return_value=SimpleNamespace(stdout='abc1234')):
            with self.assertRaises(ValueError):
                bobctl._git_ref()


class RevokeHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pidfile = self.root / 'nats.pid'
        self.pidfile.write_text('12345')
        self.conf = self.root / 'nats.conf'
        self.before = {'server_id': 'test-server', 'config_load_time': 'before'}

    def run_revoke(self, samples, kill_error=None):
        reg = Mock()
        reg.revoke.return_value = True
        with patch.object(joind, 'Registry', return_value=reg), patch.object(joind.Hub, 'render'), \
                patch.object(joind, 'monitor_json', side_effect=samples), \
                patch.object(joind.os, 'kill', side_effect=kill_error) as kill, \
                patch.object(joind.os, 'readlink', return_value='/usr/bin/nats-server'), \
                patch('subprocess.run', return_value=SimpleNamespace(stdout=f'nats-server -c {self.conf}')), \
                patch.object(joind.time, 'monotonic', side_effect=[0, 0, 6]), \
                patch.object(joind.time, 'sleep'), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            rc = bobctl.main(['revoke', 'box', '--pidfile', str(self.pidfile), '--conf-out', str(self.conf),
                             '--config-dir', str(self.root), '--http', '127.0.0.1:18232'])
        return rc, out.getvalue(), err.getvalue(), kill

    def test_revoke_is_live_without_flag_and_requires_changed_load_time(self):
        rc, out, err, kill = self.run_revoke([self.before, {**self.before, 'config_load_time': 'after'}])
        self.assertEqual(rc, 0, err)
        kill.assert_called_once_with(12345, signal.SIGHUP)
        self.assertIn('reload confirmed', out)

    def test_unconfirmed_revoke_nonzero_loud(self):
        for after in (self.before, {**self.before, 'config_load_time': ''},
                      {**self.before, 'config_load_time': 'after', 'server_id': 'other'}):
            with self.subTest(after=after):
                rc, out, err, kill = self.run_revoke([self.before, after])
                self.assertNotEqual(rc, 0)
                self.assertIn('NOT CONFIRMED', err)
                self.assertNotIn('reload confirmed', out)
                kill.assert_called_once_with(12345, signal.SIGHUP)

    def test_signal_and_monitor_failures_are_nonzero(self):
        for samples, error in (([self.before], OSError('synthetic failure')), ([OSError('offline')], None)):
            rc, out, err, _ = self.run_revoke(samples, error)
            self.assertNotEqual(rc, 0)
            self.assertIn('NOT CONFIRMED', err)
            self.assertNotIn('reload confirmed', out)

    def test_unidentified_server_never_signalled(self):
        # nats-server /varz has no PID; a monitor without server identity must not be signalled.
        for bad in ({**self.before, 'server_id': ''}, {k: v for k, v in self.before.items() if k != 'server_id'}):
            with self.subTest(bad=bad):
                rc, _, err, kill = self.run_revoke([bad])
                self.assertNotEqual(rc, 0)
                self.assertIn('NOT CONFIRMED', err)
                kill.assert_not_called()

    def test_wrong_config_never_signalled(self):
        with patch.object(joind, 'monitor_json', return_value=self.before), patch.object(joind.os, 'kill') as kill, \
                patch('subprocess.run', return_value=SimpleNamespace(stdout='nats-server -c /tmp/other.conf')):
            with self.assertRaises(RuntimeError):
                joind.signal_reload_from_pidfile(self.pidfile, conf_out=self.conf)()
            kill.assert_not_called()


class SourceLimitHardening(unittest.TestCase):
    def test_source_exhaustion_does_not_block_another_and_expires(self):
        now = [0.0]
        limiter = joind.SourceRateLimiter(clock=lambda: now[0])
        self.assertTrue(all(limiter.allow('192.0.2.1') for _ in range(30)))
        self.assertFalse(limiter.allow('192.0.2.1'))
        self.assertTrue(limiter.allow('192.0.2.2'))
        now[0] = 60
        self.assertTrue(limiter.allow('192.0.2.1'))
        self.assertNotIn('192.0.2.2', limiter.windows)

    def test_source_comes_from_authenticated_monitor_connection_with_pagination(self):
        pages = [{'total': 2, 'connections': [{'authorized_user': 'other', 'ip': '192.0.2.2'}]},
                 {'total': 2, 'connections': [{'authorized_user': 'user', 'ip': '::ffff:192.0.2.1'}]}]
        with patch.object(joind, 'monitor_json', side_effect=pages) as monitor:
            self.assertEqual(joind.source_address('127.0.0.1:18232', 'user'), '192.0.2.1')
            self.assertIn('offset=1', monitor.call_args.args[1])

    def test_missing_or_ambiguous_source_fails_closed(self):
        for connections in ([], [{'authorized_user': 'user', 'ip': ip} for ip in ('192.0.2.1', '192.0.2.2')]):
            with patch.object(joind, 'monitor_json', return_value={'total': len(connections), 'connections': connections}):
                with self.assertRaises(RuntimeError):
                    joind.source_address('127.0.0.1:18232', 'user')

    def test_serve_enforces_per_source_limit_before_redeeming(self):
        async def run():
            nc = SimpleNamespace(subscribe=AsyncMock(), flush=AsyncMock(), drain=AsyncMock())
            stop = asyncio.Event()
            hub = SimpleNamespace(http='127.0.0.1:18232')
            with patch('nats.connect', AsyncMock(return_value=nc)), \
                    patch.object(joind, 'source_address', side_effect=lambda http, user: '192.0.2.2' if user.endswith('b' * 32) else '192.0.2.1'), \
                    patch.object(joind, 'handle_redeem', return_value=({'ok': False, 'error': 'fixture'}, False)) as redeem, \
                    patch.object(joind, 'FAIL_DELAY_S', 0):
                task = asyncio.create_task(joind.serve(hub, 'nats://fixture.invalid', 'fixture', stop=stop))
                try:
                    while not nc.subscribe.called:
                        await asyncio.sleep(0)
                    self.assertEqual(nc.subscribe.call_args.args[0], 'join.redeem.*')
                    callback = nc.subscribe.call_args.kwargs['cb']
                    def message(char):
                        user = 'join-' + char * 32
                        return SimpleNamespace(subject='join.redeem.' + user, reply='_INBOX_' + user + '.reply',
                                               data=json.dumps({'user': user, 'source': 'spoofed'}).encode(), respond=AsyncMock())
                    first, same_source, other = message('a'), message('c'), message('b')
                    for _ in range(30):
                        await callback(first)
                    self.assertEqual(redeem.call_count, 30)
                    await callback(first)
                    await callback(same_source)
                    self.assertEqual(redeem.call_count, 30)
                    await callback(other)
                    self.assertEqual(redeem.call_count, 31)
                    other.respond.assert_awaited_once()
                finally:
                    stop.set()
                    await task
        asyncio.run(run())


class GoLiveHardening(unittest.TestCase):
    def test_powershell_has_no_code_parameter(self):
        text = (ROOT / 'cluster/install/join.ps1').read_text()
        params = text.split('param(', 1)[1].split(')', 1)[0]
        self.assertNotIn('$Code', params)
        self.assertIn("Read-Host 'Join code (hidden)' -AsSecureString", text)

    def test_secret_cap_precedes_bcrypt_and_registry_lock(self):
        from cluster.registry import Registry, RegistryError
        reg = Registry(Path('/unused-fixture'))
        with patch.object(reg, 'locked', side_effect=AssertionError('oversize reached registry')) as locked, patch('cluster.registry.check_secret') as check:
            for size in (201, 256, 1000):
                with self.assertRaises(RegistryError):
                    reg.redeem('join-' + 'a' * 32, 's' * size, 'box')
            locked.assert_not_called()
            check.assert_not_called()

    def test_monitor_refuses_nonlocal_before_opening(self):
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **kw: io.BytesIO(b'{}')
            for address in ('example.invalid:8232', '192.0.2.1:8232', 'localhost:8232', '127.0.0.1:0'):
                with self.assertRaises(ValueError):
                    joind.monitor_json(address, 'connz?auth=1')
            opener.assert_not_called()

    def test_executable_decoy_with_matching_config_is_not_signalled(self):
        import sys
        # A real process with the expected -c argument but a different executable.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            conf = root / 'nats.conf'
            marker = root / 'signalled'
            ready = root / 'ready'
            script = root / 'decoy.py'
            script.write_text('import signal, time, pathlib\n'
                              + f"signal.signal(signal.SIGHUP, lambda *a: pathlib.Path({str(marker)!r}).touch())\n"
                              + f"pathlib.Path({str(ready)!r}).touch()\n"
                              + 'time.sleep(30)\n')
            proc = subprocess.Popen([sys.executable, str(script), '-c', str(conf)])
            try:
                import time
                for _ in range(100):
                    if ready.exists():
                        break
                    time.sleep(0.01)
                self.assertTrue(ready.exists())
                pid = root / 'pid'
                pid.write_text(str(proc.pid))
                with patch.object(joind, 'monitor_json', return_value={
                        'server_id': 'fixture', 'config_load_time': 'unchanged'}), \
                        patch.object(joind.os, 'kill', wraps=os.kill) as kill:
                    with self.assertRaises(RuntimeError):
                        joind.signal_reload_from_pidfile(pid, conf_out=conf, timeout=0.05)()
                    kill.assert_not_called()
                self.assertFalse(marker.exists())
                self.assertIsNone(proc.poll())
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_per_node_permissions_and_legacy_config(self):
        import re
        from cluster.registry import member_permissions, guest_permissions, render_conf, LEGACY_PERMS
        for permissions, stream in ((member_permissions, 'CAPJOBS'), (guest_permissions, 'SANDBOX')):
            caps = ['shell.test'] if stream == 'CAPJOBS' else ['sandbox.test']
            a, b = permissions('nodea', caps), permissions('nodeb', caps)
            acks = [s for s in a['publish'] if s.startswith('$JS.ACK.')]
            self.assertEqual(len(acks), 1)
            self.assertIn('_n_nodea.', acks[0])
            self.assertFalse(set(acks) & set(b['publish']))
            self.assertFalse(any('JOBS.workers' in s for s in a['publish']))
        registry = {'nodes': {}, 'pending': {}, 'legacy': {
            name: '$2b$04$' + 'a' * 53 for name in LEGACY_PERMS}}
        rendered = render_conf(registry, '/tmp/fixture-js')
        template = (ROOT / 'cluster/nats.conf.template').read_text()
        for text in (rendered, template):
            for node in ('mini', 'macbook'):
                block = re.split(r'user: "?' + node + r'"?', text)[1].split('user:', 1)[0]
                for forbidden in ('"$JS.API.>"', '"$JS.ACK.>"', '"_INBOX.>"', '"results.>"', '"$JS.ACK.JOBS.workers.>"'):
                    self.assertNotIn(forbidden, block)
                self.assertIn('"_INBOX_' + node + '.>"', block)
                self.assertIn('"results.' + node + '.>"', block)
            nuc = re.split(r'user: "?nucbox"?', text)[1].split('user:', 1)[0]
            self.assertNotIn('"$JS.ACK.>"', nuc)


class JoinConcurrencyHardening(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, body, *, source='192.0.2.1', allow_loopback=False, handler=None):
        nc = SimpleNamespace(subscribe=AsyncMock(), flush=AsyncMock(), drain=AsyncMock())
        stop = asyncio.Event()
        hub = SimpleNamespace(http='127.0.0.1:18232')
        with patch('nats.connect', AsyncMock(return_value=nc)), \
                patch.object(joind, 'source_address', side_effect=lambda *a: source), \
                patch.object(joind, 'handle_redeem', side_effect=handler or (lambda *a: ({'ok': False}, False))) as redeem, \
                patch.object(joind, 'FAIL_DELAY_S', 0):
            task = asyncio.create_task(joind.serve(hub, 'nats://fixture.invalid', 'fixture',
                                                stop=stop, allow_loopback=allow_loopback))
            try:
                while not nc.subscribe.called:
                    await asyncio.sleep(0)
                await body(nc.subscribe.call_args.kwargs['cb'], redeem)
            finally:
                stop.set()
                await task

    def message(self, char='a', body_char=None):
        user = 'join-' + char * 32
        return SimpleNamespace(subject='join.redeem.' + user, reply='_INBOX_' + user + '.reply',
                               data=json.dumps({'user': 'join-' + (body_char or char) * 32}).encode(),
                               respond=AsyncMock())

    async def test_body_user_must_equal_authenticated_subject_user(self):
        async def check(callback, redeem):
            msg = self.message('a', 'b')
            await callback(msg)
            redeem.assert_not_called()
            msg.respond.assert_not_awaited()
            await callback(self.message())
            self.assertEqual(redeem.call_count, 1)
        await self.exercise(check)

    async def test_loopback_without_opt_in_refused(self):
        async def check(callback, redeem):
            msg = self.message()
            await callback(msg)
            redeem.assert_not_called()
            msg.respond.assert_not_awaited()
        for source in ('127.0.0.1', '127.8.9.10', '::1'):
            await self.exercise(check, source=source)

    async def test_loopback_codes_have_independent_small_buckets_and_aggregate_cap(self):
        async def check(callback, redeem):
            for _ in range(40):
                await callback(self.message('a'))
            self.assertEqual(redeem.call_count, 3)
            for _ in range(40):
                await callback(self.message('b'))
            self.assertEqual(redeem.call_count, 6)
            await callback(self.message('c'))
            self.assertEqual(redeem.call_count, 6)
        await self.exercise(check, source='127.0.0.1', allow_loopback=True)

    async def test_per_user_bucket_cannot_be_reset_by_source_rotation(self):
        async def check(callback, redeem):
            for i in range(31):
                with patch.object(joind, 'source_address', return_value=f'192.0.2.{i+1}'):
                    await callback(self.message())
            self.assertEqual(redeem.call_count, 30)
            await callback(self.message('b'))
            self.assertEqual(redeem.call_count, 31)
        await self.exercise(check)

    async def test_joind_serializes_without_registry_flock(self):
        import threading
        import time
        guard = threading.Lock()
        active = 0
        peak = 0
        def handler(*args):
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with guard:
                active -= 1
            return {'ok': False}, False
        async def check(callback, redeem):
            await asyncio.gather(*(callback(self.message(c)) for c in 'abcde'))
            self.assertEqual(redeem.call_count, 5)
            self.assertEqual(peak, 1)
        await self.exercise(check, handler=handler)


class DispatchHardening(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_uses_subject_identity_and_rejects_stale_busy_or_bad_heartbeats(self):
        nc = SimpleNamespace(flush=AsyncMock())
        sub = SimpleNamespace(unsubscribe=AsyncMock())
        good = {'v': 'caps/v1', 'role': 'member', 'node': 'forged', 'ts': 100,
                'caps': ['shell.test'], 'active': 0, 'max_lanes': 2}
        async def subscribe(subject, cb):
            self.assertEqual(subject, 'caps.*')
            for node, changes in (('stale', {'ts': 0}), ('busy', {'active': 2}),
                                  ('bad', {'caps': 'shell.test'}), ('future', {'ts': 200}),
                                  ('wrongrole', {'role': 'guest'}), ('owner', {})):
                await cb(SimpleNamespace(subject='caps.' + node, data=json.dumps({**good, **changes}).encode()))
            return sub
        nc.subscribe = subscribe
        with patch.object(bobctl.time, 'time', return_value=100), patch.object(bobctl.asyncio, 'sleep', AsyncMock()):
            self.assertEqual(await bobctl.select_node(nc, 'shell.test'), 'owner')
        sub.unsubscribe.assert_awaited_once()

    async def test_dispatch_refuses_a_node_that_withdraws_capacity(self):
        from cluster.spec import SpecError
        nc = SimpleNamespace(flush=AsyncMock())
        async def subscribe(subject, cb):
            for caps in (['shell.test'], []):
                body = {'v': 'caps/v1', 'role': 'member', 'ts': 100, 'caps': caps, 'active': 0, 'max_lanes': 1}
                await cb(SimpleNamespace(subject='caps.owner', data=json.dumps(body).encode()))
            return SimpleNamespace(unsubscribe=AsyncMock())
        nc.subscribe = subscribe
        with patch.object(bobctl.time, 'time', return_value=100), patch.object(bobctl.asyncio, 'sleep', AsyncMock()):
            with self.assertRaises(SpecError):
                await bobctl.select_node(nc, 'shell.test')

    async def test_submit_creates_private_consumer_and_waits_only_for_its_owner(self):
        from cluster.routing import subject_for
        for cap, stream, prefix in (('shell.test', 'CAPJOBS', 'results'), ('sandbox.test', 'SANDBOX', 'sandbox.results')):
            job = {'id': 'job', 'cap': cap}
            js = SimpleNamespace(add_consumer=AsyncMock(), publish=AsyncMock(return_value=SimpleNamespace(stream=stream, seq=1)))
            nc = SimpleNamespace(jetstream=lambda: js, close=AsyncMock(), flush=AsyncMock(),
                                 subscribe=AsyncMock(return_value=SimpleNamespace(next_msg=AsyncMock(
                                     return_value=SimpleNamespace(data=b'{"status":"ok"}')))))
            with patch('nats.connect', AsyncMock(return_value=nc)), \
                    patch.object(bobctl, 'select_node', AsyncMock(return_value='owner')):
                result = await bobctl.submit_spec('nats://fixture.invalid', 'nucbox', 'fixture', job, wait=1)
            self.assertEqual(result['node'], 'owner')
            cfg = js.add_consumer.call_args.args[1]
            self.assertIn('_n_owner', cfg.durable_name)
            self.assertEqual(cfg.filter_subject, subject_for({**job, 'id': '*'}, 'owner'))
            nc.subscribe.assert_awaited_once_with(f'{prefix}.owner.job')
            self.assertEqual(js.publish.call_args.args[0], subject_for(job, 'owner'))
            nc.close.assert_awaited_once()

    def test_filters_and_workers_are_isolated_and_guests_require_podman(self):
        from cluster.capabilities import sandbox_runtime
        from cluster.mesh_worker import MeshWorker, NodeConfig, Executor
        for role, cap in (('member', 'shell.test'), ('guest', 'sandbox.test')):
            names = []
            filters = []
            for node in ('nodea', 'nodeb'):
                cfg = bobctl.delivery_consumer(cap, node)
                names.append(cfg.durable_name)
                filters.append(cfg.filter_subject)
                worker = MeshWorker(NodeConfig({'BOB_NODE_USER': node, 'BOB_NODE_TOKEN': 'fixture',
                    'BOB_ROLE': role, 'BOB_GRANTED_CAPS': cap}), Executor(role, True), providers={}, detected={cap})
                self.assertEqual(worker.consumers()[0][1], cfg.durable_name)
            self.assertNotEqual(names[0], names[1])
            self.assertNotEqual(filters[0], filters[1])
            self.assertIn('nodea', filters[0])
            self.assertIn('nodeb', filters[1])
        self.assertIsNone(sandbox_runtime(lambda name: '/bin/docker' if name == 'docker' else None))
        self.assertEqual(sandbox_runtime(lambda name: '/bin/podman' if name == 'podman' else None), 'podman')
