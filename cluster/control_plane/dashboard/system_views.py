"""Dashboard feature implementation using the caller’s live namespace."""
from __future__ import annotations

def _escape(_runtime_bindings, s: str) -> str:
    """Escape HTML special characters."""
    return _runtime_bindings['html'].escape(str(s))

def _render_services(_runtime_bindings, data: DashboardData) -> str:
    """Render services card."""
    rows = []
    for svc in data.services:
        status_class = f'status-{svc.status}'
        rows.append(f"""\n            <tr>\n                <td><span class="status {status_class}"></span>{_runtime_bindings['_escape'](svc.name)}</td>\n                <td>{_runtime_bindings['_escape'](svc.last_run_ago)}</td>\n                <td>{svc.error_count} errors</td>\n            </tr>\n        """)
    return f"""\n        <div class="card">\n            <div class="card-title">Services ({data.machine})</div>\n            <table>\n                <tr><th>Service</th><th>Last Run</th><th>Status</th></tr>\n                {(''.join(rows) if rows else '<tr><td colspan="3" class="empty-state">No services found</td></tr>')}\n            </table>\n        </div>\n    """

def _render_guards(_runtime_bindings, data: DashboardData) -> str:
    """Render guards card."""
    if not data.guards:
        return ''
    g = data.guards
    load_pct = min(100, g.load / g.load_limit * 100)
    load_class = 'ok' if load_pct < 70 else 'warning' if load_pct < 90 else 'error'
    metrics = [f'<div class="metric">\n            <span class="metric-label">Load</span>\n            <span class="metric-value">{g.load:.1f} / {g.load_limit:.0f}</span>\n        </div>\n        <div class="progress-bar"><div class="progress-fill progress-{load_class}" style="width:{load_pct:.0f}%"></div></div>\n        ']
    if data.machine == 'mini':
        swap_class = 'ok' if g.swap_pct < 60 else 'warning' if g.swap_pct < g.swap_limit else 'error'
        metrics.append(f'<div class="metric">\n            <span class="metric-label">Swap</span>\n            <span class="metric-value">{g.swap_pct:.1f}%</span>\n        </div>\n        <div class="progress-bar"><div class="progress-fill progress-{swap_class}" style="width:{g.swap_pct:.0f}%"></div></div>\n        ')
        disk_pct = min(100, max(0, 100 - g.disk_gb / 50 * 100))
        disk_class = 'ok' if g.disk_gb > 20 else 'warning' if g.disk_gb > 15 else 'error'
        metrics.append(f'<div class="metric">\n            <span class="metric-label">Disk Free</span>\n            <span class="metric-value">{g.disk_gb:.1f} GB</span>\n        </div>')
    if data.machine == 'nucbox':
        lanes_pct = g.lanes_active / g.lanes_max * 100
        lanes_class = 'ok' if lanes_pct < 70 else 'warning' if lanes_pct < 90 else 'error'
        metrics.append(f'<div class="metric">\n            <span class="metric-label">Lanes</span>\n            <span class="metric-value">{g.lanes_active} / {g.lanes_max}</span>\n        </div>\n        <div class="progress-bar"><div class="progress-fill progress-{lanes_class}" style="width:{lanes_pct:.0f}%"></div></div>\n        ')
    status = 'All guards passed' if g.guards_passed else f'⚠️ {g.reason}'
    status_class = 'success' if g.guards_passed else 'error'
    return f"""\n        <div class="card">\n            <div class="card-title">Guards <span class="badge badge-{('pass' if g.guards_passed else 'fail')}">{status}</span></div>\n            {''.join(metrics)}\n        </div>\n    """

def _render_providers(_runtime_bindings, data: DashboardData) -> str:
    """Render providers budget card."""
    rows = []
    for p in data.providers:
        quota = f'{p.weekly_pct:.1f}%' if p.weekly_pct is not None else 'unknown'
        spend = f'${p.weekly_spend_usd:.2f}' if p.weekly_spend_usd is not None else 'unknown'
        rows.append(f"""\n            <tr>\n                <td>{_runtime_bindings['_escape'](p.name)}</td>\n                <td>{quota}</td>\n                <td>{spend}</td>\n                <td><span class="badge badge-{p.status}">{p.status}</span></td>\n            </tr>\n        """)
    return f"""\n        <div class="card">\n            <div class="card-title">Provider Budgets</div>\n            <table>\n                <tr><th>Provider</th><th>Weekly %</th><th>Spend</th><th>Status</th></tr>\n                {''.join(rows)}\n            </table>\n        </div>\n    """

def _render_scorecard(_runtime_bindings, data: DashboardData) -> str:
    """Render scorecard metrics card."""
    if not data.scorecard:
        return '\n            <div class="card">\n                <div class="card-title">Scorecard</div>\n                <div class="empty-state">No metrics data</div>\n            </div>\n        '
    s = data.scorecard
    wow = s.week_over_week

    def delta(curr: float, prev: float, higher_better: bool=True) -> str:
        if prev == 0:
            return ''
        diff = curr - prev
        if abs(diff) < 0.1:
            return ''
        direction = 'up' if (diff > 0) == higher_better else 'down'
        sign = '+' if diff > 0 else ''
        return f'<span class="metric-delta delta-{direction}">{sign}{diff:.1f}%</span>'
    return f"""\n        <div class="card">\n            <div class="card-title">Scorecard Metrics</div>\n            <div class="metric">\n                <span class="metric-label">First-Try Pass Rate</span>\n                <span class="metric-value">{s.first_try_pass_rate:.1f}%{delta(s.first_try_pass_rate, wow.get('first_try_pass_rate', 0))}</span>\n            </div>\n            <div class="metric">\n                <span class="metric-label">Pre-LLM Catch Rate</span>\n                <span class="metric-value">{s.pre_llm_catch_rate:.1f}%</span>\n            </div>\n            <div class="metric">\n                <span class="metric-label">Fix Loop Success</span>\n                <span class="metric-value">{s.fix_loop_success_rate:.1f}%{delta(s.fix_loop_success_rate, wow.get('fix_loop_success_rate', 0))}</span>\n            </div>\n            <div class="metric">\n                <span class="metric-label">Time to Merge</span>\n                <span class="metric-value">{s.median_time_to_merge_hours:.1f}h{delta(s.median_time_to_merge_hours, wow.get('median_time_to_merge_hours', 0), False)}</span>\n            </div>\n            <div class="metric">\n                <span class="metric-label">Total Reviews</span>\n                <span class="metric-value">{s.total_reviews}</span>\n            </div>\n            <div class="metric">\n                <span class="metric-label">Stuck PR Rate</span>\n                <span class="metric-value">{s.stuck_pr_rate:.1f}%</span>\n            </div>\n        </div>\n    """

def _render_alerts(_runtime_bindings, data: DashboardData) -> str:
    """Render NEEDS_JEFF alerts."""
    if not data.needs_jeff:
        return ''
    items = []
    for item in data.needs_jeff[:3]:
        items.append(f"""\n            <div class="alert">\n                <div class="alert-title">⚠️ NEEDS JEFF: {_runtime_bindings['_escape'](item.timestamp)}</div>\n                <div>{_runtime_bindings['_escape'](item.issue)}</div>\n                <div style="font-size:0.875rem;color:var(--text-muted);margin-top:0.25rem">{_runtime_bindings['_escape'](item.details)}</div>\n            </div>\n        """)
    return ''.join(items)

def render_dashboard(_runtime_bindings, data: DashboardData) -> str:
    """Render complete dashboard HTML."""
    return _runtime_bindings['HTML_TEMPLATE'].format(machine=data.machine.upper(), mode='LIVE' if data.live_mode else 'SHADOW', mode_class='live' if data.live_mode else 'shadow', timestamp=_runtime_bindings['datetime'].now(_runtime_bindings['timezone'].utc).strftime('%Y-%m-%d %H:%M:%S UTC'), alerts=_runtime_bindings['_render_alerts'](data), services_card=_runtime_bindings['_render_services'](data), guards_card=_runtime_bindings['_render_guards'](data), merges_card=_runtime_bindings['_render_merges'](data), providers_card=_runtime_bindings['_render_providers'](data), scorecard_card=_runtime_bindings['_render_scorecard'](data), backlog_card=_runtime_bindings['_render_backlog'](data), verdicts_card=_runtime_bindings['_render_verdicts'](data), fix_attempts_card=_runtime_bindings['_render_fix_attempts'](data), watchdog_card=_runtime_bindings['_render_watchdog'](data)).replace('    <div class="grid">', _runtime_bindings['Path'](__file__).with_name('engine.html').read_text() + '    <div class="grid">', 1)
