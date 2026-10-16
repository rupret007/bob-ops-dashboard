"""Security regressions: in-memory HTTP and launcher preflight, no live services."""
import email
import hmac
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from live.aggregator import AggregatorServer, COOKIE_NAME, MAX_BODY_BYTES, validate_bind
from live.private_config import read_private

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
        self.server = AggregatorServer(token=TOKEN)

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
        with patch('live.aggregator.hmac.compare_digest', wraps=hmac.compare_digest) as compare:
            status, headers, body = self.request('/?k=' + TOKEN + '&extra=discard')
            compare.assert_called_once_with(TOKEN.encode(), TOKEN.encode())
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        cookie = headers['Set-Cookie']
        for flag in ('HttpOnly', 'SameSite=Strict', 'Path=/'):
            self.assertIn(flag, cookie)
        self.assertNotIn(TOKEN, cookie + str(headers) + body.decode())
        for path in ('/', '/api/state', '/api/stream'):
            with patch('live.aggregator.hmac.compare_digest', wraps=hmac.compare_digest) as compare:
                status, _, body = self.request(path, headers=(('Cookie', cookie.split(';')[0]),))
                self.assertEqual(status, 200)
                compare.assert_called_once()
                if path.endswith('stream'):
                    self.assertIn(b'event: state', body)

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
        result = subprocess.run(['git', 'grep', '-nE', pattern, '--', '.'], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, 'Tracked tailnet address found in: ' + '\n'.join(line.split(':', 1)[0] for line in result.stdout.splitlines()))
        # Exercise the detector without adding an address literal to tracked files.
        self.assertRegex('.'.join(map(str, [100, 90, 1, 2])), pattern)


if __name__ == '__main__':
    unittest.main()
