"""A maintenance request must not erase current health or failed peer contact."""
import json
import time
from datetime import datetime, timezone

import pytest

from cluster.control_plane.dashboard import tachometer
from cluster.control_plane.health_peer import direct_observation
from cluster.control_plane.health_state import atomic_json, machine_health


def report(machine, now, state='green'):
    return dict(schema=1, machine=machine, timestamp=now,
                rollout={'phase': 'active'}, expected_groups=['guardian'],
                groups={'guardian': dict(finished_at=now, period=120,
                    checks=[dict(name='dashboard_http', passed=state != 'red')])})


def fixture(tmp_path, monkeypatch, *, peer_error=None, peer_state='green',
            local_state='green', peer_maintenance=False, local_maintenance=False,
            received_age=0, peer_offset=0):
    now = time.time()
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('STORYPOOL_STATE_DIR', str(tmp_path))
    monkeypatch.setattr(tachometer, 'read_codex_quota', lambda *a, **k: {'status': 'unknown', 'weekly_pct': None})
    peer = report('mini', now + peer_offset, peer_state)
    peer['observation'] = dict(nonce='fixture-nonce', served_at=now + peer_offset)
    details = direct_observation(peer, 'mini', 'fixture-nonce', now, 0)
    details['received_at'] = now - received_age
    local = report('nucbox', now, local_state)
    local['groups']['peer'] = dict(finished_at=now, period=120, details=details,
                                  checks=[dict(name='peer_freshness', passed=True)])
    if peer_error:
        # The real scheduler replaces a failed collection's previous details.
        local['groups']['peer'] = dict(finished_at=now, period=120, error=peer_error,
            checks=[dict(name='peer_execution', passed=False, execution_error=True)])
    atomic_json(tmp_path / 'health-status.json', local)
    atomic_json(tmp_path / 'node-control.json', dict(maintenance=local_maintenance,
                                                   reason='Operator maintenance'))
    # Legacy engine/control snapshots can remain fresh during a failed direct probe.
    stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
    atomic_json(tmp_path / 'machines/mini/engine.json', dict(synced_at=stamp,
        control=dict(maintenance=peer_maintenance, reason='Operator maintenance')))
    return now


@pytest.mark.parametrize('error', ['URLError', 'TimeoutExpired', 'ConnectionRefusedError'])
def test_failed_peer_probe_is_unreachable_even_with_recent_maintenance(tmp_path, monkeypatch, error):
    now = fixture(tmp_path, monkeypatch, peer_error=error, peer_maintenance=True)
    result = tachometer.EngineCollector(tmp_path, tmp_path / 'karen', 'nucbox').collect()
    peer = result['machines']['mini']
    assert peer['state'] == 'yellow' and peer['maintenance'] is True
    assert 'unreachable' in peer['detail'].lower()
    assert 'maintenance' in peer['detail'].lower()
    projected = machine_health(tmp_path, 'nucbox', now)['mini']
    assert projected['state'] == 'yellow'
    assert projected['connectivity'] == 'unreachable'
    assert 'unreachable' in projected['detail'].lower()
    assert result['engine'] != 'green'


def test_healthy_peer_in_maintenance_remains_reachable_with_clock_skew(tmp_path, monkeypatch):
    fixture(tmp_path, monkeypatch, peer_maintenance=True, peer_offset=3600)
    peer = tachometer.EngineCollector(tmp_path, tmp_path / 'karen', 'nucbox').collect()['machines']['mini']
    assert peer['state'] == 'maintenance' and peer['maintenance'] is True
    assert peer['connectivity'] == 'reachable'


def test_stale_peer_maintenance_cannot_replace_unknown_health(tmp_path, monkeypatch):
    fixture(tmp_path, monkeypatch, peer_maintenance=True, received_age=500)
    result = tachometer.EngineCollector(tmp_path, tmp_path / 'karen', 'nucbox').collect()
    assert result['machines']['mini']['state'] == 'yellow'
    assert result['machines']['mini']['maintenance'] is True
    assert result['machines']['mini']['connectivity'] == 'unknown'
    assert result['engine'] != 'green'


@pytest.mark.parametrize('machine', ['mini', 'nucbox'])
def test_maintenance_cannot_hide_a_reported_health_failure(tmp_path, monkeypatch, machine):
    fixture(tmp_path, monkeypatch, peer_state='red' if machine == 'mini' else 'green',
            local_state='red' if machine == 'nucbox' else 'green',
            peer_maintenance=machine == 'mini', local_maintenance=machine == 'nucbox')
    result = tachometer.EngineCollector(tmp_path, tmp_path / 'karen', 'nucbox').collect()
    assert result['machines'][machine]['state'] == 'red'
    assert result['machines'][machine]['maintenance'] is True
    assert result['engine'] == 'red'


def test_healthy_local_maintenance_preserves_its_operator_controls(tmp_path, monkeypatch):
    fixture(tmp_path, monkeypatch, local_maintenance=True)
    row = tachometer.EngineCollector(tmp_path, tmp_path / 'karen', 'nucbox').collect()['machines']['nucbox']
    assert row['state'] == 'maintenance' and row['maintenance'] is True


def test_local_failure_is_not_reclassified_as_peer_unreachable(tmp_path, monkeypatch):
    now = fixture(tmp_path, monkeypatch, local_state='red')
    row = machine_health(tmp_path, 'nucbox', now)['nucbox']
    assert row['state'] == 'red' and row.get('connectivity') != 'unreachable'


def test_invalid_peer_reply_is_unknown_without_claiming_a_network_outage(tmp_path, monkeypatch):
    now = fixture(tmp_path, monkeypatch, peer_error='ValueError', peer_maintenance=True)
    row = machine_health(tmp_path, 'nucbox', now)['mini']
    assert row['state'] == 'yellow' and row['connectivity'] == 'unknown'
    assert 'failed' in row['detail'].lower() and 'unreachable' not in row['detail'].lower()
