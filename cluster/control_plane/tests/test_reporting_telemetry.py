"""Truthful reporting must not borrow local load or invent provider spend."""
import json
from datetime import datetime, timezone
from unittest.mock import patch

from cluster.control_plane.budget_tracker import BudgetTracker
from cluster.control_plane.reporting_telemetry import machine_guard_snapshot, lane_budget_snapshot, format_budget, format_machine

NOW = 1800000000
STAMP = datetime.fromtimestamp(NOW, timezone.utc).isoformat()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def local_samples():
    return patch.multiple('cluster.control_plane.reporting_telemetry',
                          get_load1=lambda: 2.5, get_swap_pct=lambda: 12.0,
                          get_free_disk_gb=lambda: 100.0)


def peer_snapshot(path, age=0, identity='mini'):
    stamp = datetime.fromtimestamp(NOW-age, timezone.utc).isoformat()
    write(path / 'machines' / 'mini' / 'engine.json', {
        'machine': 'mini', 'synced_at': STAMP,
        'resources': {'machine': identity, 'timestamp': stamp, 'source': 'local',
                      'load': 8.25, 'swap': 84.0, 'passed': False, 'lanes': 1}})


def test_remote_machine_uses_its_own_load_and_failing_swap(tmp_path):
    peer_snapshot(tmp_path)
    with local_samples():
        values = machine_guard_snapshot(tmp_path, 'nucbox', NOW)
    assert values['nucbox']['load'] == 2.5
    assert values['mini']['load'] == 8.25
    assert values['mini']['swap'] == 84.0
    assert values['mini']['passed'] is False
    assert values['mini']['source'] == 'peer-sync'
    assert STAMP in format_machine('Mini', values['mini'])


def test_missing_stale_and_wrong_identity_never_get_local_load(tmp_path):
    with local_samples():
        assert machine_guard_snapshot(tmp_path, 'nucbox', NOW)['mini']['load'] is None
        peer_snapshot(tmp_path, age=901)
        assert machine_guard_snapshot(tmp_path, 'nucbox', NOW)['mini']['load'] is None
        peer_snapshot(tmp_path, identity='nucbox')
        assert machine_guard_snapshot(tmp_path, 'nucbox', NOW)['mini']['load'] is None


def test_budget_unknown_even_when_legacy_cost_is_zero(tmp_path):
    tracker = BudgetTracker(tmp_path / 'config')
    tracker.record('codex', cost_usd=0.02)
    jobs = tmp_path / 'jobs' / 'ledger.jsonl'
    jobs.parent.mkdir()
    job = {'job_id': 'first', 'provider': 'codex', 'machine': 'nucbox', 'start_time': STAMP, 'state': 'running'}
    jobs.write_text(json.dumps(job)+'\n'+json.dumps(dict(job, state='done'))+'\n')
    review = {'owner': 'o', 'repo': 'r', 'pr_number': 1, 'head_sha': 'a'*40, 'timestamp': STAMP,
              'review_results': [{'reviewer': 'Codex'}, {'reviewer': 'Gemini'}]}
    write(tmp_path / 'verdicts' / 'pr_LATEST.json', review)
    write(tmp_path / 'verdicts' / 'pr_KARENPASS.json', review)
    summary = lane_budget_snapshot(tracker, tmp_path, tmp_path / 'verdicts', NOW)
    assert summary['codex']['cost_usd'] is None
    assert summary['cursor']['cost_usd'] is None
    assert summary['codex']['weekly_pct'] is None
    assert summary['codex']['recorded_estimate_usd'] == 0.02
    assert summary['codex']['jobs_24h'] == 1
    assert summary['codex']['reviews_24h'] == 1
    assert summary['gemini']['reviews_24h'] == 1
    assert '$0.00' not in format_budget('cursor', summary['cursor'])
    assert 'unknown' in format_budget('codex', summary['codex'])
    assert '1 jobs, 1 reviewer calls' in format_budget('codex', summary['codex'])
    # Reporting never changes the existing cap calculation or its accounting.
    assert tracker.get_usage('codex').cost_usd == 0.02


def test_missing_activity_evidence_is_unknown(tmp_path):
    tracker = BudgetTracker(tmp_path / 'config')
    summary = lane_budget_snapshot(tracker, tmp_path, tmp_path / 'missing', NOW)
    assert summary['codex']['jobs_24h'] is None
    assert summary['codex']['reviews_24h'] is None
    assert '? jobs, ? reviewer calls' in format_budget('codex', summary['codex'])


def test_engine_advertises_local_resources_for_peer_sync(tmp_path):
    from cluster.control_plane.dashboard.tachometer import EngineCollector
    with local_samples():
        resources = EngineCollector(tmp_path, tmp_path / 'karen', 'mini').collect()['resources']
    assert resources['machine'] == 'mini'
    assert resources['source'] == 'local'
    assert resources['load'] == 2.5


def test_peer_sync_retains_only_correctly_sourced_resources(tmp_path):
    from cluster.control_plane import watchdog
    value = {'machine': 'mini', 'timestamp': STAMP, 'source': 'local', 'load': 8.25}
    payload = {'timestamp': STAMP, 'jobs': {'mini': {'source': 'local'}}, 'resources': value}
    with patch.object(watchdog, 'LOG_DIR', tmp_path):
        assert watchdog.store_peer_engine('mini', payload)
        assert json.loads((tmp_path / 'machines' / 'mini' / 'engine.json').read_text())['resources'] == value
        payload['resources'] = dict(value, machine='nucbox')
        watchdog.store_peer_engine('mini', payload)
        assert 'resources' not in json.loads((tmp_path / 'machines' / 'mini' / 'engine.json').read_text())
