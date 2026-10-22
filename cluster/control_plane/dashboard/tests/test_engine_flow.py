"""A healthy process is insufficient evidence that work is flowing."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from cluster.control_plane.dashboard.tachometer import EngineCollector


def snapshot(tmp_path, *, review_age=1, job_age=1, passed=True, verified=True,
             head='a' * 40, status='COMPLETE', alert=None, peer=None, failed=False):
    now = datetime.now(timezone.utc)
    def write(name, value):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    for name in ('watchdog.log', 'machines/mini/watchdog.log'):
        write(name, dict(timestamp=now.isoformat(), checks=[dict(passed=not failed)]))
    if review_age is not None:
        write('verdicts/review.json', dict(timestamp=(now-timedelta(seconds=review_age)).isoformat(),
              owner='owner', repo='repo', pr_number=1, head_sha=head, passed=passed, status=status))
    if job_age is not None:
        write('jobs/ledger.jsonl', dict(job_id='job', state='done', outcome_verified=verified,
              end_time=(now-timedelta(seconds=job_age)).isoformat(), machine='nucbox'))
    write('machines/mini/engine.json', dict(synced_at=now.isoformat(), machine='mini',
          peer_timestamp=now.isoformat(), flow_local=peer or dict(machine='mini',
          last_verdict_at=None, last_success_at=None, stale_alerts=0)))
    if alert:
        path = tmp_path / 'conductor/NEEDS_JEFF.md'
        path.parent.mkdir(exist_ok=True)
        path.write_text(alert)
    return EngineCollector(tmp_path, tmp_path, 'nucbox').collect()


@pytest.mark.parametrize('changes,reason', [
    ({'review_age': None}, 'No completed Karen verdict in the last 60 minutes'),
    ({'review_age': 3601}, 'No completed Karen verdict in the last 60 minutes'),
    ({'review_age': -60}, 'No completed Karen verdict in the last 60 minutes'),
    ({'status': 'ERROR'}, 'No completed Karen verdict in the last 60 minutes'),
    ({'head': 'unknown'}, 'No completed Karen verdict in the last 60 minutes'),
    ({'job_age': None}, 'No verified successful job in the last 60 minutes'),
    ({'job_age': 3601}, 'No verified successful job in the last 60 minutes'),
    ({'job_age': -60}, 'No verified successful job in the last 60 minutes'),
    ({'verified': False}, 'No verified successful job in the last 60 minutes'),
    ({'verified': 'False'}, 'No verified successful job in the last 60 minutes'),
])
def test_no_false_green(tmp_path, changes, reason):
    data = snapshot(tmp_path, **changes)
    assert data['engine'] == 'yellow'
    assert reason in data['engine_reason']
    assert data['machines']['nucbox']['state'] == 'green'


@pytest.mark.parametrize('passed', [True, False])
def test_flow_requires_a_verdict_not_a_pass(tmp_path, passed):
    data = snapshot(tmp_path, passed=passed, review_age=3599, job_age=3599)
    assert data['engine'] == 'green'
    assert 'Work is flowing' in data['engine_reason']


@pytest.mark.parametrize('prefix', ['## ', '- ', ''])
def test_stale_alert_prevents_green_without_exposing_text(tmp_path, prefix):
    old = datetime.now(timezone.utc) - timedelta(minutes=61)
    alert = prefix + old.isoformat() + ' — private issue\n\n**Issue:** private details\n'
    data = snapshot(tmp_path, alert=alert)
    assert data['engine'] == 'yellow'
    assert 'stale NEEDS_JEFF' in data['engine_reason']
    assert 'private' not in data['engine_reason']


def test_watchdog_utc_alert_and_archive(tmp_path):
    old = datetime.now(timezone.utc) - timedelta(minutes=61)
    alert = '## ' + old.strftime('%Y-%m-%d %H:%M UTC') + ' - Watchdog\n**Issue:** check\n'
    assert snapshot(tmp_path, alert=alert)['engine'] == 'yellow'
    (tmp_path / 'conductor/NEEDS_JEFF.md').rename(tmp_path / 'conductor/NEEDS_JEFF.resolved.md')
    assert snapshot(tmp_path)['engine'] == 'green'


def test_fresh_peer_flow_counts_but_never_echoes(tmp_path):
    now = datetime.now(timezone.utc).timestamp()
    peer = dict(machine='mini', last_verdict_at=now, last_success_at=now, stale_alerts=0)
    data = snapshot(tmp_path, review_age=None, job_age=None, peer=peer)
    assert data['engine'] == 'green'
    assert data['flow_local']['last_success_at'] is None
    assert data['flow_local']['last_verdict_at'] is None
    peer['machine'] = 'nucbox'
    assert snapshot(tmp_path, review_age=None, job_age=None, peer=peer)['engine'] == 'yellow'


def test_red_service_failure_is_preserved(tmp_path):
    assert snapshot(tmp_path, failed=True)['engine'] == 'red'


@pytest.mark.parametrize('content,expected', [
    ('## 2026-00-99 00:00 UTC\n**Issue:** check\n', 'yellow'),
    ('2020-01-01T00:00:00Z — Resolved: recovered\n', 'green'),
])
def test_alert_dates_fail_closed_and_explicit_resolution_is_respected(tmp_path, content, expected):
    assert snapshot(tmp_path, alert=content)['engine'] == expected


def test_stale_peer_cannot_supply_flow(tmp_path):
    now = datetime.now(timezone.utc)
    peer = dict(machine='mini', last_verdict_at=now.timestamp(),
                last_success_at=now.timestamp(), stale_alerts=0)
    snapshot(tmp_path, review_age=None, job_age=None, peer=peer)
    path = tmp_path / 'machines/mini/engine.json'
    value = json.loads(path.read_text())
    value['peer_timestamp'] = (now-timedelta(minutes=16)).isoformat()
    path.write_text(json.dumps(value))
    data = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()
    assert data['engine'] == 'yellow'
    assert 'mini work-flow evidence unavailable' in data['engine_reason']


def test_truncated_alert_file_cannot_prove_green(tmp_path):
    snapshot(tmp_path)
    (tmp_path / 'NEEDS_JEFF.md').write_text(' ' * 262145)
    assert EngineCollector(tmp_path, tmp_path, 'nucbox').collect()['engine'] == 'yellow'


def test_root_alert_with_date_at_end_of_heading_is_not_missed(tmp_path):
    snapshot(tmp_path)
    (tmp_path / 'NEEDS_JEFF.md').write_text('## Review needed — 2020-01-01T00:00:00Z\n')
    assert EngineCollector(tmp_path, tmp_path, 'nucbox').collect()['engine'] == 'yellow'


def test_peer_sync_copies_only_local_flow_fields(tmp_path, monkeypatch):
    from cluster.control_plane import watchdog
    monkeypatch.setattr(watchdog, 'LOG_DIR', tmp_path)
    flow = dict(machine='mini', last_verdict_at=1, last_success_at=2, stale_alerts=1)
    data = dict(timestamp='t', jobs={'mini': {'source': 'local'}},
                flow_local=dict(flow, private='private operator text'))
    assert watchdog.store_peer_engine('mini', data)
    value = json.loads((tmp_path / 'machines/mini/engine.json').read_text())
    assert value['flow_local'] == flow
    data['flow_local']['machine'] = 'nucbox'
    assert watchdog.store_peer_engine('mini', data)
    assert 'flow_local' not in json.loads((tmp_path / 'machines/mini/engine.json').read_text())


def test_reason_panel_uses_text_content():
    from cluster.control_plane.dashboard.server import render_dashboard
    from cluster.control_plane.dashboard.data_collector import DashboardData
    page = render_dashboard(DashboardData(timestamp='', machine='nucbox', live_mode=True))
    assert 'id="engine-reason" role="status"' in page
    assert '.textContent=data.engine_reason' in page
