"""Local supervisor recovery; never launches jobs or bypasses work admission."""
from __future__ import annotations

import argparse
import fcntl
import http.client
import json
import logging
import os
import plistlib
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from cluster.control_plane.service_logging import event
from .health_state import authoritative, serialized_recovery


def dashboard_responding():
    """Probe the actual page directly; ignore proxy settings and redirects."""
    connection = http.client.HTTPConnection('127.0.0.1', 8790, timeout=2)
    try:
        connection.request('GET', '/')
        return connection.getresponse().status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def wait_dashboard():
    # Bound startup verification to about ten seconds, including socket timeouts.
    for attempt in range(4):
        if dashboard_responding():
            return True
        if attempt < 3:
            time.sleep(0.5)
    return False


def dashboard_port_available():
    """Match HTTPServer's bind options, without taking over an old listener."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('127.0.0.1', 8790))
        except OSError:
            return False
    return True


def wait_dashboard_stopped():
    # launchctl bootout can return before the old process releases its socket.
    for attempt in range(61):
        if dashboard_port_available():
            return True
        if attempt < 60:
            time.sleep(0.5)
    return False


@serialized_recovery
def guard_dashboard(machine, root=None, observe_only=False, emit_alert=True):
    """A loaded agent can be dead; verify HTTP every cycle on both hosts."""
    if dashboard_responding():
        event('guardian', 'dashboard_http_verified', machine=machine)
        return {'ok': True, 'restarted': False}
    event('guardian', 'dashboard_http_failed', level=logging.WARNING, machine=machine)
    if observe_only:
        return {'ok': False, 'restarted': False}
    root = Path(root or Path.home())
    try:
        if machine == 'mini':
            restarted = reload_agent(root / 'Library/LaunchAgents/com.storypool.dashboard.plist')
        else:
            restarted = command(['systemctl', '--user', 'restart', 'storypool-dashboard.service']).returncode == 0
        ok = wait_dashboard() and restarted
    except (OSError, ValueError, plistlib.InvalidFileException, KeyError):
        ok = False
    event('guardian', 'dashboard_restart_verified' if ok else 'dashboard_restart_failed',
          level=logging.INFO if ok else logging.ERROR, machine=machine)
    if not ok and emit_alert:
        data = Path(os.environ.get('STORYPOOL_DATA_DIR', str(root / '.local/share/storypool')))
        data.mkdir(parents=True, exist_ok=True)
        alert = data / 'NEEDS_JEFF.md'
        marker = '{} dashboard restart failed'.format(machine)
        # Repeat only once per day while retaining subsequent recurrence evidence.
        today = datetime.now(timezone.utc).date().isoformat()
        with alert.open('a+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.seek(0)
            if not any(today in line and marker in line for line in stream):
                stream.write('\n{} — {}: http://127.0.0.1:8790/ did not recover; inspect launchd/systemd and logs/guardian.jsonl.\n'.format(
                    datetime.now(timezone.utc).isoformat(), marker))
    return {'ok': ok, 'restarted': True}


def command(argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(argv, 1, '', '')


RELOAD_ATTEMPTS = 5
# Health waits for its scheduler/probes to exit; launchd may remain registered
# through that shutdown. Observe for 30 seconds without repeating bootout.
UNREGISTER_ATTEMPTS = 61


def _launchctl_wait(argv, expected, label, stage, run=None):
    """Retry asynchronous launchd transitions; never log command output."""
    run = run or command
    attempts = UNREGISTER_ATTEMPTS if stage == 'unregister' else RELOAD_ATTEMPTS
    for attempt in range(1, attempts + 1):
        result = run(argv)
        if result.returncode == expected:
            return True
        event('guardian', 'reload_stage_pending', unit=label, stage=stage,
              attempt=attempt, exit_code=result.returncode)
        if attempt < attempts:
            time.sleep(0.5)
    event('guardian', 'reload_failed', level=logging.ERROR, unit=label,
          stage=stage, exit_code=result.returncode)
    return False


@serialized_recovery
def reload_agent(path, defer_running=False):
    # Installer and guardian must not boot out/bootstrap the same agent together.
    path = Path(path)
    with path.with_suffix('.reload-lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _reload_agent(path, defer_running)


def _reload_agent(path, defer_running=False):
    """Validate before bootout, bootstrap the new definition, verify registration.

    The updater must not boot itself out during reinstall. Its guardian applies
    the pending reload after launchd reports that the updater process has exited.
    """
    path = Path(path)
    with path.open('rb') as stream:
        label = plistlib.load(stream)['Label']
    if not isinstance(label, str) or not re.fullmatch(r'com\.storypool\.[a-z0-9-]+', label) or path.stem != label:
        raise ValueError('Unexpected StoryPool agent label')
    domain = 'gui/{}'.format(os.getuid())
    target = domain + '/' + label
    pending = path.with_suffix('.reload-pending')
    if defer_running:
        current = command(['launchctl', 'print', target])
        if current.returncode == 0 and re.search(r'\bpid\s*=\s*\d+', current.stdout):
            pending.touch()
            event('guardian', 'reload_deferred', unit=label)
            return True
    enabled = command(['launchctl', 'enable', target])
    if enabled.returncode:
        event('guardian', 'reload_failed', level=logging.ERROR, unit=label,
              stage='enable', exit_code=enabled.returncode)
        return False
    stopped = command(['launchctl', 'bootout', target])
    if stopped.returncode:
        event('guardian', 'reload_stage_pending', unit=label, stage='bootout',
              attempt=1, exit_code=stopped.returncode)
    # bootout returns before launchd always finishes unregistering the service.
    # 113 means no such service; other print failures do not prove absence.
    if not _launchctl_wait(['launchctl', 'print', target], 113, label, 'unregister'):
        return False
    if label == 'com.storypool.dashboard' and not wait_dashboard_stopped():
        event('guardian', 'dashboard_shutdown_timeout', level=logging.ERROR, unit=label)
        return False
    if not _launchctl_wait(['launchctl', 'bootstrap', domain, str(path)], 0, label, 'bootstrap'):
        return False
    # Once bootstrap succeeds, wait for registration without bootstrapping again.
    verified = _launchctl_wait(['launchctl', 'print', target], 0, label, 'register')
    if verified and label == 'com.storypool.dashboard':
        # Registration alone did not prevent the 15:48 outage. Verify serving.
        # RunAtLoad starts the service. Forcing kickstart -k here would kill the
        # just-started process and recreate the same socket shutdown race.
        verified = wait_dashboard()
    if verified:
        pending.unlink(missing_ok=True)
    event('guardian', 'agent_reloaded' if verified else 'reload_failed', unit=label)
    return verified


@serialized_recovery
def recover(machine, root=None, observe_only=False):
    root = Path(root or Path.home())
    actions = []
    if machine == 'mini':
        domain = 'gui/{}'.format(os.getuid())
        for path in sorted((root / 'Library/LaunchAgents').glob('com.storypool.*.plist')):
            label = path.stem
            if label in ('com.storypool.guardian', 'com.storypool.health'):
                continue
            if authoritative() and label in ('com.storypool.watchdog', 'com.storypool.ops-doctor', 'com.storypool.shadow'):
                continue
            # Merely installing Karen in shadow mode never authorizes reviews.
            if label == 'com.storypool.karen-poller' and not (root / '.config/storypool/live').exists():
                continue
            current = command(['launchctl', 'print', domain + '/' + label])
            pending = path.with_suffix('.reload-pending').exists()
            if current.returncode or pending:
                if pending and re.search(r'\bpid\s*=\s*\d+', current.stdout):
                    continue
                try:
                    ok = False if observe_only else reload_agent(path)
                except (OSError, ValueError, plistlib.InvalidFileException, KeyError):
                    ok = False
                actions.append({'unit': label, 'ok': ok})
    else:
        for path in sorted((root / '.config/systemd/user').glob('storypool-*.timer')):
            unit = path.name
            if authoritative() and unit in ('storypool-watchdog.timer', 'storypool-guardian.timer',
                                             'storypool-ops-doctor.timer', 'storypool-shadow.timer'):
                continue
            enabled = command(['systemctl', '--user', 'is-enabled', unit]).returncode == 0
            active = command(['systemctl', '--user', 'is-active', unit]).returncode == 0
            if not enabled or not active:
                ok = False if observe_only else command(['systemctl', '--user', 'enable', '--now', unit]).returncode == 0
                actions.append({'unit': unit, 'ok': ok})
                event('guardian', 'timer_reenabled' if ok else 'reload_failed', unit=unit)
    return actions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reload', type=Path)
    parser.add_argument('--defer-running', action='store_true')
    parser.add_argument('--machine', choices=('mini', 'nucbox'),
                        default='mini' if sys.platform == 'darwin' else 'nucbox')
    args = parser.parse_args()
    if args.reload:
        return 0 if reload_agent(args.reload, args.defer_running) else 1
    if authoritative():
        return 0
    data = Path(os.environ.get('STORYPOOL_DATA_DIR', str(Path.home() / '.local/share/storypool')))
    data.mkdir(parents=True, exist_ok=True)
    with (data / '.guardian.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        actions = recover(args.machine)
        dashboard = guard_dashboard(args.machine)
        if dashboard['restarted']:
            actions.append({'unit': 'dashboard-http', 'ok': dashboard['ok']})
        previous = {}
        try:
            previous = json.loads((data / 'guardian-status.json').read_text())
        except (OSError, ValueError):
            pass
        result = {'timestamp': datetime.now(timezone.utc).isoformat(), 'machine': args.machine,
                  'actions': actions, 'dashboard': dashboard,
                  'reloads': previous.get('reloads', 0) + sum(a['ok'] for a in actions)}
        pending = data / 'guardian-status.tmp'
        pending.write_text(json.dumps(result) + '\n')
        pending.replace(data / 'guardian-status.json')
        event('guardian', 'cycle_complete', machine=args.machine, actions=actions, reloads=result['reloads'])
    return 1 if any(not a['ok'] for a in actions) else 0


if __name__ == '__main__':
    raise SystemExit(main())
