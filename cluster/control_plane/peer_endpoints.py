"""Resolve private cluster endpoints at runtime; never guess a public destination."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from .node_control import allowed_address


class PeerUnavailable(RuntimeError):
    pass


def tailnet_status():
    try:
        result = subprocess.run(['tailscale', 'status', '--json'], check=True,
                                capture_output=True, text=True, timeout=3)
        status = json.loads(result.stdout)
        if not isinstance(status, dict):
            raise ValueError('Invalid status')
        return status
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise PeerUnavailable('Peer discovery unavailable; configure private peers.json') from exc


def peer_node(machine):
    aliases = {'mini': ('mini', 'mac-mini'), 'nucbox': ('nucbox',)}
    if machine not in aliases:
        raise PeerUnavailable('Unknown machine')
    status = tailnet_status()
    nodes = [status.get('Self', {})] + list((status.get('Peer') or {}).values())
    matches = []
    for node in nodes:
        host = str(node.get('HostName', '')).lower().removesuffix('.local')
        if any(host == name or host.endswith('-' + name) for name in aliases[machine]):
            matches.append(node)
    if len(matches) != 1:
        raise PeerUnavailable('Peer discovery ambiguous or missing; configure private peers.json')
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
