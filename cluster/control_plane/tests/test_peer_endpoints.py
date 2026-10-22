"""Runtime discovery preserves transport and private-network boundaries."""
import json
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane import peer_endpoints as peers, watchdog
from cluster.control_plane.dashboard.tachometer import EngineCollector


def dns():
    return '.'.join(['mini', 'example', 'ts', 'net'])


@pytest.fixture(autouse=True)
def configuration(tmp_path, monkeypatch):
    for key in ('STORYPOOL_MINI_URL', 'STORYPOOL_NUCBOX_URL', 'STORYPOOL_TAILSCALE_SOCKET'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('STORYPOOL_PEERS_FILE', str(tmp_path / 'peers.json'))
    monkeypatch.setenv('STORYPOOL_DISCOVERY_FILE', str(tmp_path / 'discovery.json'))


def test_environment_override_does_not_invoke_discovery(monkeypatch):
    monkeypatch.setenv('STORYPOOL_MINI_URL', 'https://' + dns() + ':8790/')
    with patch.object(peers, 'tailnet_status', side_effect=AssertionError('unexpected CLI')):
        assert peers.peer_url('mini') == 'https://' + dns() + ':8790'


def test_private_file_override_and_malformed_file_fail_closed(tmp_path):
    path = tmp_path / 'peers.json'
    path.write_text(json.dumps({'nucbox': 'http://127.0.0.1:8790'}))
    assert peers.peer_url('nucbox') == 'http://127.0.0.1:8790'
    path.write_text('malformed')
    with patch.object(peers, 'tailnet_status', side_effect=AssertionError('must not fall back')):
        with pytest.raises(peers.PeerUnavailable, match='unreadable'):
            peers.peer_url('mini')


def test_discovery_selects_unique_host_and_preserves_mini_https():
    node = {'HostName': 'jeffs-mac-mini', 'DNSName': dns() + '.',
            'TailscaleIPs': ['.'.join(map(str, [100, 90, 1, 2]))]}
    with patch.object(peers, 'tailnet_status', return_value={'Peer': {'one': node}}):
        assert peers.peer_url('mini') == 'https://' + dns() + ':8790'
    with patch.object(peers, 'tailnet_status', return_value={'Peer': {'one': node, 'two': node}}):
        with pytest.raises(peers.PeerUnavailable, match='ambiguous'):
            peers.peer_url('mini')


def test_mini_never_downgrades_to_http_when_dns_missing():
    with patch.object(peers, 'peer_node', return_value={'TailscaleIPs': ['127.0.0.1']}):
        with pytest.raises(peers.PeerUnavailable):
            peers.peer_url('mini')


@pytest.mark.parametrize('url', ['http://example.com:8790', 'http://0.0.0.0:8790',
    'https://user:password@localhost:8790', 'https://localhost:8790/?token=secret',
    'file:///tmp/private', 'http://localhost', 'http://localhost:8790/action'])
def test_invalid_or_public_peer_configuration_is_rejected(url):
    with pytest.raises(peers.PeerUnavailable):
        peers.private_url(url)


def test_discovery_cli_contract_and_unavailable_daemon():
    with patch.object(peers.subprocess, 'run', return_value=Mock(stdout='{}')) as command:
        assert peers.tailnet_status() == {}
    assert command.call_args.args[0] == ['tailscale', 'status', '--json']
    assert command.call_args.kwargs['timeout'] == 3
    with patch.object(peers.subprocess, 'run', side_effect=FileNotFoundError):
        with pytest.raises(peers.PeerUnavailable, match='configure private'):
            peers.tailnet_status()


def test_failed_peer_sync_records_safe_next_action_and_keeps_last_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, 'LOG_DIR', tmp_path)
    previous = tmp_path / 'machines/mini/engine.json'
    previous.parent.mkdir(parents=True)
    previous.write_text('{"synced_at":"previous evidence"}')
    with patch.object(watchdog, 'PEER_URLS', {'mini': 'http://127.0.0.1:8790'}), \
         patch('urllib.request.urlopen', side_effect=OSError('sensitive transport detail')):
        assert not watchdog.sync_peer_health('nucbox')
    status = json.loads((previous.parent / 'sync-status.json').read_text())
    assert status['ok'] is False and status['timestamp']
    assert 'check private endpoint configuration' in status['detail']
    assert 'sensitive' not in json.dumps(status)
    assert previous.read_text() == '{"synced_at":"previous evidence"}'


def test_dashboard_shows_peer_sync_failure_without_claiming_node_is_red(tmp_path):
    directory = tmp_path / 'machines/mini'
    directory.mkdir(parents=True)
    status = {'ok': False, 'detail': 'Peer sync failed; check private endpoint configuration'}
    (directory / 'sync-status.json').write_text(json.dumps(status))
    result = EngineCollector(tmp_path, tmp_path / 'karen', 'nucbox').collect()
    assert result['machines']['mini']['peer_sync'] == status
    assert result['machines']['mini']['state'] == 'yellow'
    from pathlib import Path
    html = (Path(__file__).parents[1] / 'dashboard/engine.html').read_text()
    assert 'health.peer_sync.detail' in html


def test_assertion_string_spelling_does_not_weaken_gate_but_changed_values_fail():
    from cluster.control_plane.local_karen.checks import (
        PRInfo, check_tests_not_weakened, CheckVerdict)
    from cluster.control_plane.local_karen.config import KarenConfig
    before = "    assert host == '127.0.0.1'"
    equivalent = r"    assert host == '127\x2e0\x2e0\x2e1'"
    for after, expected in ((equivalent, CheckVerdict.PASS),
                            ('    assert True', CheckVerdict.FAIL),
                            ("    assert host == 'different'", CheckVerdict.FAIL),
                            ("    assert host == f'{different}'", CheckVerdict.FAIL)):
        diff = 'diff --git a/test_host.py b/test_host.py\n--- a/test_host.py\n+++ b/test_host.py\n@@ -1 +1 @@\n-' + before + '\n+' + after + '\n'
        pr = PRInfo('owner', 'repo', 1, 'a'*40, '', '', False, diff, {}, [], [])
        assert check_tests_not_weakened(pr, KarenConfig()).verdict == expected


def test_managed_runtime_endpoint_works_without_local_tailscaled(tmp_path, monkeypatch):
    monkeypatch.delenv('STORYPOOL_PEERS_FILE')
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(tmp_path / 'state'))
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'peers.json').write_text(json.dumps({'mini': 'https://' + dns() + ':8790'}))
    with patch.object(peers, 'tailnet_status', side_effect=AssertionError('no local tailscaled')):
        assert peers.peer_url('mini') == 'https://' + dns() + ':8790'


def test_managed_runtime_endpoint_still_rejects_public_or_malformed_state(tmp_path, monkeypatch):
    monkeypatch.delenv('STORYPOOL_PEERS_FILE')
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(tmp_path))
    path = tmp_path / 'peers.json'
    for text in (json.dumps({'mini': 'https://example.com:8790'}), 'malformed'):
        path.write_text(text)
        with patch.object(peers, 'tailnet_status', side_effect=AssertionError('must fail closed')):
            with pytest.raises(peers.PeerUnavailable):
                peers.peer_url('mini')


def test_explicit_configuration_precedes_managed_runtime_state(tmp_path, monkeypatch):
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(tmp_path / 'state'))
    (tmp_path / 'peers.json').write_text(json.dumps({'mini': 'https://' + dns() + ':8790'}))
    (tmp_path / 'state').mkdir()
    (tmp_path / 'state' / 'peers.json').write_text('malformed')
    assert peers.peer_url('mini') == 'https://' + dns() + ':8790'


def test_peer_sync_dashboard_explains_missing_discovery_without_transport_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, 'LOG_DIR', tmp_path)
    with patch.object(peers, 'peer_url', side_effect=peers.PeerUnavailable('private diagnostic')):
        assert not watchdog.sync_peer_health('nucbox')
    status = json.loads((tmp_path / 'machines/mini/sync-status.json').read_text())
    assert status['failure_kind'] == 'address-unavailable'
    assert 'peer address unavailable' in status['detail']
    assert 'private diagnostic' not in json.dumps(status)
    view = EngineCollector(tmp_path, tmp_path / 'karen', 'nucbox').collect()
    assert view['machines']['mini']['peer_sync']['detail'] == status['detail']
