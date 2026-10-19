"""Conservative comparison and reversible native-manager cutover."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from . import guardian, watchdog
from .health_adapters import PERIODS, collect
from .health_state import atomic_json, recovery_lock

RETIRED = ('watchdog', 'guardian', 'ops-doctor', 'shadow')


def launchd_enable_states(output):
    """Require an explicit, understood override for each owned legacy unit."""
    listing = re.fullmatch(r'\s*disabled services\s*=\s*\{(.*)\}\s*', output, re.S)
    if not listing:
        raise RuntimeError('Unknown agent enable-state format')
    states = {}
    for line in listing[1].splitlines():
        if not line.strip():
            continue
        entry = re.fullmatch(r'\s*"([^"\n]+)"\s*=>\s*(true|false|enabled|disabled)\s*', line)
        if not entry or entry[1] in states:
            raise RuntimeError('Unknown agent enable-state entry')
        states[entry[1]] = entry[2] in ('false', 'enabled')
    labels = {'com.storypool.' + name for name in RETIRED}
    if not labels.issubset(states):
        raise RuntimeError('Missing agent enable-state entry')
    return {name: states['com.storypool.' + name] for name in RETIRED}


def same_checks(old, new):
    keys = ('passed', 'execution_error', 'can_repair', 'repair_action')
    def normalize(checks):
        return {c['name']: tuple(c.get(k, False if k == 'execution_error' else None) for k in keys)
                for c in checks}
    return len(old) == len(new) and normalize(old) == normalize(new)


def complete_cycle(status, now, since=0):
    for name, period in PERIODS.items():
        group = status.get('groups', {}).get(name, {})
        if (not group or group.get('error') or not group.get('checks')
                or any(c.get('execution_error') for c in group['checks'])
                or group.get('started_at', group.get('finished_at', 0)) < since
                or not 0 <= now - group.get('finished_at', 0) <= period + 90):
            return False
    return True


def peer_canary_ok(status, peer, now, observation=None):
    if observation is not None:
        from .health_peer import observation_time, number
        try:
            now = observation_time(observation, now)
        except ValueError:
            return False
        served = observation.get('clock_observation', {}).get('served_at', now)
        confirmed = peer.get('rollout', {}).get('last_confirmed_at', peer.get('rollout', {}).get('canary_passed_at', 0))
        if not number(confirmed) or confirmed > served:
            return False
    receipt = peer.get('rollout', {})
    return (peer.get('machine') == 'nucbox' and receipt.get('phase') == 'active'
            and receipt.get('head') == status.get('loaded_head')
            and 0 <= now - peer.get('timestamp', 0) < 900
            and 0 <= now - receipt.get('last_confirmed_at', receipt.get('canary_passed_at', 0)) < 900)


def compare_cycle(status):
    """Run legacy read-only implementations as independent output oracles.

    Legacy services remain live. Candidate mode never repairs or writes alerts.
    Both output sets remain in the receipt; value changes block automatic switch.
    """
    machine = status['machine']
    old_watchdog = watchdog.run_watchdog(machine, check_only=True).to_dict()
    comparisons = {'watchdog': same_checks(old_watchdog['checks'], status['groups']['watchdog']['checks'])}
    old_guardian = {}
    try:
        old_guardian = json.loads((Path(os.environ.get('STORYPOOL_DATA_DIR', '~/.local/share/storypool')).expanduser()
                                   / 'guardian-status.json').read_text())
        from datetime import datetime
        fresh = 0 <= time.time() - datetime.fromisoformat(old_guardian['timestamp']).timestamp() < 240
        comparisons['guardian'] = fresh and old_guardian['dashboard']['ok'] == status['groups']['guardian']['details']['dashboard']['ok']
    except (OSError, ValueError, KeyError, TypeError):
        comparisons['guardian'] = False
    old_doctor = collect('doctor', machine, False)
    comparisons['doctor'] = same_checks(old_doctor['checks'], status['groups']['doctor']['checks'])
    old_shadow = collect('shadow', machine, False)
    def decisions(value):
        return sorted(json.dumps({k: row.get(k) for k in (
            'decision_type', 'action', 'reason', 'repo', 'pr_number', 'head_sha', 'lane_provider')}, sort_keys=True)
                      for name in ('dispatch_decisions', 'gate_decisions')
                      for row in value.get(name, []))
    comparisons['shadow'] = decisions(old_shadow['details']) == decisions(status['groups']['shadow']['details'])
    return dict(timestamp=time.time(), head=status['loaded_head'], machine=machine,
                passed=all(comparisons.values()), comparisons=comparisons,
                legacy=dict(watchdog=old_watchdog, guardian=old_guardian,
                            doctor=old_doctor, shadow=old_shadow))


class Manager:
    def __init__(self, machine, root=None):
        self.machine, self.root = machine, Path(root or Path.home())
        self.domain = 'gui/{}'.format(os.getuid())

    def run(self, argv):
        return guardian.command(argv)

    def snapshot(self):
        result = {}
        disabled = ''
        if self.machine == 'mini':
            listing = self.run(['launchctl', 'print-disabled', self.domain])
            if listing.returncode:
                raise RuntimeError('Unable to inspect agent enable state')
            disabled = launchd_enable_states(listing.stdout)
        for name in RETIRED:
            if self.machine == 'mini':
                target = self.domain + '/com.storypool.' + name
                installed = (self.root / 'Library/LaunchAgents' / ('com.storypool.' + name + '.plist')).exists()
                enabled = disabled[name]
                registered = self.run(['launchctl', 'print', target])
                if registered.returncode not in (0, 113):
                    raise RuntimeError('Unable to inspect agent registration')
                result[name] = dict(installed=installed, enabled=enabled, active=registered.returncode == 0)
            else:
                unit = 'storypool-' + name + '.timer'
                installed = (self.root / '.config/systemd/user' / unit).exists()
                result[name] = dict(installed=installed,
                                    enabled=self.run(['systemctl', '--user', 'is-enabled', unit]).returncode == 0,
                                    active=self.run(['systemctl', '--user', 'is-active', unit]).returncode == 0)
        return result

    def disable(self):
        for name, value in self.snapshot().items():
            if not value['installed']:
                continue
            if self.machine == 'mini':
                target = self.domain + '/com.storypool.' + name
                if self.run(['launchctl', 'disable', target]).returncode:
                    raise RuntimeError('Unable to disable legacy agent')
                self.run(['launchctl', 'bootout', target])
                if not guardian._launchctl_wait(['launchctl', 'print', target], 113,
                                                'com.storypool.' + name, 'unregister', self.run):
                    raise RuntimeError('Unable to unregister legacy agent')
            else:
                unit = 'storypool-' + name
                if self.run(['systemctl', '--user', 'disable', '--now', unit + '.timer']).returncode:
                    raise RuntimeError('Unable to disable legacy timer')
                if self.run(['systemctl', '--user', 'stop', unit + '.service']).returncode:
                    raise RuntimeError('Unable to stop legacy service')

    def verify_disabled(self):
        values = self.snapshot()
        return all(not v['active'] and not v.get('enabled', False) for v in values.values())

    def restore(self, previous):
        for name, value in previous.items():
            if not value['installed']:
                continue
            if self.machine == 'mini':
                target = self.domain + '/com.storypool.' + name
                if self.run(['launchctl', 'enable', target]).returncode:
                    raise RuntimeError('Unable to restore agent enable state')
                if value['active']:
                    path = self.root / 'Library/LaunchAgents' / ('com.storypool.' + name + '.plist')
                    if self.run(['launchctl', 'print', target]).returncode:
                        if self.run(['launchctl', 'bootstrap', self.domain, str(path)]).returncode:
                            raise RuntimeError('Unable to restore legacy agent')
                if not value.get('enabled', True) and self.run(['launchctl', 'disable', target]).returncode:
                    raise RuntimeError('Unable to restore disabled agent state')
            else:
                unit = 'storypool-' + name + '.timer'
                for action, selected in [('enable' if value['enabled'] else 'disable', True),
                                         ('start', value['active'])]:
                    if selected and self.run(['systemctl', '--user', action, unit]).returncode:
                        raise RuntimeError('Unable to restore legacy timer')


def recover_transition(root, status, manager, head, now):
    """A new binary may observe again only after proving legacy ownership."""
    old_head = status.get('loaded_head', '')
    if (status.get('rollout', {}).get('phase') != 'transition-failed'
            or not isinstance(old_head, str) or not isinstance(head, str)
            or not re.fullmatch(r'[0-9a-f]{40}', old_head)
            or not re.fullmatch(r'[0-9a-f]{40}', head) or old_head == head):
        return None
    root = Path(root)
    with recovery_lock(root):
        if (root / '.health-authority').exists():
            return None
        try:
            units = manager.snapshot()
            if set(units) != set(RETIRED) or not all(
                    isinstance(v, dict) and all(v.get(k) is True for k in ('installed', 'enabled', 'active'))
                    for v in units.values()):
                return None
            atomic_json(root / ('health-transition-failed-' + old_head + '.json'),
                        dict(loaded_head=old_head, rollout=status['rollout'],
                             verified_units=units, recovered_at=now))
        except (OSError, RuntimeError, ValueError, TypeError):
            return None
    return dict(phase='observe', started_at=now, recovered_transition_head=old_head)


def activate(root, status, manager, now):
    with recovery_lock(root):
        previous = manager.snapshot()
        old = dict(status['rollout'])
        # Persist transition before stopping units: a guardian must never
        # resurrect an intentionally retired unit while this transaction runs.
        status['rollout'].update(phase='switching', previous_units=previous)
        atomic_json(Path(root) / 'health-status.json', status)
        (Path(root) / '.health-authority').touch(mode=0o600)
        try:
            manager.disable()
            if not manager.verify_disabled():
                raise RuntimeError('Legacy units still active')
        except Exception:
            manager.restore(previous)
            status['rollout'] = old
            atomic_json(Path(root) / 'health-status.json', status)
            (Path(root) / '.health-authority').unlink(missing_ok=True)
            raise
        status['rollout'].update(phase='canary', activated_at=now,
                                  previous_units=previous, head=status['loaded_head'])
        atomic_json(Path(root) / 'health-status.json', status)
    return status['rollout']


def rollback(root, status, manager):
    with recovery_lock(root):
        status['rollout']['phase'] = 'switching'
        atomic_json(Path(root) / 'health-status.json', status)
        manager.restore(status['rollout']['previous_units'])
        status['rollout'].update(phase='rolled-back', rolled_back_at=time.time())
        atomic_json(Path(root) / 'health-status.json', status)
        (Path(root) / '.health-authority').unlink(missing_ok=True)
        (Path(root) / '.health-rollback-request').unlink(missing_ok=True)
    return status['rollout']
