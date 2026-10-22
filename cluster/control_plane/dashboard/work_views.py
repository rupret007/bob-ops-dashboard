"""Dashboard feature implementation using the caller’s live namespace."""
from __future__ import annotations

def _render_merges(_runtime_bindings, data: DashboardData) -> str:
    """Render merges card."""
    hour_pct = data.merges_this_hour / data.merge_cap_hour * 100
    day_pct = data.merges_today / data.merge_cap_day * 100
    hour_class = 'ok' if hour_pct < 70 else 'warning' if hour_pct < 100 else 'error'
    day_class = 'ok' if day_pct < 70 else 'warning' if day_pct < 100 else 'error'
    recent = ''
    if data.merges_today_list:
        items = []
        for m in data.merges_today_list[:5]:
            items.append(f"<li>{_runtime_bindings['_escape'](m.repo)} #{m.pr_number}</li>")
        recent = f"<ul style='margin-top:0.5rem;padding-left:1.5rem;font-size:0.8rem'>{''.join(items)}</ul>"
    return f'\n        <div class="card">\n            <div class="card-title">Merges Today</div>\n            <div class="metric">\n                <span class="metric-label">This Hour</span>\n                <span class="metric-value">{data.merges_this_hour} / {data.merge_cap_hour}</span>\n            </div>\n            <div class="progress-bar"><div class="progress-fill progress-{hour_class}" style="width:{hour_pct:.0f}%"></div></div>\n            <div class="metric">\n                <span class="metric-label">Today</span>\n                <span class="metric-value">{data.merges_today} / {data.merge_cap_day}</span>\n            </div>\n            <div class="progress-bar"><div class="progress-fill progress-{day_class}" style="width:{day_pct:.0f}%"></div></div>\n            {recent}\n        </div>\n    '

def _render_backlog(_runtime_bindings, data: DashboardData) -> str:
    """Render backlog table card."""
    if not data.backlog:
        return '\n            <div class="card card-full">\n                <div class="card-title">Backlog</div>\n                <div class="empty-state">No PRs in backlog</div>\n            </div>\n        '
    rows = []
    for pr in data.backlog[:15]:
        state_class = {'draft': 'pending', 'needs-karen': 'pending', 'karen-pass': 'pass', 'karen-fail': 'fail'}.get(pr.state, 'pending')
        ci_class = {'passing': 'pass', 'failing': 'fail', 'pending': 'pending'}.get(pr.ci_status, 'pending')
        rows.append(f'''\n            <tr>\n                <td>{_runtime_bindings['_escape'](pr.repo)}</td>\n                <td><a href="{_runtime_bindings['_escape'](pr.url)}" target="_blank">#{pr.pr_number}</a></td>\n                <td class="truncate" title="{_runtime_bindings['_escape'](pr.title)}">{_runtime_bindings['_escape'](pr.title)}</td>\n                <td><span class="badge badge-{state_class}">{_runtime_bindings['_escape'](pr.state)}</span></td>\n                <td><span class="badge badge-{ci_class}">{_runtime_bindings['_escape'](pr.ci_status)}</span></td>\n                <td>{_runtime_bindings['_escape'](pr.head_sha)}</td>\n            </tr>\n        ''')
    return f"""\n        <div class="card card-full">\n            <div class="card-title">Backlog <span style="font-weight:normal;color:var(--text-muted)">({len(data.backlog)} PRs)</span></div>\n            <table>\n                <tr><th>Repo</th><th>PR</th><th>Title</th><th>State</th><th>CI</th><th>SHA</th></tr>\n                {''.join(rows)}\n            </table>\n        </div>\n    """

def _render_verdicts(_runtime_bindings, data: DashboardData) -> str:
    """Render verdicts card."""
    if not data.verdicts:
        return '\n            <div class="card">\n                <div class="card-title">Recent Verdicts</div>\n                <div class="empty-state">No verdicts</div>\n            </div>\n        '
    rows = []
    for v in data.verdicts[:10]:
        verdict_class = 'pass' if v.verdict == 'PASS' else 'fail'
        rows.append(f'''\n            <tr>\n                <td>{_runtime_bindings['_escape'](v.repo)} #{v.pr_number}</td>\n                <td><span class="badge badge-{verdict_class}">{v.verdict}</span></td>\n                <td class="truncate" title="{_runtime_bindings['_escape'](v.reason)}">{_runtime_bindings['_escape'](v.reason)}</td>\n                <td>{_runtime_bindings['_escape'](v.reviewer)}</td>\n            </tr>\n        ''')
    return f"""\n        <div class="card card-full">\n            <div class="card-title">Recent Karen Verdicts</div>\n            <table>\n                <tr><th>PR</th><th>Verdict</th><th>Reason</th><th>Reviewer</th></tr>\n                {''.join(rows)}\n            </table>\n        </div>\n    """

def _render_fix_attempts(_runtime_bindings, data: DashboardData) -> str:
    """Render fix attempts card."""
    if not data.fix_attempts:
        return '\n            <div class="card">\n                <div class="card-title">Fix Loop Attempts</div>\n                <div class="empty-state">No fix attempts</div>\n            </div>\n        '
    rows = []
    for a in data.fix_attempts[:10]:
        status_class = 'pass' if a.success else 'fail'
        status_text = 'success' if a.success else 'failed'
        rows.append(f'''\n            <tr>\n                <td>{_runtime_bindings['_escape'](a.repo)} #{a.pr_number}</td>\n                <td><span class="badge badge-{status_class}">{status_text}</span></td>\n                <td>{_runtime_bindings['_escape'](a.tool)}</td>\n                <td class="truncate" title="{_runtime_bindings['_escape'](a.error)}">{_runtime_bindings['_escape'](a.error) or '-'}</td>\n            </tr>\n        ''')
    return f"""\n        <div class="card">\n            <div class="card-title">Fix Loop Attempts</div>\n            <table>\n                <tr><th>PR</th><th>Status</th><th>Tool</th><th>Error</th></tr>\n                {''.join(rows)}\n            </table>\n        </div>\n    """

def _render_watchdog(_runtime_bindings, data: DashboardData) -> str:
    """Render watchdog events card."""
    if not data.watchdog_events:
        return '\n            <div class="card">\n                <div class="card-title">Watchdog Events</div>\n                <div class="empty-state">No watchdog events</div>\n            </div>\n        '
    rows = []
    for e in data.watchdog_events[:10]:
        status = 'pass' if e.passed else 'pass' if e.repaired else 'fail'
        status_text = '✓' if e.passed else 'repaired' if e.repaired else 'failed'
        rows.append(f'''\n            <tr>\n                <td>{_runtime_bindings['_escape'](e.check_name)}</td>\n                <td><span class="badge badge-{status}">{status_text}</span></td>\n                <td class="truncate" title="{_runtime_bindings['_escape'](e.message)}">{_runtime_bindings['_escape'](e.message)}</td>\n            </tr>\n        ''')
    return f"""\n        <div class="card">\n            <div class="card-title">Watchdog Events</div>\n            <table>\n                <tr><th>Check</th><th>Status</th><th>Message</th></tr>\n                {''.join(rows)}\n            </table>\n        </div>\n    """
