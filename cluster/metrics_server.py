"""Disabled-by-default same-origin SSE bridge. Start manually only after Karen gate."""
from __future__ import annotations
import argparse
import asyncio
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from cluster.live_metrics import validate


def private_bind(host):
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError('bind must be a literal loopback or tailnet address') from None
    if not (addr.is_loopback or addr in ipaddress.ip_network('100.64.0.0/10') or
            addr in ipaddress.ip_network('fd7a:115c:a1e0::/48')):
        raise ValueError('public, wildcard and LAN binds refused')
    return str(addr)


class Bridge:
    def __init__(self, host='127.0.0.1', port=8787):
        self.host, self.port = private_bind(host), port
        self.latest, self.clients = {}, set()
        self.server = self.subscription = None

    async def receive(self, msg):
        if len(msg.data) > 8192:
            return
        try:
            data = validate(json.loads(msg.data))
        except (ValueError, TypeError, KeyError):
            return
        if msg.subject != 'metrics.' + data['host']:
            return
        self.latest[data['host']] = data
        for queue in self.clients:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(data)

    async def start(self, nc):
        self.subscription = await nc.subscribe('metrics.*', cb=self.receive)
        await nc.flush()
        self.server = await asyncio.start_server(self.handle, self.host, self.port, limit=8192)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def close(self):
        for queue in self.clients:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(None)
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.subscription:
            await self.subscription.unsubscribe()

    async def handle(self, reader, writer):
        queue = None
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
            lines = header.decode('ascii').split('\r\n')
            method, path, _ = lines[0].split()
            headers = dict(line.split(':', 1) for line in lines[1:] if ':' in line)
            headers = {k.lower(): v.strip() for k, v in headers.items()}
            authority = f'[{self.host}]:{self.port}' if ':' in self.host else f'{self.host}:{self.port}'
            origin = headers.get('origin')
            if headers.get('host') != authority or (origin and origin != 'http://' + authority):
                writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
            elif method != 'GET' or path not in ('/', '/events', '/view.js'):
                writer.write(b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n')
            elif path != '/events':
                name = 'index.html' if path == '/' else 'view.js'
                body = (Path(__file__).parent / 'metrics_web' / name).read_bytes()
                mime = 'text/html; charset=utf-8' if path == '/' else 'text/javascript'
                writer.write((f'HTTP/1.1 200 OK\r\nContent-Type: {mime}\r\nContent-Length: {len(body)}\r\n'
                              "Content-Security-Policy: default-src 'self'; frame-ancestors 'none'\r\n"
                              'X-Content-Type-Options: nosniff\r\nCache-Control: no-store\r\n\r\n').encode() + body)
            elif len(self.clients) >= 32:
                writer.write(b'HTTP/1.1 503 Unavailable\r\nContent-Length: 0\r\n\r\n')
            else:
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-store\r\nX-Accel-Buffering: no\r\n\r\nretry: 2000\n\n')
                queue = asyncio.Queue(maxsize=4)
                self.clients.add(queue)
                for data in self.latest.values():
                    queue.put_nowait(data)
                while not reader.at_eof():
                    await asyncio.wait_for(writer.drain(), 5)
                    try:
                        data = await asyncio.wait_for(queue.get(), 10)
                        if data is None:
                            break
                        writer.write(b'event: metrics\ndata: ' + json.dumps(data, allow_nan=False).encode() + b'\n\n')
                    except asyncio.TimeoutError:
                        writer.write(b': heartbeat\n\n')
            await asyncio.wait_for(writer.drain(), 5)
        except (ValueError, UnicodeError, asyncio.TimeoutError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError, ConnectionError):
            pass
        finally:
            if queue is not None:
                self.clients.discard(queue)
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass


async def run(args):
    import nats
    private_bind(args.bind)
    # No URL credentials or raw exception logging. No JetStream/inbox API calls.
    url = os.environ['BOB_METRICS_NATS_URL']
    parts = urlsplit(url)
    if parts.username or parts.password:
        raise ValueError('use separate private credential environment variables')
    async def quiet_error(_error):
        pass
    nc = await nats.connect(url, user=os.environ['BOB_METRICS_VIEWER_USER'],
                            password=os.environ['BOB_METRICS_VIEWER_PASSWORD'], error_cb=quiet_error)
    bridge = await Bridge(args.bind, args.port).start(nc)
    try:
        await asyncio.Future()
    finally:
        await bridge.close()
        await nc.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit('Metrics bridge unavailable; check private configuration') from None


if __name__ == '__main__':
    main()
