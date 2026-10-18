import json
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer
from threading import Thread
from urllib.request import urlopen

from cluster.control_plane.dashboard.tachometer import EngineCollector
from cluster.control_plane.dashboard.server import DashboardHandler, render_dashboard
from cluster.control_plane.dashboard.data_collector import DataCollector, DashboardData


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_counts_verified_jobs_verdicts_and_stale_remote(tmp_path):
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()
    data = tmp_path / 'data'
    karen = tmp_path / 'karen'
    ledger = data / 'jobs' / 'ledger.jsonl'
    ledger.parent.mkdir(parents=True)
    jobs = [dict(job_id='one', state='queued'),
            dict(job_id='one', state='done', outcome_verified=True, end_time=timestamp),
            dict(job_id='two', state='failed', end_time=timestamp),
            dict(job_id='three', state='running', lease_expires=(now + timedelta(minutes=5)).isoformat()),
            dict(job_id='expired', state='running', lease_expires=(now - timedelta(minutes=5)).isoformat())]
    ledger.write_text('\n'.join(json.dumps(j) for j in jobs)+'\ninvalid partial')
    verdict = karen / 'verdicts' / 'owner_repo_1_sha_PASS.json'
    write(verdict, dict(repo='repo', pr_number=1, timestamp=timestamp, passed=True))
    verdict.with_name('LATEST.json').symlink_to(verdict.name)
    write(data / 'conductor' / 'merge-state.json', {'merges': [{'ts': now.timestamp()}]})
    write(data / 'backlog.json', {'prs': [{}, {}]})
    write(data / 'watchdog.log', {'timestamp': timestamp, 'checks': [{'passed': True}]})
    write(data / 'machines' / 'mini' / 'watchdog.log', {'timestamp': (now - timedelta(hours=2)).isoformat(), 'checks': [{'passed': True}]})
    (data / 'MONITOR.md').write_text('Earlier\n<unsafe> monitor status\n')
    engine = EngineCollector(data, karen, 'nucbox')
    result = engine.collect()
    assert [g['value'] for g in result['gauges']] == [1, 1, 1, 100.0, 1, 50.0, 2]
    assert result['machines']['mini']['state'] == 'yellow'
    assert result['machines']['nucbox']['state'] == 'green'
    assert result['engine'] == 'yellow'
    assert result['monitor'] == '<unsafe> monitor status'
    assert engine.collect() is result


def test_unknown_values_and_failed_health(tmp_path):
    engine = EngineCollector(tmp_path, tmp_path, 'mini')
    result = engine.collect()
    assert result['gauges'][3]['value'] is None
    assert result['gauges'][6]['value'] is None
    assert result['gauges'][0]['max'] is None
    write(tmp_path / 'watchdog.log', {'timestamp': datetime.now(timezone.utc).isoformat(), 'checks': [{'passed': False}]})
    engine._at = 0
    assert engine.collect()['engine'] == 'red'


def test_engine_endpoint_is_read_only_and_lightweight(tmp_path):
    class Handler(DashboardHandler):
        collector = DataCollector(data_dir=tmp_path, karen_dir=tmp_path)
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen('http://127.0.0.1:%s/api/engine' % server.server_port) as response:
            assert response.headers['Cache-Control'] == 'no-store'
            assert 'gauges' in json.load(response)
        assert list(tmp_path.iterdir()) == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_live_panel_uses_svg_and_safe_text():
    page = render_dashboard(DashboardData(timestamp='', machine='mini', live_mode=True))
    assert '/api/engine' in page
    assert '<svg' in page
    assert 'setTimeout(refresh,3000)' in page
    assert 'textContent=data.monitor' in page
    assert 'http-equiv="refresh"' not in page
    assert 'https://cdn' not in page
