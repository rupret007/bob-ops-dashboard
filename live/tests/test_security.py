"""Security regressions: in-memory HTTP and launcher preflight, no live services."""
import email
import fcntl
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from live.aggregator import AggregatorServer, COOKIE_NAME, MAX_BODY_BYTES, SESSION_MAX_AGE, validate_bind
from live.private_config import read_private
from live.view_codes import mint_view_code, redeem_view_code

ROOT = Path(__file__).resolve().parents[2]
TOKEN = 'synthetic-security-test-token'


class MemorySocket:
    def __init__(self, request, server):
        self.input = io.BytesIO(request)
        self.output = io.BytesIO()
        self.server = server

    def makefile(self, *args):
        return self.input

    def sendall(self, data):
        self.output.write(data)
        if b'event: state' in data:
            self.server._stop.set()
            self.server._notify_sse()


class HttpSecurityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.codes_path = Path(self.directory.name) / 'view-codes.json'
        self.now = 1700000000
        with patch.dict(os.environ, {'LIVE_VIEW_CODES_FILE': str(self.codes_path)}):
            self.server = AggregatorServer(token=TOKEN, clock=lambda: self.now)

    def mint_code(self, ttl=300):
        return mint_view_code(ttl, path=self.codes_path, now=self.now)

    def login_cookie(self):
        status, headers, _ = self.request('/?k=' + self.mint_code())
        self.assertEqual(status, 303)
        return headers['Set-Cookie'].split(';')[0]

    def request(self, path, *, method='GET', headers=(), body=b''):
        raw = f'{method} {path} HTTP/1.0\r\nHost: localhost\r\n'.encode()
        raw += b''.join(f'{k}: {v}\r\n'.encode() for k, v in headers)
        sock = MemorySocket(raw + b'\r\n' + body, self.server)
        self.server.make_handler()(sock, ('127.0.0.1', 12345), object())
        head, _, data = sock.output.getvalue().partition(b'\r\n\r\n')
        status, _, fields = head.partition(b'\r\n')
        return int(status.split()[1]), email.message_from_bytes(fields), data

    def test_all_data_and_html_routes_require_auth(self):
        for path in ('/', '/index.html', '/api/state', '/api/stream', '/api/future'):
            for headers in ((), (('Authorization', 'Bearer wrong'),), (('Cookie', f'{COOKIE_NAME}=wrong'),)):
                with self.subTest(path=path, headers=headers):
                    status, _, body = self.request(path, headers=headers)
                    self.assertEqual(status, 401)
                    self.assertEqual(body, b'')
        self.assertEqual(self.request('/healthz')[0], 200)
        self.assertEqual(self.request('/api/node', method='POST')[0], 401)
        self.assertEqual(self.request('/api/future', method='POST')[0], 401)

    def test_bearer_and_legacy_tokens_use_compare_digest(self):
        for name, value in (('Authorization', 'Bearer ' + TOKEN), ('X-Live-Token', TOKEN)):
            with patch('live.aggregator.hmac.compare_digest', wraps=hmac.compare_digest) as compare:
                status, _, body = self.request('/api/state', headers=((name, value),))
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)['version'], 'state/v1')
                compare.assert_called_once_with(TOKEN.encode(), TOKEN.encode())
        with patch('live.aggregator.hmac.compare_digest', wraps=hmac.compare_digest) as compare:
            self.assertEqual(self.request('/api/state', headers=(('Authorization', 'Bearer wrong'),))[0], 401)
            compare.assert_called_once_with(b'wrong', TOKEN.encode())

    def test_browser_login_cookie_redirect_and_authenticated_stream(self):
        code = self.mint_code()
        digest = hashlib.sha256(code.encode()).hexdigest()
        with patch('live.aggregator.hmac.compare_digest', wraps=hmac.compare_digest) as compare:
            status, headers, body = self.request('/?k=' + code + '&extra=discard')
            compare.assert_called_once_with(digest, digest)
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        cookie = headers['Set-Cookie']
        for flag in ('HttpOnly', 'SameSite=Strict', 'Path=/', f'Max-Age={SESSION_MAX_AGE}'):
            self.assertIn(flag, cookie)
        self.assertNotIn(TOKEN, cookie + str(headers) + body.decode())
        self.assertFalse(code in cookie + str(headers) + body.decode())
        for path in ('/', '/api/state', '/api/stream'):
            with patch('live.aggregator.hmac.compare_digest', wraps=hmac.compare_digest) as compare:
                status, _, body = self.request(path, headers=(('Cookie', cookie.split(';')[0]),))
                self.assertEqual(status, 200)
                compare.assert_called_once()
                if path.endswith('stream'):
                    self.assertIn(b'event: state', body)

    def test_permanent_token_is_never_a_view_code(self):
        self.mint_code()
        status, headers, _ = self.request('/?k=' + TOKEN)
        self.assertEqual(status, 401)
        self.assertIsNone(headers['Set-Cookie'])

    def test_code_is_consumed_and_reuse_rejected_even_with_cookie(self):
        code = self.mint_code()
        status, headers, _ = self.request('/?k=' + code)
        self.assertEqual(status, 303)
        cookie = headers['Set-Cookie'].split(';')[0]
        for auth in ((), (('Cookie', cookie),)):
            status, headers, _ = self.request('/?k=' + code, headers=auth)
            self.assertEqual(status, 401)
            self.assertIsNone(headers['Set-Cookie'])
        self.assertEqual(json.loads(self.codes_path.read_text()), {})

    def test_expired_view_code_rejected(self):
        code = self.mint_code(ttl=1)
        self.now += 1
        status, headers, _ = self.request('/?k=' + code)
        self.assertEqual(status, 401)
        self.assertIsNone(headers['Set-Cookie'])

    def test_session_cookie_expires_server_side(self):
        cookie = self.login_cookie()
        self.now += SESSION_MAX_AGE - 1
        self.assertEqual(self.request('/api/state', headers=(('Cookie', cookie),))[0], 200)
        self.now += 1
        for path in ('/', '/api/state', '/api/stream'):
            self.assertEqual(self.request(path, headers=(('Cookie', cookie),))[0], 401)

    def test_open_browser_stream_stops_at_session_expiry(self):
        cookie = self.login_cookie()
        request = f'GET /api/stream HTTP/1.0\r\nCookie: {cookie}\r\n\r\n'.encode()
        sock = MemorySocket(request, self.server)

        def receive(data):
            sock.output.write(data)
            if b'event: state' in data:
                self.now += SESSION_MAX_AGE

        with patch.object(sock, 'sendall', side_effect=receive), patch('live.aggregator.threading.Condition'):
            self.server.make_handler()(sock, ('127.0.0.1', 12345), object())
        self.assertEqual(sock.output.getvalue().count(b'event: state'), 1)
        self.assertEqual(self.server._sse_clients, [])

    def test_expired_stream_closes_keep_alive_connection(self):
        cookie = self.login_cookie()
        first = f'GET /api/stream HTTP/1.1\r\nHost: localhost\r\nCookie: {cookie}\r\n\r\n'
        second = f'GET /api/state HTTP/1.1\r\nHost: localhost\r\nCookie: {cookie}\r\n\r\n'
        sock = MemorySocket((first + second).encode(), self.server)

        def receive(data):
            sock.output.write(data)
            if b'event: state' in data:
                self.now += 1  # stays valid for the pipelined request if the socket were reused

        expires = self.now + 1
        with patch.object(sock, 'sendall', side_effect=receive), \
                patch('live.aggregator.threading.Condition'), \
                patch.object(self.server, 'session_expiry', return_value=expires):
            self.server.make_handler()(sock, ('127.0.0.1', 12345), object())
        out = sock.output.getvalue()
        self.assertEqual(out.count(b'event: state'), 1)
        # After the stream ends the server must not serve another request on the socket.
        self.assertEqual(out.count(b'HTTP/1.'), 1)

    def test_cookie_timestamp_is_signed_and_future_cookies_rejected(self):
        cookie = self.login_cookie()
        tampered = cookie.replace(str(self.now), str(self.now + 1))
        self.now += 1
        self.assertEqual(self.request('/api/state', headers=(('Cookie', tampered),))[0], 401)
        self.now -= 2
        self.assertEqual(self.request('/api/state', headers=(('Cookie', cookie),))[0], 401)
        self.now += 2
        self.server = AggregatorServer(token=TOKEN + '-rotated', clock=lambda: self.now)
        self.assertEqual(self.request('/api/state', headers=(('Cookie', cookie),))[0], 401)

    def test_cookie_only_upload_rejected_and_token_headers_accepted(self):
        cookie = self.login_cookie()
        body = json.dumps({'version': 'node/v1', 'node_id': 'nuc'}).encode()
        headers = (('Cookie', cookie), ('Content-Length', str(len(body))))
        self.assertEqual(self.request('/api/node', method='POST', headers=headers, body=body)[0], 401)
        self.assertEqual(self.server.store.node_reports, {})
        for header in (('Authorization', 'Bearer ' + TOKEN), ('X-Live-Token', TOKEN)):
            self.assertEqual(self.request('/api/node', method='POST', headers=headers + (header,), body=body)[0], 204)

    def test_cli_code_is_visible_to_running_server(self):
        # Capture the credential: never echo minted codes into test logs.
        env = {k: v for k, v in os.environ.items() if not k.startswith('LIVE_')}
        env['LIVE_VIEW_CODES_FILE'] = str(self.codes_path)
        result = subprocess.run([sys.executable, 'live/aggregator.py', 'mint-view-code'],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        code = result.stdout.strip()
        stored = json.loads(self.codes_path.read_text())
        expiry = next(iter(stored.values()))
        self.now = expiry - 300
        self.assertEqual(self.request('/?k=' + code)[0], 303)
        self.assertEqual(self.request('/?k=' + code)[0], 401)

    def test_login_rejects_missing_empty_bad_duplicate_and_unicode_keys(self):
        for path in ('/', '/?k=', '/?k=wrong', '/?k=%C3%A9', '/?k='+TOKEN+'&k='+TOKEN):
            with self.subTest(path=path):
                status, headers, _ = self.request(path)
                self.assertEqual(status, 401)
                self.assertIsNone(headers['Set-Cookie'])
        self.assertEqual(self.request('/api/state?k='+TOKEN)[0], 401)

    def post(self, length, body=b'', extra=()):
        headers = [('Authorization', 'Bearer ' + TOKEN)]
        if length is not None:
            headers.append(('Content-Length', length))
        return self.request('/api/node', method='POST', headers=tuple(headers)+extra, body=body)[0]

    def test_post_body_cap_and_length_validation_without_handler_crashes(self):
        for length in (str(MAX_BODY_BYTES + 1), '9' * 5000):
            self.assertEqual(self.post(length), 413)
        for length in ('-1', 'garbage', '1.2', '+2', '', None):
            with self.subTest(length=length):
                self.assertEqual(self.post(length), 400)
        self.assertEqual(self.post('2', b'{}', (('Content-Length', '2'),)), 400)
        self.assertEqual(self.post('2', b'{}', (('Transfer-Encoding', 'chunked'),)), 400)
        for body in (b'\xff', b'[]', b'null', b'"text"', b'{', b'[' * 2000 + b']' * 2000):
            self.assertEqual(self.post(str(len(body)), body), 400)
        body = json.dumps({'version': 'node/v1', 'node_id': 'nuc'}).encode()
        padded = body + b' ' * (MAX_BODY_BYTES - len(body))
        self.assertEqual(self.post(str(MAX_BODY_BYTES), padded), 204)
        self.assertEqual(self.post('000' + str(len(body)), body), 204)
        self.assertEqual(self.request('/api/state', headers=(('Authorization', 'Bearer '+TOKEN),))[0], 200)


class StartupSecurityTests(unittest.TestCase):
    def env(self):
        return {k: v for k, v in os.environ.items() if not k.startswith(('LIVE_', 'BOB_LIVE_'))}

    def test_missing_empty_token_fails_before_startup(self):
        for token in (None, '', '   '):
            env = self.env()
            if token is not None:
                env['LIVE_TOKEN'] = token
            result = subprocess.run([sys.executable, 'live/aggregator.py'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('LIVE_TOKEN must be set and non-empty', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            with self.assertRaisesRegex(ValueError, 'LIVE_TOKEN'):
                AggregatorServer(token=token)

    def test_bind_allowlist_and_default(self):
        tailnet_start = '.'.join(map(str, [100, 64, 0, 0]))
        tailnet_end = '.'.join(map(str, [100, 127, 255, 255]))
        for host in ('127.0.0.1', '127.1.2.3', '::1', tailnet_start, tailnet_end):
            self.assertEqual(validate_bind(host), host)
        for host in ('0.0.0.0', '::', '192.0.2.1', 'localhost', '', '::ffff:127.0.0.1',
                     '::ffff:' + '.'.join(map(str, [100, 64, 0, 1])), '::ffff:7f00:1', '::ffff:6440:1',
                     '.'.join(map(str, [100, 63, 255, 255])), '.'.join(map(str, [100, 128, 0, 0]))):
            with self.subTest(host=host):
                result = subprocess.run([sys.executable, 'live/aggregator.py'], cwd=ROOT,
                                        env={**self.env(), 'LIVE_TOKEN': TOKEN, 'LIVE_BIND': host}, capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('LIVE_BIND', result.stderr)
                with self.assertRaisesRegex(ValueError, 'LIVE_BIND'):
                    AggregatorServer(token=TOKEN, host=host)
        result = subprocess.run([sys.executable, 'live/aggregator.py', '--check-config'], cwd=ROOT,
                                env={**self.env(), 'LIVE_TOKEN': TOKEN}, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_launchers_require_private_token_file_even_if_env_token_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            token = home / '.config/bob-live/token'
            token.parent.mkdir(parents=True)
            env = {**self.env(), 'HOME': directory, 'LIVE_TOKEN': TOKEN, 'BOB_LIVE_PUSH': 'http://aggregator.example.invalid/api/node'}
            for contents, mode in ((None, 0o600), ('', 0o600), (TOKEN, 0o644)):
                if contents is not None:
                    token.write_text(contents)
                    token.chmod(mode)
                for launcher in ('run-mini.sh', 'run-nucbox.sh'):
                    result = subprocess.run(['bash', str(ROOT / 'live' / launcher)], env=env, capture_output=True, text=True, timeout=5)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('live configuration:', result.stderr)
                    self.assertFalse((home / '.local').exists())
            token.write_text(TOKEN + '\n')
            token.chmod(0o600)
            result = subprocess.run(['bash', '-c', 'source "$ROOT/live/load-env.sh"; python3 -c \'import os; print(os.environ["LIVE_TOKEN"])\''],
                                    env={**env, 'ROOT': str(ROOT)}, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), TOKEN)
            # Existing test-process PIDs prevent launchers from starting any service.
            state = home / '.local/state/bob-live'
            state.mkdir(parents=True)
            for name in ('mini-agent', 'nuc-agent', 'aggregator'):
                (state / (name + '.pid')).write_text(str(os.getpid()))
            config = token.parent / 'live.env'
            config.write_text('BOB_LIVE_PUSH=http://aggregator.example.invalid/api/node\nLIVE_BIND=127.0.0.1\n')
            config.chmod(0o600)
            env.pop('BOB_LIVE_PUSH')
            for launcher in ('run-mini.sh', 'run-nucbox.sh'):
                result = subprocess.run(['bash', str(ROOT / 'live' / launcher)], env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('already running', result.stdout)
            config.unlink()
            result = subprocess.run(['bash', str(ROOT / 'live/run-mini.sh')], env=env, capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('BOB_LIVE_PUSH', result.stderr)

    def test_private_files_cannot_live_in_published_checkout(self):
        with self.assertRaisesRegex(ValueError, 'outside the repository'):
            read_private(ROOT / 'live/token')
        result = subprocess.run(['git', 'check-ignore', 'live/token', 'live.env', '.config/bob-live/token'], cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(len(result.stdout.splitlines()), 3)

    def test_no_tracked_tailnet_ip_literals(self):
        pattern = r'\b100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b'
        magic_dns = r'\b[a-z0-9_-]+(\.[a-z0-9_-]+)*\.ts\.net\b'
        result = subprocess.run(['git', 'grep', '-niE', pattern + '|' + magic_dns, '--', '.'], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, 'Tracked tailnet address found in: ' + '\n'.join(line.split(':', 1)[0] for line in result.stdout.splitlines()))
        # Exercise the detector without adding an address literal to tracked files.
        self.assertRegex('.'.join(map(str, [100, 90, 1, 2])), pattern)
        self.assertRegex('.'.join(['node', 'example', 'ts', 'net']), magic_dns)
        self.assertRegex('.'.join(['example', 'ts', 'net']), magic_dns)

    def test_readme_documents_view_code_flow(self):
        readme = (ROOT / 'live/README.md').read_text()
        for required in ('mint-view-code', '--ttl', 'LIVE_VIEW_CODES_FILE', 'single-use', '12 hours', 'Max-Age'):
            self.assertIn(required, readme)
        self.assertNotIn('?k=<URL-encoded-token>', readme)


class ViewCodeStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'private/view-codes.json'

    def test_private_store_contains_only_hashes_and_retains_other_codes(self):
        first = mint_view_code(path=self.path, now=1000)
        second = mint_view_code(3600, path=self.path, now=1000)
        self.assertFalse(first in self.path.read_text())
        self.assertFalse(second in self.path.read_text())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(json.loads(self.path.read_text()).values()), [1300, 4600])
        self.assertTrue(redeem_view_code(first, path=self.path, now=1001))
        self.assertTrue(redeem_view_code(second, path=self.path, now=1001))

    def test_concurrent_redemption_succeeds_once(self):
        code = mint_view_code(path=self.path, now=1000)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: redeem_view_code(code, path=self.path, now=1001), range(16)))
        self.assertEqual(sum(results), 1)

    def test_expiry_is_checked_after_waiting_for_store_lock(self):
        code = mint_view_code(path=self.path, now=1000)
        current = 1001
        lock = fcntl.flock

        def acquire(stream, operation):
            nonlocal current
            lock(stream, operation)
            current = 1300

        with patch('live.view_codes.fcntl.flock', side_effect=acquire):
            self.assertFalse(redeem_view_code(code, path=self.path, clock=lambda: current))

    def test_invalid_ttl_rejected_by_cli_without_creating_store(self):
        for ttl in ('0', '-1', '3601', 'invalid'):
            result = subprocess.run([sys.executable, 'live/aggregator.py', 'mint-view-code', '--ttl', ttl],
                                    cwd=ROOT, env={**os.environ, 'LIVE_VIEW_CODES_FILE': str(self.path)},
                                    capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b'')
            self.assertFalse(self.path.exists())

    def test_missing_unsafe_corrupt_and_repository_stores_fail_closed(self):
        self.assertFalse(redeem_view_code('wrong', path=self.path))
        code = mint_view_code(path=self.path)
        self.path.chmod(0o644)
        self.assertFalse(redeem_view_code(code, path=self.path))
        with self.assertRaises(ValueError):
            mint_view_code(path=self.path)
        self.path.chmod(0o600)
        for raw in ('{', '[]', '{"bad": 123}', '{"bad": "expiry"}'):
            self.path.write_text(raw)
            self.assertFalse(redeem_view_code(code, path=self.path))
        with self.assertRaisesRegex(ValueError, 'outside the repository'):
            mint_view_code(path=ROOT / 'live/view-codes.json')
        link = self.path.with_name('link.json')
        link.symlink_to(self.path)
        self.assertFalse(redeem_view_code(code, path=link))


if __name__ == '__main__':
    unittest.main()
