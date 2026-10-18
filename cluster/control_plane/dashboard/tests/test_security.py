"""Regressions for the local dashboard's network and HTML boundaries."""

from http.client import HTTPConnection
from http.server import HTTPServer
from html.parser import HTMLParser
from threading import Thread
from unittest.mock import Mock

import pytest

from cluster.control_plane.dashboard.data_collector import (
    DashboardData, FixAttemptRecord, GuardValues, KarenVerdict, MergeRecord, PRStatus,
    ProviderBudget, ServiceHealth,
)
from cluster.control_plane.dashboard.server import DashboardHandler, DashboardServer, render_dashboard


class ParsedHTML(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.tags = []
        self.text = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, text):
        self.text.append(text)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1", "example.invalid"])
def test_dashboard_rejects_nonlocal_bind(host):
    with pytest.raises(ValueError, match="127.0.0.1"):
        DashboardServer(host=host)


def test_untrusted_status_fields_remain_text():
    payload = '\"><img src=x onerror="alert(1)">'
    data = DashboardData(
        timestamp="2026-10-18T12:00:00Z", machine=payload, live_mode=False,
        services=[ServiceHealth("worker", payload, "", "", "")],
        guards=GuardValues("mini", 1, 10, guards_passed=False, reason=payload),
        providers=[ProviderBudget("codex", 1, 0, 0, 75, payload)],
        verdicts=[KarenVerdict("owner/repo", payload, "a" * 40, payload, "", "checks", "")],
        merges_today_list=[MergeRecord("owner/repo", payload, "a" * 40, "")],
        fix_attempts=[FixAttemptRecord("owner/repo", payload, "", False, "codex")],
    )
    parsed = ParsedHTML(render_dashboard(data))
    assert not any(tag in {"img", "script"} for tag, _ in parsed.tags)
    assert all(not key.startswith("on") for _, attrs in parsed.tags for key in attrs)
    assert payload in "".join(parsed.text)
    assert payload.upper() in "".join(parsed.text)


@pytest.mark.parametrize("repo,number,expected", [
    ("owner/repo", 12, "https://github.com/owner/repo/pull/12"),
    ("../repo", 12, "#"),
    ("owner/..", 12, "#"),
    ("owner/repo?bad", 12, "#"),
    ("owner/repo", -1, "#"),
    ("owner/repo", True, "#"),
])
def test_backlog_links_use_validated_identity(repo, number, expected):
    pr = PRStatus(repo, number, "title", "draft", "pending", "author", "a" * 40,
                  "", url="javascript:alert(1)")
    data = DashboardData("", "mini", False, backlog=[pr])
    parsed = ParsedHTML(render_dashboard(data))
    links = [attrs for tag, attrs in parsed.tags if tag == "a"]
    assert len(links) == 1
    assert links[0]["href"] == expected
    assert "noopener" in links[0]["rel"]


@pytest.fixture
def dashboard_http(monkeypatch):
    collector = Mock()
    collector.collect.return_value = DashboardData("", "mini", False)
    monkeypatch.setattr(DashboardHandler, "collector", collector)
    server = HTTPServer(("127.0.0.1", 0), DashboardHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, collector
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.mark.parametrize("route", ["/", "/index.html", "/api/data", "/health"])
def test_foreign_host_is_rejected_before_reading_data(dashboard_http, route):
    port, collector = dashboard_http
    connection = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", route, headers={"Host": "attacker.invalid"})
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        collector.collect.assert_not_called()
    finally:
        connection.close()


def test_local_requests_succeed_with_browser_security_headers(dashboard_http):
    port, collector = dashboard_http
    connection = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert "frame-ancestors 'none'" in response.getheader("Content-Security-Policy")
        assert response.getheader("Referrer-Policy") == "no-referrer"
        assert b"StoryPool Dashboard" in response.read()
        collector.collect.assert_called_once()
    finally:
        connection.close()
