"""Private discovery must recognize model-suffixed hosts without guessing peers."""
import json
from unittest.mock import patch

import pytest

from cluster.control_plane import peer_endpoints as peers, watchdog
from cluster.control_plane.dashboard.tachometer import EngineCollector


@pytest.mark.parametrize('hostname', ['NucBoxQ8', 'nucbox-q8.local', 'office-nucbox', 'nucbox'])
def test_model_hostname_resolves_one_tailnet_node(hostname, tmp_path, monkeypatch):
    monkeypatch.setenv('STORYPOOL_PEERS_FILE', str(tmp_path / 'absent.json'))
    monkeypatch.delenv('STORYPOOL_NUCBOX_URL', raising=False)
    address = '.'.join(map(str, [100, 90, 1, 2]))
    node = {'HostName': hostname, 'TailscaleIPs': [address]}
    with patch.object(peers, 'tailnet_status', return_value={'Peer': {'one': node}}):
        assert peers.peer_url('nucbox') == 'http://' + address + ':8790'


@pytest.mark.parametrize('hostname', ['nucbox-backup', 'nucboxevil', 'other-nucboxq8', 'nucboxq8.attacker'])
def test_similar_hostnames_do_not_become_cluster_members(hostname):
    with patch.object(peers, 'tailnet_status', return_value={'Peer': {'one': {'HostName': hostname}}}):
        with pytest.raises(peers.PeerUnavailable):
            peers.peer_node('nucbox')


def test_two_model_hosts_remain_ambiguous():
    nodes = {'one': {'HostName': 'NucBoxQ8'}, 'two': {'HostName': 'nucbox'}}
    with patch.object(peers, 'tailnet_status', return_value={'Peer': nodes}):
        with pytest.raises(peers.PeerUnavailable, match='ambiguous'):
            peers.peer_node('nucbox')


@pytest.mark.parametrize('status,kind,action', [
    ({'Peer': {}}, 'identity-missing', 'configure private peers.json'),
    ({'Peer': {'one': {'HostName': 'nucbox'}, 'two': {'HostName': 'NucBoxQ8'}}},
     'identity-ambiguous', 'select one peer in private peers.json'),
])
def test_discovery_failure_has_safe_diagnostic_in_dashboard(status, kind, action, tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, 'LOG_DIR', tmp_path)
    monkeypatch.setenv('STORYPOOL_PEERS_FILE', str(tmp_path / 'absent.json'))
    monkeypatch.delenv('STORYPOOL_NUCBOX_URL', raising=False)
    with patch.object(peers, 'tailnet_status', return_value=status):
        assert watchdog.sync_peer_health('mini') is False
    value = json.loads((tmp_path / 'machines/nucbox/sync-status.json').read_text())
    assert value['failure_kind'] == kind
    assert action in value['detail']
    data = EngineCollector(tmp_path, tmp_path / 'verdicts', 'mini').collect()
    assert data['machines']['nucbox']['state'] == 'yellow'
    assert data['machines']['nucbox']['peer_sync']['detail'] == value['detail']


def test_cli_failure_is_distinct_from_missing_host_without_exposing_output(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, 'LOG_DIR', tmp_path)
    monkeypatch.setenv('STORYPOOL_PEERS_FILE', str(tmp_path / 'absent.json'))
    monkeypatch.delenv('STORYPOOL_NUCBOX_URL', raising=False)
    with patch.object(peers.subprocess, 'run', side_effect=OSError('private CLI output')):
        assert watchdog.sync_peer_health('mini') is False
    value = json.loads((tmp_path / 'machines/nucbox/sync-status.json').read_text())
    assert value['failure_kind'] == 'discovery-unavailable'
    assert 'check Tailscale CLI access' in value['detail']
    assert 'private CLI output' not in json.dumps(value)


def test_dashboard_labels_peer_contact_separately_from_machine_report():
    from cluster.control_plane.dashboard.server import render_dashboard
    from cluster.control_plane.dashboard.data_collector import DashboardData
    page = render_dashboard(DashboardData(timestamp='', machine='mini', live_mode=True))
    assert 'last peer contact ' in page
    assert 'Last machine health report: ' in page


@pytest.fixture(autouse=True)
def clear_host_manifest(monkeypatch):
    monkeypatch.delenv('STORYPOOL_MANIFEST_FILE', raising=False)


def configure_hosts(tmp_path, monkeypatch, hostnames):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'machines': {'nucbox': {'hostnames': hostnames}}}))
    monkeypatch.setenv('STORYPOOL_MANIFEST_FILE', str(path))
    return path


def test_nuc_wsl_resolves_without_manifest():
    node = {'HostName': 'nuc-wsl'}
    with patch.object(peers, 'tailnet_status', return_value={'Peer': {'one': node}}):
        assert peers.peer_node('nucbox') is node


def test_manifest_hostname_is_exact_and_case_insensitive(tmp_path, monkeypatch):
    configure_hosts(tmp_path, monkeypatch, ['Build-Station.local'])
    node = {'HostName': 'build-station'}
    with patch.object(peers, 'tailnet_status', return_value={'Peer': {'one': node}}):
        assert peers.peer_node('nucbox') is node
    for hostname in ('other-build-station', 'build-station-backup', 'build-station.attacker'):
        with patch.object(peers, 'tailnet_status', return_value={'Peer': {'one': {'HostName': hostname}}}):
            with pytest.raises(peers.PeerUnavailable):
                peers.peer_node('nucbox')


def test_manifest_host_plus_existing_alias_is_ambiguous(tmp_path, monkeypatch):
    configure_hosts(tmp_path, monkeypatch, ['build-station'])
    nodes = {'one': {'HostName': 'build-station'}, 'two': {'HostName': 'nucbox'}}
    with patch.object(peers, 'tailnet_status', return_value={'Peer': nodes}):
        with pytest.raises(peers.PeerUnavailable) as error:
            peers.peer_node('nucbox')
    assert error.value.kind == 'identity-ambiguous'


@pytest.mark.parametrize('value', [
    'malformed', json.dumps([]), json.dumps({'machines': []}),
    json.dumps({'machines': {'nucbox': []}}),
    json.dumps({'machines': {'nucbox': {'hostnames': 'build-station'}}}),
    json.dumps({'machines': {'nucbox': {'hostnames': ['https://private:8790']}}}),
    json.dumps({'machines': {'nucbox': {'hostnames': ['*.example']}}}),
    json.dumps({'machines': {'nucbox': {'hostnames': [None]}}}),
    json.dumps({'machines': {'nucbox': {'hostnames': ['a' * 64]}}}),
    json.dumps({'machines': {'nucbox': {'hostnames': ['valid'] * 17}}}),
    ' ' * 65537,
])
def test_invalid_explicit_manifest_fails_closed(value, tmp_path, monkeypatch):
    path = tmp_path / 'manifest.json'
    path.write_text(value)
    monkeypatch.setenv('STORYPOOL_MANIFEST_FILE', str(path))
    with patch.object(peers, 'tailnet_status', return_value={'Peer': {'one': {'HostName': 'nucbox'}}}):
        with pytest.raises(peers.PeerUnavailable, match='manifest'):
            peers.peer_node('nucbox')


def test_missing_explicit_manifest_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv('STORYPOOL_MANIFEST_FILE', str(tmp_path / 'absent.json'))
    with pytest.raises(peers.PeerUnavailable, match='manifest'):
        peers.peer_node('nucbox')
