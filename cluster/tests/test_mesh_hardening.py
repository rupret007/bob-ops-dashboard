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
