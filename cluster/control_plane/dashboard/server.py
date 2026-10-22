#!/usr/bin/env python3
"""StoryPool Dashboard HTTP Server.

Stdlib-only Python web server serving real-time cluster status.
Serves on 127.0.0.1:8790 (local only, Tailscale for remote access).

Auto-refreshes every 30s from existing JSON/log files.
Read-only, no secrets displayed.
"""

from __future__ import annotations

from . import system_views, work_views

import html
import json
import os
import re
import socket
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

from .data_collector import DataCollector, DashboardData
from .tachometer import EngineCollector

DEFAULT_PORT = 8790
DEFAULT_HOST = "127.0.0.1"

from .template import HTML_TEMPLATE


def _escape(s: str) -> str:
    """Escape HTML special characters."""
    return system_views._escape(globals(), s)


def _render_services(data: DashboardData) -> str:
    """Render services card."""
    return system_views._render_services(globals(), data)


def _render_guards(data: DashboardData) -> str:
    """Render guards card."""
    return system_views._render_guards(globals(), data)


def _render_merges(data: DashboardData) -> str:
    """Render merges card."""
    return work_views._render_merges(globals(), data)


def _render_providers(data: DashboardData) -> str:
    """Render providers budget card."""
    return system_views._render_providers(globals(), data)


def _render_scorecard(data: DashboardData) -> str:
    """Render scorecard metrics card."""
    return system_views._render_scorecard(globals(), data)


def _render_backlog(data: DashboardData) -> str:
    """Render backlog table card."""
    return work_views._render_backlog(globals(), data)


def _render_verdicts(data: DashboardData) -> str:
    """Render verdicts card."""
    return work_views._render_verdicts(globals(), data)


def _render_fix_attempts(data: DashboardData) -> str:
    """Render fix attempts card."""
    return work_views._render_fix_attempts(globals(), data)


def _render_watchdog(data: DashboardData) -> str:
    """Render watchdog events card."""
    return work_views._render_watchdog(globals(), data)


def _render_alerts(data: DashboardData) -> str:
    """Render NEEDS_JEFF alerts."""
    return system_views._render_alerts(globals(), data)


def render_dashboard(data: DashboardData) -> str:
    """Render complete dashboard HTML."""
    return system_views.render_dashboard(globals(), data)


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for dashboard."""
    
    collector: Optional[DataCollector] = None
    
    def log_message(self, format: str, *args) -> None:
        """Suppress default logging."""
        pass
    
    def do_GET(self) -> None:
        """Handle GET requests."""
        from ..node_control import allowed_address
        if not allowed_address(self.client_address[0]):
            self.send_error(403, "Localhost or tailnet required")
            return
        parsed = urlparse(self.path)
        
        if parsed.path == "/" or parsed.path == "/index.html":
            self._serve_dashboard()
        elif parsed.path == "/api/engine":
            self._serve_engine()
        elif parsed.path == "/api/health":
            self._serve_api_health()
        elif parsed.path == "/api/data":
            self._serve_api_data()
        elif parsed.path == "/api/karen/status":
            self._serve_karen_status()
        elif parsed.path == "/health":
            self._serve_health()
        else:
            self._serve_404()
    
    def _serve_karen_status(self) -> None:
        """Read-only delivery and dispatch evidence; no execution endpoint."""
        from ..local_karen.config import KarenConfig
        source = self.collector or DataCollector()
        from ..local_karen.runtime_status import public_status
        payload = {"runtime": public_status(KarenConfig.load())}
        for key, path in (("receipt", KarenConfig.load().get_verdict_dir() / "push-receipt.json"),
                          ("dispatch", source.data_dir / "dispatch" / "dispatch-status.json")):
            try:
                payload[key] = json.loads(path.read_text())
            except (OSError, ValueError):
                payload[key] = None
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _control_node(self):
        from ..node_control import audit_action, authorized_post, set_maintenance
        if not authorized_post(self.client_address[0], self.headers.get('Host', ''),
                               self.headers.get('Origin', ''), self.headers.get('Content-Type', '')):
            self.send_error(403, 'Localhost/tailnet and same origin required')
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024:
                raise ValueError('Invalid body size')
            self.connection.settimeout(5)
            body = json.loads(self.rfile.read(length))
            if body.get('confirm') is not True or type(body.get('maintenance')) is not bool:
                raise ValueError('Explicit confirmation required')
            source = self.collector or DataCollector()
            target = body.get('machine')
            if target not in ('mini', 'nucbox'):
                raise ValueError('Unknown node')
            if target != source.machine:
                from ..watchdog import PEER_URLS
                import urllib.request
                url = PEER_URLS[target]
                request = urllib.request.Request(url + '/api/control/node',
                    data=json.dumps(body).encode(), method='POST',
                    headers={'Content-Type': 'application/json', 'Origin': url})
                # Never use a public proxy for tailnet control traffic.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(request, timeout=5) as response:
                    result = json.loads(response.read(4096))
                if result.get('maintenance') is not body['maintenance']:
                    raise ValueError('Invalid peer receipt')
                # Update only acknowledged control state, never job freshness.
                peer_file = source.data_dir / 'machines' / target / 'engine.json'
                from .tachometer import read_json
                snapshot = read_json(peer_file) or {}
                audit_action('Jeff/operator via ' + self.client_address[0] + ': ' + target + ' confirmed ' + ('maintenance' if body['maintenance'] else 'return to service'), source.data_dir)
                snapshot['control'] = result
                peer_file.parent.mkdir(parents=True, exist_ok=True)
                temp = peer_file.with_suffix('.control.tmp')
                temp.write_text(json.dumps(snapshot))
                temp.replace(peer_file)
            else:
                result = set_maintenance(body['maintenance'],
                    'Jeff/operator via ' + self.client_address[0], source.data_dir)
            engine = getattr(self.server, 'engine_collector', None)
            if engine:
                engine._at = 0
            payload = json.dumps(result).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(payload)
        except (ValueError, TypeError, AttributeError):
            self.send_error(400, 'Invalid confirmed node control')
        except Exception:
            self.send_error(503, 'Node control unavailable; state not confirmed')

    def do_POST(self) -> None:
        if self.path == '/api/control/node':
            self._control_node()
            return
        from ..local_karen.verdict_push import PUSH_PATH, MAX_BYTES, receive
        if self.path != PUSH_PATH:
            self._serve_404()
            return
        if not self.headers.get("X-Karen-Signature"):
            self.send_error(403, "Karen signature required")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BYTES:
                self.send_error(413)
                return
            self.connection.settimeout(15)
            body = self.rfile.read(length)
            receipt = receive(body, self.headers.get("X-Karen-Signature", ""))
            payload = json.dumps(receipt).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except PermissionError:
            self.send_error(403, "Invalid Karen signature")
        except (ValueError, TypeError, KeyError):
            self.send_error(400, "Invalid Karen delivery")
        except Exception:
            self.send_error(503, "Karen delivery unavailable")

    def _serve_dashboard(self) -> None:
        """Serve main dashboard HTML."""
        try:
            data = self.collector.collect() if self.collector else DashboardData(
                timestamp=datetime.now(timezone.utc).isoformat(),
                machine="unknown",
                live_mode=False,
            )
            html_content = render_dashboard(data)
            
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(html_content.encode("utf-8"))
        except Exception as e:
            self._serve_error(str(e))
    
    def _serve_api_data(self) -> None:
        """Serve API data as JSON."""
        try:
            data = self.collector.collect() if self.collector else DashboardData(
                timestamp=datetime.now(timezone.utc).isoformat(),
                machine="unknown",
                live_mode=False,
            )
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(json.dumps(data.to_dict(), indent=2).encode("utf-8"))
        except Exception as e:
            self._serve_error(str(e))
    
    def _serve_api_health(self) -> None:
        from ..health_state import read_status
        from ..health_service import public_snapshot
        nonce = parse_qs(urlparse(self.path).query).get('nonce', [None])[0]
        if nonce is not None and not re.fullmatch(r'[0-9a-f]{32}', nonce):
            self.send_error(400, 'Invalid observation challenge')
            return
        source = self.collector or DataCollector()
        payload = json.dumps(public_snapshot(read_status(source.data_dir), nonce)).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def _serve_engine(self) -> None:
        try:
            collector = getattr(self.server, 'engine_collector', None)
            if collector is None:
                source = self.collector or DataCollector()
                collector = EngineCollector(source.data_dir, source.karen_dir, source.machine)
                self.server.engine_collector = collector
            payload = json.dumps(collector.collect()).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(payload)
        except Exception:
            self._serve_error('Engine snapshot unavailable')

    def _serve_health(self) -> None:
        """Serve health check endpoint."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
    
    def _serve_404(self) -> None:
        """Serve 404 response."""
        self.send_response(404)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Not Found")
    
    def _serve_error(self, message: str) -> None:
        """Serve error response."""
        self.send_response(500)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(f"Internal Server Error: {message}".encode("utf-8"))


class DashboardServer:
    """StoryPool dashboard HTTP server."""
    
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        collector: Optional[DataCollector] = None,
    ):
        self.host = host
        self.port = port
        self.collector = collector or DataCollector()
        self._server: Optional[HTTPServer] = None
    
    def start(self) -> None:
        """Start the dashboard server."""
        DashboardHandler.collector = self.collector
        
        self._server = HTTPServer((self.host, self.port), DashboardHandler)
        print(f"StoryPool Dashboard running at http://{self.host}:{self.port}/")
        
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            self._server.server_close()
    
    def stop(self) -> None:
        """Stop the dashboard server."""
        if self._server:
            self._server.shutdown()


def main() -> int:
    """CLI entry point for dashboard server."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="StoryPool local dashboard server"
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Host to bind to (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="StoryPool data directory",
    )
    
    args = parser.parse_args()
    
    collector = DataCollector(data_dir=args.data_dir) if args.data_dir else None
    server = DashboardServer(
        host=args.host,
        port=args.port,
        collector=collector,
    )
    
    # Mini only in practice: binds the Tailscale address when this host owns it,
    # so the NucBox dispatcher can pull trusted local Karen verdicts.
    try:
        from ..local_karen.verdict_sync import start_share_thread
        start_share_thread()
    except Exception as exc:  # never let sharing break the dashboard
        print(f"verdict share unavailable: {exc}")

    server.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
