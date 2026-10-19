"""Resolve private cluster endpoints at runtime; never guess a public destination."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from .node_control import allowed_address


class PeerUnavailable(RuntimeError):
    def __init__(self, message, kind='address-unavailable'):
        super().__init__(message)
        self.kind = kind


def tailnet_status():
    argv = ['tailscale']
    socket = discovery_socket()
    if socket:
        argv.append('--socket=' + socket)
    argv += ['status', '--json']
    try:
        result = subprocess.run(argv, check=True,
                                capture_output=True, text=True, timeout=3)
        status = json.loads(result.stdout)
        if not isinstance(status, dict):
            raise ValueError('Invalid status')
        return status
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise PeerUnavailable('Peer discovery unavailable; configure private peers.json',
                              kind='discovery-unavailable') from exc


def discovery_socket():
    """Select the configured local daemon, including a userspace WSL daemon.

    The locator holds no peer addresses or credentials; roles and current
    addresses still come from the daemon's authenticated tailnet state.
    """
    try:
        socket = os.environ.get('STORYPOOL_TAILSCALE_SOCKET')
        if socket is None:
            path = Path(os.environ.get('STORYPOOL_DISCOVERY_FILE',
                '~/.config/storypool/discovery.json')).expanduser()
            try:
                with path.open('rb') as stream:
                    raw = stream.read(8193)
            except FileNotFoundError:
                return None
            if len(raw) > 8192:
                raise ValueError('Oversized discovery configuration')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError('Invalid discovery configuration')
            socket = value.get('tailscale_socket')
            if socket is None:
                return None
        if (not isinstance(socket, str) or not socket or len(socket) > 4096
                or '\x00' in socket or not Path(socket).is_absolute()
                or not Path(socket).is_socket()):
            raise ValueError('Invalid daemon socket')
        return socket
    except (OSError, ValueError) as exc:
        raise PeerUnavailable('Private discovery configuration invalid; check local daemon socket',
                              kind='discovery-configuration') from exc



def manifest_hostnames(machine):
    """Read optional machine identities without generating deployment config.

    STORYPOOL_MANIFEST_FILE points to JSON containing
    {"machines": {"nucbox": {"hostnames": ["build-station"]}}}.
    Entries are exact Tailscale HostName labels, not URLs or suffix patterns.
    Explicit malformed configuration fails closed; private URL checks still apply.
    """
    if 'STORYPOOL_MANIFEST_FILE' not in os.environ:
        return set()
    try:
        path = Path(os.environ['STORYPOOL_MANIFEST_FILE']).expanduser()
        with path.open('rb') as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError('Manifest too large')
        data = json.loads(raw)
        machines = data.get('machines') if isinstance(data, dict) else None
        if not isinstance(machines, dict):
            raise ValueError('Invalid machine map')
        profile = machines.get(machine, {})
        if not isinstance(profile, dict):
            raise ValueError('Invalid machine profile')
        names = profile.get('hostnames', [])
        if not isinstance(names, list) or len(names) > 16:
            raise ValueError('Invalid hostname list')
        result = set()
        for name in names:
            if not isinstance(name, str):
                raise ValueError('Invalid hostname')
            host = name.lower().removesuffix('.local')
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', host):
                raise ValueError('Invalid hostname')
            result.add(host)
        return result
    except (OSError, ValueError) as exc:
        raise PeerUnavailable('Private host manifest unreadable or invalid') from exc


def canonical_hostname(node, status):
    """Use only a DNS label issued in this node's current private tailnet.

    Tailscale HostName is an OS display name; macOS may include spaces and
    punctuation there. DNSName is the canonical host.<MagicDNSSuffix> name.
    """
    tailnet = status.get('CurrentTailnet')
    suffix = tailnet.get('MagicDNSSuffix') if isinstance(tailnet, dict) else None
    dns = node.get('DNSName')
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    if not isinstance(suffix, str) or not isinstance(dns, str):
        return ''
    suffix, dns = suffix.lower(), dns.lower().removesuffix('.')
    if not re.fullmatch(r'(?:' + label + r'\.)+ts\.net', suffix):
        return ''
    ending = '.' + suffix
    host = dns[:-len(ending)] if dns.endswith(ending) else ''
    return host if re.fullmatch(label, host) else ''


def peer_node(machine):
    aliases = {'mini': ('mini', 'mac-mini'), 'nucbox': ('nucbox',)}
    if machine not in aliases:
        raise PeerUnavailable('Unknown machine')
    exact_hosts = manifest_hostnames(machine)
    if machine == 'nucbox':
        exact_hosts.add('nuc-wsl')
    status = tailnet_status()
    nodes = [status.get('Self', {})] + list((status.get('Peer') or {}).values())
    matches = []
    for node in nodes:
        host = str(node.get('HostName', '')).lower().removesuffix('.local')
        identities = {host, canonical_hostname(node, status)} - {''}
        # Hardware model suffixes are common on NucBox hosts. Keep the grammar
        # anchored and reject multiple matches rather than selecting a host by
        # list order, online state, or a loose substring match.
        model_host = machine == 'nucbox' and re.fullmatch(r'nucbox-?[a-z]{1,4}[0-9]{1,3}', host)
        if (model_host or identities & exact_hosts or
                any(identity == name or identity.endswith('-' + name)
                    for identity in identities for name in aliases[machine])):
            matches.append(node)
    if len(matches) != 1:
        kind = 'identity-ambiguous' if matches else 'identity-missing'
        raise PeerUnavailable('Peer discovery ambiguous or missing; configure private peers.json', kind=kind)
    return matches[0]


def private_url(value):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if (parsed.scheme not in ('http', 'https') or not parsed.port or parsed.username or
                parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/') or
                not (allowed_address(host) or host == 'localhost' or
                     (parsed.scheme == 'https' and host.endswith('.ts.net')))):
            raise ValueError('Invalid endpoint')
        return value.rstrip('/')
    except (ValueError, TypeError, AttributeError) as exc:
        raise PeerUnavailable('Peer endpoint must be a private HTTP(S) origin with a port') from exc


def peer_url(machine):
    if machine not in ('mini', 'nucbox'):
        raise PeerUnavailable('Unknown machine')
    value = os.environ.get('STORYPOOL_' + machine.upper() + '_URL')
    if not value:
        paths = [Path(os.environ.get('STORYPOOL_PEERS_FILE', '~/.config/storypool/peers.json')).expanduser()]
        # A managed installation may be unable to change ~/.config (for example
        # the monitor sandbox). Keep an explicit private endpoint in shared
        # runtime state rather than depending on a local tailscaled in WSL.
        if 'STORYPOOL_PEERS_FILE' not in os.environ:
            paths.append(Path(os.environ.get('STORYPOOL_DATA_DIR', '~/.local/share/storypool')).expanduser() / 'peers.json')
        for path in paths:
            try:
                value = json.loads(path.read_text()).get(machine)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, AttributeError) as exc:
                raise PeerUnavailable('Private peer configuration unreadable') from exc
            if value:
                break
    if value:
        return private_url(value)
    node = peer_node(machine)
    if machine == 'mini':
        dns = str(node.get('DNSName', '')).rstrip('.')
        # The Mini's dashboard is behind Tailscale HTTPS; never fall back to HTTP.
        return private_url('https://' + dns + ':8790')
    addresses = [ip for ip in node.get('TailscaleIPs', []) if allowed_address(ip) and ':' not in ip]
    if not addresses:
        raise PeerUnavailable('Peer tailnet IPv4 unavailable')
    return private_url('http://' + addresses[0] + ':8790')


def share_address():
    addresses = tailnet_status().get('Self', {}).get('TailscaleIPs', [])
    return next((ip for ip in addresses if allowed_address(ip) and ':' not in ip), None)


class PeerURLs:
    """Compatibility mapping: discovery occurs only when a consumer needs a peer."""
    def __getitem__(self, machine):
        return peer_url(machine)
