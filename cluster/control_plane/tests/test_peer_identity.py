"""Private discovery must recognize model-suffixed hosts without guessing peers."""
import json
from unittest.mock import patch

import pytest

from cluster.control_plane import peer_endpoints as peers, watchdog
from cluster.control_plane.dashboard.tachometer import EngineCollector


# Synthetic DNS names are assembled like the existing address fixtures.
TEST_TAILNET = ".".join(["tailtest", "ts", "net"])


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


@pytest.fixture(autouse=True)
def isolate_discovery_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv('STORYPOOL_TAILSCALE_SOCKET', raising=False)
    monkeypatch.setenv('STORYPOOL_DISCOVERY_FILE', str(tmp_path / 'discovery.json'))


@pytest.fixture
def socket_path():
    import tempfile
    from pathlib import Path
    # Darwin AF_UNIX paths must fit within104bytes; pytest's paths exceed it.
    with tempfile.TemporaryDirectory(prefix='sp-socket-', dir='/tmp') as directory:
        yield Path(directory) / 'daemon.sock'


def test_userspace_daemon_socket_discovers_actual_tailnet(tmp_path, socket_path):
    import socket
    from unittest.mock import Mock
    path = socket_path
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))
        (tmp_path / 'discovery.json').write_text(json.dumps({'tailscale_socket': str(path)}))
        status = {'Self': {'HostName': 'nuc-wsl'}}
        with patch.object(peers.subprocess, 'run', return_value=Mock(stdout=json.dumps(status))) as command:
            assert peers.peer_node('nucbox') == status['Self']
        assert command.call_args.args[0] == ['tailscale', '--socket=' + str(path), 'status', '--json']
        assert command.call_args.kwargs['timeout'] == 3


@pytest.mark.parametrize('value', ['malformed', '[]', ' ' * 8193,
    '{"tailscale_socket": "relative.sock"}', '{"tailscale_socket": ""}',
    '{"tailscale_socket": 3}', '{"tailscale_socket": "/absent/socket"}'])
def test_invalid_daemon_locator_fails_closed_without_cli_or_sensitive_error(tmp_path, value):
    (tmp_path / 'discovery.json').write_text(value)
    with patch.object(peers.subprocess, 'run', side_effect=AssertionError('No fallback daemon')):
        with pytest.raises(peers.PeerUnavailable) as error:
            peers.tailnet_status()
    assert error.value.kind == 'discovery-configuration'
    assert value not in str(error.value)


def test_explicit_socket_override_requires_socket_and_does_not_read_file(tmp_path, monkeypatch, socket_path):
    import socket
    path = socket_path
    (tmp_path / 'discovery.json').write_text('malformed private content')
    monkeypatch.setenv('STORYPOOL_TAILSCALE_SOCKET', str(path))
    path.write_text('regular files are not daemon sockets')
    with pytest.raises(peers.PeerUnavailable):
        peers.discovery_socket()
    path.unlink()
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))
        assert peers.discovery_socket() == str(path)


def configure_hosts(tmp_path, monkeypatch, hostnames):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'machines': {'nucbox': {'hostnames': hostnames}}}))
    monkeypatch.setenv('STORYPOOL_MANIFEST_FILE', str(path))
    return path


def test_nuc_wsl_resolves_without_manifest():
    node = {'HostName': 'nuc-wsl'}
    with patch.object(peers, 'tailnet_status', return_value={'Peer': {'one': node}}):
        assert peers.peer_node('nucbox') is node


def test_display_name_resolves_via_current_tailnet_dns(tmp_path, monkeypatch):
    monkeypatch.setenv('STORYPOOL_PEERS_FILE', str(tmp_path / 'absent.json'))
    monkeypatch.delenv('STORYPOOL_MINI_URL', raising=False)
    node = {'HostName': 'Office’s Mac mini',
            'DNSName': f'office-mac-mini.{TEST_TAILNET}.'}
    status = {'CurrentTailnet': {'MagicDNSSuffix': TEST_TAILNET}, 'Self': node}
    with patch.object(peers, 'tailnet_status', return_value=status):
        assert peers.peer_url('mini') == f'https://office-mac-mini.{TEST_TAILNET}:8790'


@pytest.mark.parametrize('suffix,dns', [
    (None, f'office-mini.{TEST_TAILNET}.'),
    (TEST_TAILNET, '.'.join(['office-mini', 'foreign', 'ts', 'net', ''])),
    (TEST_TAILNET, 'office-mini.example.com.'),
    (TEST_TAILNET, f'office-mini.fake.{TEST_TAILNET}.'),
    (TEST_TAILNET, f'office-mini.{TEST_TAILNET}.attacker.'),
    (TEST_TAILNET, f'office-mini..{TEST_TAILNET}.'),
    (TEST_TAILNET, f'office-mini.{TEST_TAILNET}..'),
    (TEST_TAILNET, [f'office-mini.{TEST_TAILNET}']),
    ('example.com', 'office-mini.example.com'),
    (f'{TEST_TAILNET}.', f'office-mini.{TEST_TAILNET}.'),
    ('.'.join(['tailtest', '', 'ts', 'net']), '.'.join(['office-mini', 'tailtest', '', 'ts', 'net'])),
])
def test_dns_identity_needs_exact_valid_current_tailnet(suffix, dns):
    status = {'CurrentTailnet': {'MagicDNSSuffix': suffix},
              'Peer': {'one': {'HostName': 'Display name', 'DNSName': dns}}}
    with patch.object(peers, 'tailnet_status', return_value=status):
        with pytest.raises(peers.PeerUnavailable):
            peers.peer_node('mini')


def test_dns_identity_cannot_choose_between_two_matching_nodes():
    status = {'CurrentTailnet': {'MagicDNSSuffix': TEST_TAILNET}, 'Peer': {
        'one': {'HostName': 'Office computer', 'DNSName': f'office-mini.{TEST_TAILNET}.'},
        'two': {'HostName': 'mini'},
    }}
    with patch.object(peers, 'tailnet_status', return_value=status):
        with pytest.raises(peers.PeerUnavailable) as error:
            peers.peer_node('mini')
    assert error.value.kind == 'identity-ambiguous'


def test_wsl_role_is_not_inferred_from_windows_hardware_dns_name():
    wsl = {'HostName': 'nuc-wsl', 'DNSName': f'nuc-wsl.{TEST_TAILNET}.'}
    status = {'CurrentTailnet': {'MagicDNSSuffix': TEST_TAILNET}, 'Peer': {
        'windows': {'HostName': 'NucBox_K6', 'DNSName': f'nucbox-k6.{TEST_TAILNET}.'},
        'wsl': wsl,
    }}
    with patch.object(peers, 'tailnet_status', return_value=status):
        assert peers.peer_node('nucbox') is wsl


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
