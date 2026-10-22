"""A retired healthy log must never override the new authority."""
import json
import time
from datetime import datetime, timezone

from cluster.control_plane.dashboard.tachometer import EngineCollector
from cluster.control_plane.health_state import atomic_json


def test_active_authority_ignores_legacy_green_and_missing_snapshot(tmp_path):
    (tmp_path / '.health-authority').touch()
    (tmp_path / 'watchdog.log').write_text(json.dumps({
        'timestamp': datetime.now(timezone.utc).isoformat(), 'checks': [{'passed': True}]}))
    result = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()
    assert result['machines']['nucbox']['state'] == 'yellow'
    assert result['engine'] == 'yellow'


def test_unified_failed_check_and_original_peer_time_reach_dashboard(tmp_path):
    now = time.time()
    value = {'schema': 1, 'timestamp': now, 'machine': 'nucbox', 'rollout': {'phase': 'active'},
             'groups': {'guardian': {'finished_at': now, 'period': 120,
                                   'checks': [{'name': 'dashboard_http', 'passed': False}],
                                   'details': {'dashboard': {'ok': False}}},
                        'peer': {'finished_at': now, 'period': 300, 'checks': [],
                                 'details': {'received_at': now, 'health': {
                                     'schema': 1, 'machine': 'mini', 'timestamp': now - 600,
                                     'groups': {}}}}}}
    atomic_json(tmp_path / 'health-status.json', value)
    result = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()
    assert result['machines']['nucbox']['state'] == 'red'
    assert result['machines']['mini']['state'] == 'yellow'
    assert result['engine'] == 'red'
