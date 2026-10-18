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


def test_security_scan_stop_banner_deduplicates_and_follows_new_heads(tmp_path):
    from cluster.control_plane.local_karen.tests.test_verdict_push import security_verdict
    value = security_verdict().to_dict()
    now = datetime.now(timezone.utc)
    value['timestamp'] = (now - timedelta(minutes=1)).isoformat()
    folder = tmp_path / 'verdicts'
    write(folder / 'result.json', value)
    write(folder / 'latest.json', value)
    result = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()
    assert result['security_evidence']['count'] == 1
    assert 'before model review' in result['security_evidence']['reason']
    assert 'merge and automatic repair remain blocked' in result['security_evidence']['reason']
    assert 'config.py' not in result['security_evidence']['reason']
    changed = dict(value, head_sha='b' * 40, timestamp=now.isoformat(), passed=True)
    write(folder / 'new-head.json', changed)
    result = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()
    assert result['security_evidence']['count'] == 0
    assert 'No scanner-only' in result['security_evidence']['reason']


def test_security_scan_stop_banner_is_safe_text_and_handles_legacy_artifacts(tmp_path):
    write(tmp_path / 'verdicts' / 'legacy.json', {
        'timestamp': datetime.now(timezone.utc).isoformat(), 'passed': False,
        'reviewer': 'deterministic_security', 'reason': '<script>untrusted</script>'})
    result = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()
    assert result['security_evidence']['count'] == 0
    page = render_dashboard(DashboardData(timestamp='', machine='mini', live_mode=True))
    assert 'id="engine-security-evidence" role="status"' in page
    assert "getElementById('engine-security-evidence').textContent" in page
    assert '<script>untrusted</script>' not in page


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


def test_old_failures_keep_their_actual_time_after_ledger_rewrite(tmp_path):
    old = '2026-01-01T00:00:00+00:00'
    now = datetime.now(timezone.utc).isoformat()
    ledger = tmp_path / 'jobs' / 'ledger.jsonl'
    ledger.parent.mkdir()
    ledger.write_text(json.dumps(dict(job_id='old', state='failed', end_time=old,
                                     created_at=old, updated_at=now)) + '\n' +
                      json.dumps(dict(job_id='new', state='queued', created_at=now)) + '\n')
    snapshot = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()
    assert snapshot['activity'][0]['text'].endswith('queued')
    assert snapshot['activity'][1]['timestamp'] == old
    assert snapshot['gauges'][5]['value'] is None


def test_running_jobs_per_machine_and_llm_speedometers(tmp_path):
    now = datetime.now(timezone.utc)
    iso = lambda d: (now + d).isoformat()
    data, karen = tmp_path / 'data', tmp_path / 'karen'
    ledger = data / 'jobs' / 'ledger.jsonl'
    ledger.parent.mkdir(parents=True)
    jobs = [dict(job_id='a', repo='o/web', pr_number=7, provider='codex', model='gpt-5 low', machine='nucbox', state='running',
                 start_time=iso(timedelta(minutes=-3)), lease_expires=iso(timedelta(minutes=30)), pid=0),
            dict(job_id='b', repo='o/web', pr_number=8, provider='claude', machine='nucbox', state='done', outcome_verified=True,
                 start_time=iso(timedelta(minutes=-20)), end_time=iso(timedelta(minutes=-10))),
            dict(job_id='c', repo='o/x', pr_number=9, provider='codex', machine='mini', state='running',
                 start_time=iso(timedelta(minutes=-1)), lease_expires=iso(timedelta(minutes=30)))]
    ledger.write_text('\n'.join(json.dumps(j) for j in jobs))
    write(karen / 'verdicts' / 'v.json', dict(repo='o/web', pr_number=7, timestamp=iso(timedelta(minutes=-5)), passed=True,
                                              review_results=[{'reviewer': 'codex'}, {'reviewer': 'gemini-2.5'}]))
    write(karen / 'verdicts' / 'old.json', dict(repo='o/web', pr_number=6, timestamp=iso(timedelta(hours=-3)), passed=True,
                                                review_results=[{'reviewer': 'codex'}]))
    write(karen / 'codex_weekly_usage.json', {'week_start': now.timestamp() - 86400, 'usage_pct': 12.0})
    write(tmp_path / 'budget.json', {'usage': {'codex': {'calls': 19}, 'grok': {'calls': 0}}})
    peer = [{'service': 'codex', 'active': 1, 'calls_hour': 4, 'jobs_hour': 1, 'reviews_hour': 3, 'weekly_pct': 20.0}]
    write(data / 'machines' / 'mini' / 'engine.json', {'synced_at': now.isoformat(), 'llm_local': peer, 'jobs': {
        'source': 'local', 'count': 1, 'running': [dict(job_id='m', repo='o/m', pr_number=1, provider='gemini', model=None,
                                                        start_time=iso(timedelta(minutes=-2)), elapsed_sec=1)]}})
    write(data / 'machines' / 'lab' / 'engine.json', {'synced_at': iso(timedelta(hours=-2)), 'jobs': {'source': 'local', 'count': 3}})
    result = EngineCollector(data, karen, 'nucbox', budget_file=tmp_path / 'budget.json').collect()
    nuc = result['jobs']['nucbox']
    assert nuc['source'] == 'local' and nuc['count'] == 1 and nuc['capacity'] == 7 and nuc['reserved_codex'] == 2
    assert [(r['repo'], r['pr_number'], r['provider'], r['model']) for r in nuc['running']] == [('o/web', 7, 'codex', 'gpt-5 low')]
    assert 170 <= nuc['running'][0]['elapsed_sec'] <= 200
    mini = result['jobs']['mini']
    assert mini['source'] == 'peer-sync' and mini['count'] == 1 and mini['running'][0]['elapsed_sec'] >= 100
    assert result['jobs']['lab']['source'] == 'unavailable' and result['jobs']['lab']['count'] is None
    local = {g['service']: g for g in result['llm_local']}
    assert local['codex']['active'] == 1 and local['codex']['max_concurrent'] == 2
    assert (local['codex']['jobs_hour'], local['codex']['reviews_hour'], local['codex']['calls_hour']) == (1, 1, 2)
    assert local['gemini']['calls_hour'] == 1 and local['claude']['jobs_hour'] == 1
    assert local['codex']['weekly_pct'] == 12.0 and local['codex']['weekly_cap_pct'] == 75.0 and local['codex']['weekly_calls'] == 19
    assert local['grok']['conserve'] is True and local['codex']['conserve'] is False
    merged = {g['service']: g for g in result['llm']}
    assert [g['service'] for g in result['llm']] == ['codex', 'claude', 'gemini', 'minimax', 'cursor', 'grok']
    assert merged['codex']['active'] == 2 and merged['codex']['calls_hour'] == 6 and merged['codex']['weekly_pct'] == 20.0
    assert merged['codex']['machines'] == ['nucbox', 'mini']


def test_llm_gauges_report_no_data_without_evidence(tmp_path):
    result = EngineCollector(tmp_path, tmp_path, 'mini', budget_file=tmp_path / 'none.json').collect()
    assert all(g['calls_hour'] is None and g['active'] is None and g['weekly_pct'] is None for g in result['llm'])
    assert result['jobs']['mini']['count'] is None and result['jobs']['nucbox']['source'] == 'unavailable'


def test_live_panel_has_jobs_and_speedometers():
    page = render_dashboard(DashboardData(timestamp='', machine='mini', live_mode=True))
    assert 'engine-machine-jobs' in page and 'engine-llm' in page and 'renderLlm(data.llm)' in page
    assert 'no data' in page and 'conserve' in page


def test_health_ignores_followup_events_and_reports_guardian_and_peer_sync(tmp_path):
    now = datetime.now(timezone.utc)
    healthy = {'timestamp': now.isoformat(), 'checks': [{'passed': True}]}
    (tmp_path / 'watchdog.log').write_text(json.dumps(healthy) + '\n' +
        json.dumps({'timestamp': now.isoformat(), 'action': 'main_ci_followup'}) + '\n')
    write(tmp_path / 'guardian-status.json', {'reloads': 3, 'timestamp': now.isoformat()})
    write(tmp_path / 'machines/mini/engine.json', {'synced_at': (now - timedelta(hours=1)).isoformat(),
                                                'guardian': {'reloads': 2}})
    result = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()
    assert result['machines']['nucbox']['state'] == 'green'
    assert result['machines']['nucbox']['guardian']['reloads'] == 3
    assert result['machines']['mini']['detail'] == 'peer sync stale'
    assert result['machines']['mini']['sync_age_seconds'] >= 3600
    assert result['machines']['mini']['guardian']['reloads'] == 2
    assert result['machines']['mini']['state'] == 'yellow'


def test_fresh_peer_contact_does_not_claim_stale_health_is_green(tmp_path):
    now = datetime.now(timezone.utc)
    write(tmp_path / 'machines/mini/engine.json', {'synced_at': now.isoformat()})
    result = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()
    assert result['machines']['mini']['detail'] == 'peer reachable; health report stale'
    assert result['machines']['mini']['state'] == 'yellow'


def test_failed_peer_sync_is_yellow_even_when_last_health_is_green(tmp_path):
    now = datetime.now(timezone.utc)
    write(tmp_path / 'watchdog.log', {'timestamp': now.isoformat(), 'checks': [{'passed': True}]})
    peer = tmp_path / 'machines/mini'
    write(peer / 'watchdog.log', {'timestamp': now.isoformat(), 'checks': [{'passed': True}]})
    write(peer / 'engine.json', {'synced_at': (now - timedelta(minutes=12)).isoformat()})
    write(peer / 'sync-status.json', {'timestamp': now.isoformat(), 'ok': False})
    result = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()
    assert result['machines']['mini']['state'] == 'yellow'
    assert result['machines']['mini']['reported_state'] == 'green'
    assert result['machines']['mini']['sync_issue'] == 'peer sync failed'
    assert result['machines']['mini']['detail'] == 'peer sync failed; last health report was green'
    assert result['engine'] == 'yellow'


def test_stale_peer_sync_does_not_keep_recent_healthy_report_green(tmp_path):
    now = datetime.now(timezone.utc)
    peer = tmp_path / 'machines/mini'
    write(peer / 'watchdog.log', {'timestamp': (now - timedelta(minutes=20)).isoformat(), 'checks': [{'passed': True}]})
    write(peer / 'engine.json', {'synced_at': (now - timedelta(minutes=20)).isoformat()})
    result = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()['machines']['mini']
    assert result['state'] == 'yellow'
    assert result['reported_state'] == 'green'
    assert result['detail'] == 'peer sync stale; last health report was green'


def test_new_peer_snapshot_recovers_after_older_sync_failure(tmp_path):
    now = datetime.now(timezone.utc)
    peer = tmp_path / 'machines/mini'
    write(peer / 'watchdog.log', {'timestamp': now.isoformat(), 'checks': [{'passed': True}]})
    write(peer / 'engine.json', {'synced_at': now.isoformat()})
    write(peer / 'sync-status.json', {'timestamp': (now - timedelta(minutes=1)).isoformat(), 'ok': False})
    result = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()['machines']['mini']
    assert result['state'] == 'green'
    assert result['detail'] == '0 failed checks'
    assert result['sync_issue'] is None


def test_peer_sync_failure_does_not_hide_fresh_machine_failure(tmp_path):
    now = datetime.now(timezone.utc)
    peer = tmp_path / 'machines/mini'
    write(peer / 'watchdog.log', {'timestamp': now.isoformat(), 'checks': [{'passed': False}]})
    write(peer / 'engine.json', {'synced_at': (now - timedelta(minutes=12)).isoformat()})
    write(peer / 'sync-status.json', {'timestamp': now.isoformat(), 'ok': False})
    result = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()['machines']['mini']
    assert result['state'] == 'red'
    assert result['reported_state'] == 'red'
    assert result['detail'] == '1 failed checks'
    assert result['sync_issue'] == 'peer sync failed'


def test_live_panel_displays_current_sync_issue_without_repeating_detail():
    page = render_dashboard(DashboardData(timestamp='', machine='nucbox', live_mode=True))
    assert 'health.sync_issue&&!health.detail.startsWith(health.sync_issue)' in page
    assert 'health.peer_sync?.ok===false' not in page


def test_current_sync_failure_keeps_operator_recovery_detail():
    page = render_dashboard(DashboardData(timestamp='', machine='nucbox', live_mode=True))
    # Recovery details are shown only while the collector marks sync degraded.
    assert "health.sync_issue&&health.peer_sync?.detail?' · '+health.peer_sync.detail:''" in page
