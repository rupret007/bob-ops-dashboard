#!/usr/bin/env python3
"""StoryPool Dashboard HTTP Server.

Stdlib-only Python web server serving real-time cluster status.
Serves on 127.0.0.1:8790 (local only, Tailscale for remote access).

Auto-refreshes every 30s from existing JSON/log files.
Read-only, no secrets displayed.
"""

from __future__ import annotations

import html
import json
import os
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

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">

    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>StoryPool Dashboard - {machine}</title>
    <style>
        :root {{
            --bg: #0d1117;
            --card-bg: #161b22;
            --border: #30363d;
            --text: #c9d1d9;
            --text-muted: #8b949e;
            --accent: #58a6ff;
            --success: #3fb950;
            --warning: #d29922;
            --error: #f85149;
            --shadow: #010409;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.5;
            padding: 1rem;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
            padding-bottom: 1rem;
            border-bottom: 1px solid var(--border);
        }}
        .header h1 {{
            font-size: 1.5rem;
            font-weight: 600;
        }}
        .mode-badge {{
            padding: 0.25rem 0.75rem;
            border-radius: 1rem;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
        }}
        .mode-live {{ background: var(--success); color: var(--bg); }}
        .mode-shadow {{ background: var(--warning); color: var(--bg); }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
            gap: 1rem;
        }}
        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 1rem;
        }}
        .card-title {{
            font-size: 0.875rem;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.75rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .card-full {{ grid-column: 1 / -1; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.875rem;
        }}
        th, td {{
            padding: 0.5rem;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }}
        th {{
            color: var(--text-muted);
            font-weight: 500;
        }}
        tr:last-child td {{ border-bottom: none; }}
        .status {{
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 0.5rem;
        }}
        .status-running {{ background: var(--success); }}
        .status-stopped {{ background: var(--warning); }}
        .status-error {{ background: var(--error); }}
        .status-unknown {{ background: var(--text-muted); }}
        .badge {{
            display: inline-block;
            padding: 0.125rem 0.5rem;
            border-radius: 1rem;
            font-size: 0.75rem;
            font-weight: 500;
        }}
        .badge-pass {{ background: rgba(63, 185, 80, 0.2); color: var(--success); }}
        .badge-fail {{ background: rgba(248, 81, 73, 0.2); color: var(--error); }}
        .badge-pending {{ background: rgba(210, 153, 34, 0.2); color: var(--warning); }}
        .badge-ok {{ background: rgba(63, 185, 80, 0.2); color: var(--success); }}
        .badge-warning {{ background: rgba(210, 153, 34, 0.2); color: var(--warning); }}
        .badge-capped {{ background: rgba(248, 81, 73, 0.2); color: var(--error); }}
        .badge-conserve {{ background: rgba(139, 148, 158, 0.2); color: var(--text-muted); }}
        .metric {{
            display: flex;
            justify-content: space-between;
            padding: 0.5rem 0;
            border-bottom: 1px solid var(--border);
        }}
        .metric:last-child {{ border-bottom: none; }}
        .metric-label {{ color: var(--text-muted); }}
        .metric-value {{ font-weight: 600; font-family: monospace; }}
        .metric-delta {{
            font-size: 0.75rem;
            margin-left: 0.5rem;
        }}
        .delta-up {{ color: var(--success); }}
        .delta-down {{ color: var(--error); }}
        .progress-bar {{
            height: 6px;
            background: var(--border);
            border-radius: 3px;
            overflow: hidden;
            margin-top: 0.25rem;
        }}
        .progress-fill {{
            height: 100%;
            border-radius: 3px;
            transition: width 0.3s;
        }}
        .progress-ok {{ background: var(--success); }}
        .progress-warning {{ background: var(--warning); }}
        .progress-error {{ background: var(--error); }}
        .alert {{
            background: rgba(248, 81, 73, 0.1);
            border: 1px solid var(--error);
            border-radius: 6px;
            padding: 1rem;
            margin-bottom: 1rem;
        }}
        .alert-title {{
            font-weight: 600;
            color: var(--error);
            margin-bottom: 0.5rem;
        }}
        a {{
            color: var(--accent);
            text-decoration: none;
        }}
        a:hover {{ text-decoration: underline; }}
        .timestamp {{
            font-size: 0.75rem;
            color: var(--text-muted);
        }}
        .empty-state {{
            text-align: center;
            padding: 2rem;
            color: var(--text-muted);
        }}
        .truncate {{
            max-width: 200px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>StoryPool Dashboard</h1>
        <div>
            <span class="mode-badge mode-{mode_class}">{mode}</span>
            <span class="timestamp">Updated: {timestamp} (engine auto-refresh 3s)</span>
        </div>
    </div>
    
    {alerts}
    
    <div class="grid">
        {services_card}
        {guards_card}
        {merges_card}
        {providers_card}
        {scorecard_card}
        {backlog_card}
        {verdicts_card}
        {fix_attempts_card}
        {watchdog_card}
    </div>
</body>
</html>
"""


def _escape(s: str) -> str:
    """Escape HTML special characters."""
    return html.escape(str(s))


def _render_services(data: DashboardData) -> str:
    """Render services card."""
    rows = []
    for svc in data.services:
        status_class = f"status-{svc.status}"
        rows.append(f"""
            <tr>
                <td><span class="status {status_class}"></span>{_escape(svc.name)}</td>
                <td>{_escape(svc.last_run_ago)}</td>
                <td>{svc.error_count} errors</td>
            </tr>
        """)
    
    return f"""
        <div class="card">
            <div class="card-title">Services ({data.machine})</div>
            <table>
                <tr><th>Service</th><th>Last Run</th><th>Status</th></tr>
                {''.join(rows) if rows else '<tr><td colspan="3" class="empty-state">No services found</td></tr>'}
            </table>
        </div>
    """


def _render_guards(data: DashboardData) -> str:
    """Render guards card."""
    if not data.guards:
        return ""
    
    g = data.guards
    
    load_pct = min(100, (g.load / g.load_limit) * 100)
    load_class = "ok" if load_pct < 70 else ("warning" if load_pct < 90 else "error")
    
    metrics = [
        f"""<div class="metric">
            <span class="metric-label">Load</span>
            <span class="metric-value">{g.load:.1f} / {g.load_limit:.0f}</span>
        </div>
        <div class="progress-bar"><div class="progress-fill progress-{load_class}" style="width:{load_pct:.0f}%"></div></div>
        """
    ]
    
    if data.machine == "mini":
        swap_class = "ok" if g.swap_pct < 60 else ("warning" if g.swap_pct < 80 else "error")
        metrics.append(f"""<div class="metric">
            <span class="metric-label">Swap</span>
            <span class="metric-value">{g.swap_pct:.1f}%</span>
        </div>
        <div class="progress-bar"><div class="progress-fill progress-{swap_class}" style="width:{g.swap_pct:.0f}%"></div></div>
        """)
        
        disk_pct = min(100, max(0, 100 - (g.disk_gb / 50) * 100))
        disk_class = "ok" if g.disk_gb > 20 else ("warning" if g.disk_gb > 15 else "error")
        metrics.append(f"""<div class="metric">
            <span class="metric-label">Disk Free</span>
            <span class="metric-value">{g.disk_gb:.1f} GB</span>
        </div>""")
    
    if data.machine == "nucbox":
        lanes_pct = (g.lanes_active / g.lanes_max) * 100
        lanes_class = "ok" if lanes_pct < 70 else ("warning" if lanes_pct < 90 else "error")
        metrics.append(f"""<div class="metric">
            <span class="metric-label">Lanes</span>
            <span class="metric-value">{g.lanes_active} / {g.lanes_max}</span>
        </div>
        <div class="progress-bar"><div class="progress-fill progress-{lanes_class}" style="width:{lanes_pct:.0f}%"></div></div>
        """)
    
    status = "All guards passed" if g.guards_passed else f"⚠️ {g.reason}"
    status_class = "success" if g.guards_passed else "error"
    
    return f"""
        <div class="card">
            <div class="card-title">Guards <span class="badge badge-{'pass' if g.guards_passed else 'fail'}">{status}</span></div>
            {''.join(metrics)}
        </div>
    """


def _render_merges(data: DashboardData) -> str:
    """Render merges card."""
    hour_pct = (data.merges_this_hour / data.merge_cap_hour) * 100
    day_pct = (data.merges_today / data.merge_cap_day) * 100
    
    hour_class = "ok" if hour_pct < 70 else ("warning" if hour_pct < 100 else "error")
    day_class = "ok" if day_pct < 70 else ("warning" if day_pct < 100 else "error")
    
    recent = ""
    if data.merges_today_list:
        items = []
        for m in data.merges_today_list[:5]:
            items.append(f"<li>{_escape(m.repo)} #{m.pr_number}</li>")
        recent = f"<ul style='margin-top:0.5rem;padding-left:1.5rem;font-size:0.8rem'>{''.join(items)}</ul>"
    
    return f"""
        <div class="card">
            <div class="card-title">Merges Today</div>
            <div class="metric">
                <span class="metric-label">This Hour</span>
                <span class="metric-value">{data.merges_this_hour} / {data.merge_cap_hour}</span>
            </div>
            <div class="progress-bar"><div class="progress-fill progress-{hour_class}" style="width:{hour_pct:.0f}%"></div></div>
            <div class="metric">
                <span class="metric-label">Today</span>
                <span class="metric-value">{data.merges_today} / {data.merge_cap_day}</span>
            </div>
            <div class="progress-bar"><div class="progress-fill progress-{day_class}" style="width:{day_pct:.0f}%"></div></div>
            {recent}
        </div>
    """


def _render_providers(data: DashboardData) -> str:
    """Render providers budget card."""
    rows = []
    for p in data.providers:
        pct = min(100, (p.weekly_pct / max(p.cap_pct, 1)) * 100) if p.cap_pct > 0 else 0
        pct_class = "ok" if p.status == "ok" else p.status
        
        rows.append(f"""
            <tr>
                <td>{_escape(p.name)}</td>
                <td>{p.weekly_pct:.1f}%</td>
                <td>${p.weekly_spend_usd:.2f}</td>
                <td><span class="badge badge-{p.status}">{p.status}</span></td>
            </tr>
        """)
    
    return f"""
        <div class="card">
            <div class="card-title">Provider Budgets</div>
            <table>
                <tr><th>Provider</th><th>Weekly %</th><th>Spend</th><th>Status</th></tr>
                {''.join(rows)}
            </table>
        </div>
    """


def _render_scorecard(data: DashboardData) -> str:
    """Render scorecard metrics card."""
    if not data.scorecard:
        return """
            <div class="card">
                <div class="card-title">Scorecard</div>
                <div class="empty-state">No metrics data</div>
            </div>
        """
    
    s = data.scorecard
    wow = s.week_over_week
    
    def delta(curr: float, prev: float, higher_better: bool = True) -> str:
        if prev == 0:
            return ""
        diff = curr - prev
        if abs(diff) < 0.1:
            return ""
        direction = "up" if (diff > 0) == higher_better else "down"
        sign = "+" if diff > 0 else ""
        return f'<span class="metric-delta delta-{direction}">{sign}{diff:.1f}%</span>'
    
    return f"""
        <div class="card">
            <div class="card-title">Scorecard Metrics</div>
            <div class="metric">
                <span class="metric-label">First-Try Pass Rate</span>
                <span class="metric-value">{s.first_try_pass_rate:.1f}%{delta(s.first_try_pass_rate, wow.get('first_try_pass_rate', 0))}</span>
            </div>
            <div class="metric">
                <span class="metric-label">Pre-LLM Catch Rate</span>
                <span class="metric-value">{s.pre_llm_catch_rate:.1f}%</span>
            </div>
            <div class="metric">
                <span class="metric-label">Fix Loop Success</span>
                <span class="metric-value">{s.fix_loop_success_rate:.1f}%{delta(s.fix_loop_success_rate, wow.get('fix_loop_success_rate', 0))}</span>
            </div>
            <div class="metric">
                <span class="metric-label">Time to Merge</span>
                <span class="metric-value">{s.median_time_to_merge_hours:.1f}h{delta(s.median_time_to_merge_hours, wow.get('median_time_to_merge_hours', 0), False)}</span>
            </div>
            <div class="metric">
                <span class="metric-label">Total Reviews</span>
                <span class="metric-value">{s.total_reviews}</span>
            </div>
            <div class="metric">
                <span class="metric-label">Stuck PR Rate</span>
                <span class="metric-value">{s.stuck_pr_rate:.1f}%</span>
            </div>
        </div>
    """


def _render_backlog(data: DashboardData) -> str:
    """Render backlog table card."""
    if not data.backlog:
        return """
            <div class="card card-full">
                <div class="card-title">Backlog</div>
                <div class="empty-state">No PRs in backlog</div>
            </div>
        """
    
    rows = []
    for pr in data.backlog[:15]:
        state_class = {
            "draft": "pending",
            "needs-karen": "pending", 
            "karen-pass": "pass",
            "karen-fail": "fail",
        }.get(pr.state, "pending")
        
        ci_class = {
            "passing": "pass",
            "failing": "fail",
            "pending": "pending",
        }.get(pr.ci_status, "pending")
        
        rows.append(f"""
            <tr>
                <td>{_escape(pr.repo)}</td>
                <td><a href="{_escape(pr.url)}" target="_blank">#{pr.pr_number}</a></td>
                <td class="truncate" title="{_escape(pr.title)}">{_escape(pr.title)}</td>
                <td><span class="badge badge-{state_class}">{_escape(pr.state)}</span></td>
                <td><span class="badge badge-{ci_class}">{_escape(pr.ci_status)}</span></td>
                <td>{_escape(pr.head_sha)}</td>
            </tr>
        """)
    
    return f"""
        <div class="card card-full">
            <div class="card-title">Backlog <span style="font-weight:normal;color:var(--text-muted)">({len(data.backlog)} PRs)</span></div>
            <table>
                <tr><th>Repo</th><th>PR</th><th>Title</th><th>State</th><th>CI</th><th>SHA</th></tr>
                {''.join(rows)}
            </table>
        </div>
    """


def _render_verdicts(data: DashboardData) -> str:
    """Render verdicts card."""
    if not data.verdicts:
        return """
            <div class="card">
                <div class="card-title">Recent Verdicts</div>
                <div class="empty-state">No verdicts</div>
            </div>
        """
    
    rows = []
    for v in data.verdicts[:10]:
        verdict_class = "pass" if v.verdict == "PASS" else "fail"
        rows.append(f"""
            <tr>
                <td>{_escape(v.repo)} #{v.pr_number}</td>
                <td><span class="badge badge-{verdict_class}">{v.verdict}</span></td>
                <td class="truncate" title="{_escape(v.reason)}">{_escape(v.reason)}</td>
                <td>{_escape(v.reviewer)}</td>
            </tr>
        """)
    
    return f"""
        <div class="card card-full">
            <div class="card-title">Recent Karen Verdicts</div>
            <table>
                <tr><th>PR</th><th>Verdict</th><th>Reason</th><th>Reviewer</th></tr>
                {''.join(rows)}
            </table>
        </div>
    """


def _render_fix_attempts(data: DashboardData) -> str:
    """Render fix attempts card."""
    if not data.fix_attempts:
        return """
            <div class="card">
                <div class="card-title">Fix Loop Attempts</div>
                <div class="empty-state">No fix attempts</div>
            </div>
        """
    
    rows = []
    for a in data.fix_attempts[:10]:
        status_class = "pass" if a.success else "fail"
        status_text = "success" if a.success else "failed"
        rows.append(f"""
            <tr>
                <td>{_escape(a.repo)} #{a.pr_number}</td>
                <td><span class="badge badge-{status_class}">{status_text}</span></td>
                <td>{_escape(a.tool)}</td>
                <td class="truncate" title="{_escape(a.error)}">{_escape(a.error) or '-'}</td>
            </tr>
        """)
    
    return f"""
        <div class="card">
            <div class="card-title">Fix Loop Attempts</div>
            <table>
                <tr><th>PR</th><th>Status</th><th>Tool</th><th>Error</th></tr>
                {''.join(rows)}
            </table>
        </div>
    """


def _render_watchdog(data: DashboardData) -> str:
    """Render watchdog events card."""
    if not data.watchdog_events:
        return """
            <div class="card">
                <div class="card-title">Watchdog Events</div>
                <div class="empty-state">No watchdog events</div>
            </div>
        """
    
    rows = []
    for e in data.watchdog_events[:10]:
        status = "pass" if e.passed else ("pass" if e.repaired else "fail")
        status_text = "✓" if e.passed else ("repaired" if e.repaired else "failed")
        rows.append(f"""
            <tr>
                <td>{_escape(e.check_name)}</td>
                <td><span class="badge badge-{status}">{status_text}</span></td>
                <td class="truncate" title="{_escape(e.message)}">{_escape(e.message)}</td>
            </tr>
        """)
    
    return f"""
        <div class="card">
            <div class="card-title">Watchdog Events</div>
            <table>
                <tr><th>Check</th><th>Status</th><th>Message</th></tr>
                {''.join(rows)}
            </table>
        </div>
    """


def _render_alerts(data: DashboardData) -> str:
    """Render NEEDS_JEFF alerts."""
    if not data.needs_jeff:
        return ""
    
    items = []
    for item in data.needs_jeff[:3]:
        items.append(f"""
            <div class="alert">
                <div class="alert-title">⚠️ NEEDS JEFF: {_escape(item.timestamp)}</div>
                <div>{_escape(item.issue)}</div>
                <div style="font-size:0.875rem;color:var(--text-muted);margin-top:0.25rem">{_escape(item.details)}</div>
            </div>
        """)
    
    return "".join(items)


def render_dashboard(data: DashboardData) -> str:
    """Render complete dashboard HTML."""
    return HTML_TEMPLATE.format(
        machine=data.machine.upper(),
        mode="LIVE" if data.live_mode else "SHADOW",
        mode_class="live" if data.live_mode else "shadow",
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        alerts=_render_alerts(data),
        services_card=_render_services(data),
        guards_card=_render_guards(data),
        merges_card=_render_merges(data),
        providers_card=_render_providers(data),
        scorecard_card=_render_scorecard(data),
        backlog_card=_render_backlog(data),
        verdicts_card=_render_verdicts(data),
        fix_attempts_card=_render_fix_attempts(data),
        watchdog_card=_render_watchdog(data),
    ).replace("    <div class=\"grid\">", Path(__file__).with_name("engine.html").read_text() + "    <div class=\"grid\">", 1)


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for dashboard."""
    
    collector: Optional[DataCollector] = None
    
    def log_message(self, format: str, *args) -> None:
        """Suppress default logging."""
        pass
    
    def do_GET(self) -> None:
        """Handle GET requests."""
        parsed = urlparse(self.path)
        
        if parsed.path == "/" or parsed.path == "/index.html":
            self._serve_dashboard()
        elif parsed.path == "/api/engine":
            self._serve_engine()
        elif parsed.path == "/api/data":
            self._serve_api_data()
        elif parsed.path == "/health":
            self._serve_health()
        else:
            self._serve_404()
    
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
