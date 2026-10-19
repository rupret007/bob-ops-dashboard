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


def peer_canary_ok(status, peer, now):
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
            disabled = listing.stdout
        for name in RETIRED:
            if self.machine == 'mini':
                target = self.domain + '/com.storypool.' + name
                installed = (self.root / 'Library/LaunchAgents' / ('com.storypool.' + name + '.plist')).exists()
                enabled = not re.search(r'"com\.storypool\.' + re.escape(name) + r'"\s*=>\s*true', disabled)
                result[name] = dict(installed=installed, enabled=enabled,
                                    active=self.run(['launchctl', 'print', target]).returncode == 0)
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
