"""Persistent node draining. State is local; peer actions go to its dashboard."""
from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


def state_dir():
    return Path(os.environ.get('STORYPOOL_STATE_DIR', '~/.local/share/storypool')).expanduser()


def machine_name():
    return os.environ.get('STORYPOOL_MACHINE', 'mini' if platform.system() == 'Darwin' else 'nucbox')


def read_control(root=None):
    path = Path(root or state_dir()) / 'node-control.json'
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or type(value.get('maintenance')) is not bool:
            raise ValueError('Invalid maintenance state')
        return value
    except FileNotFoundError:
        return {'maintenance': False}
    except (OSError, ValueError):
        return {'maintenance': True, 'reason': 'Unreadable operator state; return to service explicitly'}


def is_draining(machine=None, root=None):
    # This file controls this host only, never a peer's workers.
    return read_control(root)['maintenance']


def allowed_address(value):
    try:
        addr = ipaddress.ip_address(value)
        if getattr(addr, 'ipv4_mapped', None):
            addr = addr.ipv4_mapped
        return addr.is_loopback or addr in ipaddress.ip_network('100.64.0.0/10') or addr in ipaddress.ip_network('fd7a:115c:a1e0::/48')
    except ValueError:
        return False


def authorized_post(address, host, origin, content_type):
    # Exact origin plus IP/localhost Host prevents CSRF and DNS rebinding.
    parsed = urlparse('http://' + host)
    trusted_host = parsed.hostname == 'localhost' or allowed_address(parsed.hostname or '')
    return (allowed_address(address) and trusted_host and
            origin in ('http://' + host, 'https://' + host) and
            content_type.split(';')[0].strip() == 'application/json')


def audit_action(message, root=None):
    root = Path(root or state_dir())
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).astimezone(ZoneInfo('America/Chicago'))
    message = ''.join(c for c in message[:300] if c.isprintable())
    with (root / 'HANDOFF.md').open('a') as audit:
        audit.write('{} — {}.\n'.format(now.isoformat(), message))


def set_maintenance(enabled, actor, root=None):
    if type(enabled) is not bool:
        raise ValueError('maintenance must be boolean')
    root = Path(root or state_dir())
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.node-control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = datetime.now(timezone.utc)
        actor = ''.join(c for c in actor[:120] if c.isprintable())
        value = {'maintenance': enabled, 'updated_at': now.isoformat(), 'actor': actor,
                 'reason': 'Operator requested maintenance' if enabled else 'Operator returned node to service'}
        # Audit must succeed before acknowledging a state change.
        audit_action(actor + ': ' + value['reason'], root)
        temp = root / 'node-control.json.tmp'
        temp.write_text(json.dumps(value) + '\n')
        temp.replace(root / 'node-control.json')
        return value
