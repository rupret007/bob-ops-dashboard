"""Machine health must not conceal a blocked work admission path."""
import json
from datetime import datetime, timedelta, timezone

from cluster.control_plane.dashboard.tachometer import EngineCollector
from cluster.control_plane.dashboard.server import render_dashboard
from cluster.control_plane.dashboard.data_collector import DashboardData


def snapshot(tmp_path, decision, minutes=0):
    now = datetime.now(timezone.utc)
    for name in ['watchdog.log', 'machines/mini/watchdog.log']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(timestamp=now.isoformat(), checks=[dict(passed=True)])))
    path = tmp_path / 'dispatch/dispatch-status.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(timestamp=(now-timedelta(minutes=minutes)).isoformat(),
                                    summary=dict(decisions=[decision]))))
    return EngineCollector(tmp_path, tmp_path, 'nucbox').collect()


def test_coordination_failure_turns_engine_yellow_without_changing_machine_health(tmp_path):
    data = snapshot(tmp_path, dict(action='dispatch_failed', reason='Coordination unavailable; new work paused'))
    assert data['engine'] == 'yellow'
    assert data['machines']['nucbox']['state'] == 'green'
    assert data['admission']['state'] == 'paused'
    assert 'shared coordination' in data['admission']['reason']


def test_next_success_clears_admission_warning(tmp_path):
    data = snapshot(tmp_path, dict(action='dispatch', reason='lane granted'))
    assert data['engine'] == 'green'
    assert data['admission']['state'] == 'ready'


def test_stale_admission_status_is_unknown(tmp_path):
    data = snapshot(tmp_path, dict(action='dispatch_failed', reason='Coordination unavailable'), minutes=16)
    assert data['admission']['state'] == 'unknown'


def test_status_panel_uses_safe_text():
    page = render_dashboard(DashboardData(timestamp='', machine='nucbox', live_mode=True))
    assert 'id="engine-admission"' in page
    assert "textContent=data.admission" in page
