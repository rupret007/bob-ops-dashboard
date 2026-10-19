"""Clock differences never freshen stale reports or invalidate fresh replies."""
import copy
import json
from http.server import HTTPServer
from threading import Thread
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from cluster.control_plane import health_adapters as adapters, health_peer, health_state
from cluster.control_plane.dashboard.data_collector import DataCollector
from cluster.control_plane.dashboard.server import DashboardHandler
from cluster.control_plane.health_rollout import peer_canary_ok


def report(served=1000):
    return dict(schema=1, machine='nucbox', timestamp=served - 5,
                observation=dict(nonce='a' * 32, served_at=served),
                rollout=dict(phase='active', head='b' * 40, last_confirmed_at=served - 5),
                groups=dict(guardian=dict(finished_at=served - 10, period=120,
                                         checks=[dict(name='http', passed=True)])))


@pytest.mark.parametrize('offset', [-3600, -7.2, 7.2, 3600])
def test_direct_observation_uses_peer_clock_and_preserves_source_time(tmp_path, offset):
    value = report(10000 + offset)
    original = copy.deepcopy(value)
    details = health_peer.direct_observation(value, 'nucbox', 'a' * 32, 10000, .2)
    assert value == original
    local = dict(schema=1, machine='mini', timestamp=10000, loaded_head='b' * 40,
                 rollout=dict(phase='active'), groups=dict(peer=dict(details=details)))
    health_state.atomic_json(tmp_path / 'health-status.json', local)
    assert health_state.machine_health(tmp_path, 'mini', 10010)['nucbox']['state'] == 'green'
    assert peer_canary_ok(local, value, 10010, details)
    assert health_state.machine_health(tmp_path, 'mini', 10146)['nucbox']['state'] == 'yellow'
    assert not peer_canary_ok(local, value, 10400, details)


@pytest.mark.parametrize('field,value', [('nonce', 'b' * 32), ('served_at', None),
    ('served_at', True), ('served_at', float('nan')), ('served_at', float('inf'))])
def test_rejects_replayed_missing_or_invalid_clock_envelope(field, value):
    source = report()
    source['observation'][field] = value
    with pytest.raises(ValueError):
        health_peer.direct_observation(source, 'nucbox', 'a' * 32, 900, .2)


@pytest.mark.parametrize('elapsed', [-1, 5.01, float('nan')])
def test_slow_or_invalid_exchange_never_anchors_health(elapsed):
    with pytest.raises(ValueError):
        health_peer.direct_observation(report(), 'nucbox', 'a' * 32, 900, elapsed)


@pytest.mark.parametrize('change', [dict(timestamp=849), dict(timestamp=1000.1),
                                 dict(machine='mini'), dict(schema=2)])
def test_nonce_cannot_rescue_expired_future_wrong_identity_or_schema(change):
    with pytest.raises(ValueError):
        health_peer.direct_observation(dict(report(), **change), 'nucbox', 'a' * 32, 900, .2)


def test_round_trip_age_counts_toward_expiry_and_clock_reversal_rejects():
    with pytest.raises(ValueError):
        health_peer.direct_observation(dict(report(), timestamp=850), 'nucbox', 'a' * 32, 900, .2)
    details = health_peer.direct_observation(report(), 'nucbox', 'a' * 32, 900, .2)
    with pytest.raises(ValueError):
        health_peer.observation_time(details, 899)
    details['health']['rollout']['last_confirmed_at'] = 1000.1
    assert not peer_canary_ok(dict(loaded_head='b' * 40), details['health'], 900, details)


def test_endpoint_echoes_fresh_challenge_without_recursing_and_collector_consumes_it(tmp_path):
    value = report()
    value.pop('observation')
    value['groups']['peer'] = dict(details=dict(health=dict(recursive='private peer cache')))
    health_state.atomic_json(tmp_path / 'health-status.json', value)

    class Handler(DashboardHandler):
        collector = DataCollector(data_dir=tmp_path, karen_dir=tmp_path)

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:' + str(server.server_port)
    try:
        with patch('cluster.control_plane.health_service.time.time', return_value=1000):
            with urlopen(base + '/api/health?nonce=' + 'a' * 32) as response:
                assert response.headers['Cache-Control'] == 'no-store'
                payload = json.load(response)
            assert payload['observation'] == dict(nonce='a' * 32, served_at=1000)
            assert 'private peer cache' not in str(payload)
            with patch.object(adapters.watchdog, 'PEER_URLS', {'nucbox': base}):
                observed = adapters.collect_peer('mini')
            assert observed['checks'][0]['passed']
            assert observed['details']['health']['timestamp'] == 995
        with pytest.raises(HTTPError) as error:
            urlopen(base + '/api/health?nonce=bad')
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_collection_cadence_fits_existing_report_expiry():
    assert adapters.PERIODS['peer'] + 5 < 150


def test_rolling_upgrade_keeps_old_peer_on_original_strict_clock_validation():
    old = report()
    old.pop('observation')
    details = health_peer.direct_observation(old, 'nucbox', 'a' * 32, 1000, .2)
    assert details['clock_mode'] == 'legacy-strict'
    assert 'clock_observation' not in details
    for now in (990, 1200):
        with pytest.raises(ValueError):
            health_peer.direct_observation(old, 'nucbox', 'a' * 32, now, .2)
