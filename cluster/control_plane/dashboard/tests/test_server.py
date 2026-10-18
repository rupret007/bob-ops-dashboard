"""Tests for dashboard HTTP server."""

import json
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

from cluster.control_plane.dashboard.server import (
    DashboardServer,
    DashboardHandler,
    render_dashboard,
    _escape,
    _render_services,
    _render_guards,
    _render_providers,
)
from cluster.control_plane.dashboard.data_collector import (
    DataCollector,
    DashboardData,
    ServiceHealth,
    GuardValues,
    ProviderBudget,
)


class TestEscapeFunction:
    """Tests for HTML escaping."""
    
    def test_escape_html(self):
        """Should escape HTML special chars."""
        assert _escape("<script>") == "&lt;script&gt;"
        assert _escape("a & b") == "a &amp; b"
        assert _escape('"quoted"') == "&quot;quoted&quot;"
    
    def test_escape_safe_string(self):
        """Safe strings should pass through."""
        assert _escape("hello world") == "hello world"
        assert _escape("test-123") == "test-123"


class TestRenderFunctions:
    """Tests for render helper functions."""
    
    def test_render_services(self):
        """Should render services card."""
        data = DashboardData(
            timestamp="2026-10-18T12:00:00Z",
            machine="mini",
            live_mode=True,
            services=[
                ServiceHealth(
                    name="shadow",
                    status="running",
                    last_run="2026-10-18T11:55:00Z",
                    last_run_ago="5m ago",
                    log_file="/tmp/shadow.log",
                ),
            ],
        )
        
        html = _render_services(data)
        
        assert "shadow" in html
        assert "running" in html.lower() or "status-running" in html
        assert "5m ago" in html
    
    def test_render_guards(self):
        """Should render guards card."""
        data = DashboardData(
            timestamp="2026-10-18T12:00:00Z",
            machine="mini",
            live_mode=True,
            guards=GuardValues(
                machine="mini",
                load=5.0,
                load_limit=10.0,
                swap_pct=30.0,
                swap_limit=80.0,
                disk_gb=25.0,
                disk_min=15.0,
                guards_passed=True,
            ),
        )
        
        html = _render_guards(data)
        
        assert "Load" in html
        assert "5.0" in html
        assert "Swap" in html
        assert "Disk" in html
    
    def test_render_providers(self):
        """Should render providers card."""
        data = DashboardData(
            timestamp="2026-10-18T12:00:00Z",
            machine="mini",
            live_mode=True,
            providers=[
                ProviderBudget(
                    name="codex",
                    weekly_pct=50.0,
                    daily_spend_usd=2.0,
                    weekly_spend_usd=10.0,
                    cap_pct=75.0,
                    status="ok",
                ),
                ProviderBudget(
                    name="grok",
                    weekly_pct=0.0,
                    daily_spend_usd=0.0,
                    weekly_spend_usd=0.0,
                    cap_pct=0.0,
                    status="conserve",
                ),
            ],
        )
        
        html = _render_providers(data)
        
        assert "codex" in html
        assert "50.0" in html
        assert "grok" in html
        assert "conserve" in html


class TestRenderDashboard:
    """Tests for full dashboard rendering."""
    
    def test_render_dashboard_basic(self):
        """Should render complete dashboard."""
        data = DashboardData(
            timestamp="2026-10-18T12:00:00Z",
            machine="mini",
            live_mode=True,
        )
        
        html = render_dashboard(data)
        
        assert "<!DOCTYPE html>" in html
        assert "StoryPool Dashboard" in html
        assert "MINI" in html
        assert "LIVE" in html
        assert "auto-refresh" in html.lower()
    
    def test_render_dashboard_shadow_mode(self):
        """Should show SHADOW badge in shadow mode."""
        data = DashboardData(
            timestamp="2026-10-18T12:00:00Z",
            machine="nucbox",
            live_mode=False,
        )
        
        html = render_dashboard(data)
        
        assert "SHADOW" in html
        assert "mode-shadow" in html


class TestDashboardHandler:
    """Tests for HTTP handler."""
    
    def test_handler_has_collector(self):
        """Handler should accept collector."""
        collector = DataCollector()
        DashboardHandler.collector = collector
        assert DashboardHandler.collector is collector


class TestDashboardServer:
    """Tests for dashboard server."""
    
    def test_server_init(self):
        """Server should initialize with defaults."""
        server = DashboardServer()
        
        assert server.host == "127.0.0.1"
        assert server.port == 8790
        assert server.collector is not None
    
    def test_server_custom_port(self):
        """Server should accept custom port."""
        server = DashboardServer(port=9999)
        
        assert server.port == 9999
    
    def test_server_start_stop(self, tmp_path: Path):
        """Server should start and stop cleanly."""
        import socket
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        
        collector = DataCollector(
            data_dir=tmp_path / "storypool",
            karen_dir=tmp_path / "karen",
            metrics_dir=tmp_path / "metrics",
        )
        (tmp_path / "storypool").mkdir()
        (tmp_path / "karen").mkdir()
        (tmp_path / "metrics").mkdir()
        
        server = DashboardServer(port=port, collector=collector)
        
        thread = threading.Thread(target=server.start, daemon=True)
        thread.start()
        
        time.sleep(0.5)
        
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                assert resp.status == 200
                assert resp.headers.get_content_type() == "application/json"
                data = json.loads(resp.read().decode())
                assert data["status"] == "ok"
        finally:
            server.stop()
            thread.join(timeout=2)
        assert not thread.is_alive()
