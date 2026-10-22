"""One native health scheduler; legacy services remain owner until cutover."""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import math
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .health_adapters import PERIODS, TIMEOUTS
from .health_state import (atomic_json, data_dir, observe, read_status,
                           render_alerts)
from .service_logging import event, redact
from .watchdog_check import bounded_output


def validated_result(result, group):
    """An empty or malformed adapter reply cannot prove probe completion."""
    if (not isinstance(result, dict) or not isinstance(result.get('checks'), list)
            or not result['checks']):
        raise ValueError('Invalid check result')
    names = set()
    for check in result['checks']:
        if (not isinstance(check, dict) or not isinstance(check.get('name'), str)
                or not check['name'].strip() or check['name'] in names
                or check['name'] == group + '_execution'
                or type(check.get('passed')) is not bool
                or not isinstance(check.get('message', ''), str)
                or any(key in check and type(check[key]) is not bool
                       for key in ('unknown', 'execution_error', 'repaired'))):
            raise ValueError('Invalid check schema')
        names.add(check['name'])
    if (any(key in result and type(result[key]) is not bool
            for key in ('unknown', 'repair_attempted', 'execution_completed'))
            or ('details' in result and not isinstance(result['details'], dict))
            or (result.get('error') is not None
                and (not isinstance(result['error'], str) or not result['error']))):
        raise ValueError('Invalid group schema')
    return result


def execute(group, machine, live):
    started = time.monotonic()
    argv = [sys.executable, '-m', 'cluster.control_plane.health_adapters', group,
            '--machine', machine]
    if live:
        argv.append('--live')
    result = redact(validated_result(json.loads(bounded_output(argv, TIMEOUTS[group])), group))
    elapsed = time.monotonic() - started
    if not math.isfinite(elapsed) or not 0 <= elapsed <= TIMEOUTS[group]:
        raise TimeoutError('Probe completion exceeded deadline')
    # Only this parent-side runner can supply completion proof, after the
    # bounded child has exited and its complete response has been validated.
    result['execution_completed'] = True
    return result


def execution_succeeded(result):
    return (result.get('execution_completed') is True and result.get('error') is None
            and not result.get('unknown', False)
            and not any(check.get('execution_error') for check in result['checks']))


def public_snapshot(status, nonce=None):
    result = copy.deepcopy(status)
    # Peers may exchange only self-observations, never recursively cached peers.
    result.get('groups', {}).get('peer', {}).pop('details', None)
    result.pop('incidents', None)
    if nonce is not None:
        result['observation'] = dict(nonce=nonce, served_at=time.time())
    return result


class Scheduler:
    def __init__(self, root, machine, head, fast=None, slow=None):
        self.root, self.machine = Path(root), machine
        self.fast = fast or ThreadPoolExecutor(max_workers=2)
        self.slow = slow or ThreadPoolExecutor(max_workers=4)
        self.pending, self.due = {}, {name: 0 for name in PERIODS}
        self.status = read_status(root)
        if self.status.get('machine') != machine:
            self.status = {}
        changed = self.status.get('loaded_head') not in (None, head)
        if changed and self.status.get('rollout', {}).get('phase') == 'transition-failed':
            from .health_rollout import Manager, recover_transition
            recovered = recover_transition(root, self.status, Manager(machine), head, time.time())
            if recovered:
                self.status['rollout'] = recovered
        self.status.update(schema=1, machine=machine, loaded_head=head,
                           expected_groups=list(PERIODS))
        self.status.setdefault('groups', {})
        self.status.setdefault('rollout', {'phase': 'observe', 'started_at': time.time()})
        if changed:
            # Never compare/certify a new binary using a previous binary's checks.
            self.status['groups'] = {}
            if self.status['rollout']['phase'] == 'rolled-back':
                self.status['rollout'] = dict(phase='observe', started_at=time.time(),
                                              previous_rollback=self.status['rollout'])
        if changed and self.status['rollout']['phase'] in ('active', 'canary'):
            self.status['rollout'].update(phase='canary', activated_at=time.time(), head=head)
        self.status.setdefault('repair_after', {})
        self.transition = None
        self.retry_rollout_at = 0

    def rollout_tick(self, monotonic, now):
        from .health_rollout import (Manager, activate, compare_cycle, complete_cycle,
                                     peer_canary_ok, rollback)
        rollout = self.status['rollout']
        if self.transition and self.transition[1].done():
            kind, future = self.transition
            self.transition = None
            try:
                result = future.result()
                if kind == 'compare':
                    rollout['comparison'] = result
                    if result.get('passed'):
                        self.retry_rollout_at = 0
                else:
                    self.status['rollout'] = rollout = result
                    if kind == 'activate':
                        self.due = {name: monotonic for name in PERIODS}
            except Exception as exc:
                rollout['error'] = type(exc).__name__
                # A partial transition can be diagnosed from the persisted
                # manager receipt; do not retry native mutations blindly.
                rollout['phase'] = 'transition-failed' if kind != 'compare' else 'observe'
                self.retry_rollout_at = now + 600
        if self.transition or now < self.retry_rollout_at:
            return
        request = self.root / '.health-rollback-request'
        if request.exists() and rollout['phase'] in ('active', 'canary'):
            rollout['phase'] = 'switching'
            atomic_json(self.root / 'health-status.json', self.status)
            self.transition = ('rollback', self.slow.submit(rollback, self.root,
                               copy.deepcopy(self.status), Manager(self.machine)))
            return
        if rollout['phase'] == 'observe' and complete_cycle(self.status, now):
            comparison = rollout.get('comparison', {})
            if not comparison.get('passed') or comparison.get('head') != self.status['loaded_head']:
                self.transition = ('compare', self.slow.submit(compare_cycle, copy.deepcopy(self.status)))
                self.retry_rollout_at = now + 600
                return
            if self.machine == 'mini':
                observation = self.status['groups']['peer'].get('details', {})
                peer = observation.get('health', {})
                if not peer_canary_ok(self.status, peer, now, observation):
                    return
            rollout['phase'] = 'switching'
            atomic_json(self.root / 'health-status.json', self.status)
            self.transition = ('activate', self.slow.submit(activate, self.root,
                               copy.deepcopy(self.status), Manager(self.machine), now))
        elif rollout['phase'] == 'canary':
            since = rollout.get('activated_at', now)
            failed = any(group.get('error') or any(c.get('execution_error') for c in group.get('checks', []))
                         for group in self.status['groups'].values()
                         if group.get('started_at', 0) >= since)
            if failed:
                rollout['phase'] = 'switching'
                atomic_json(self.root / 'health-status.json', self.status)
                self.transition = ('rollback', self.slow.submit(rollback, self.root,
                                   copy.deepcopy(self.status), Manager(self.machine)))
            elif complete_cycle(self.status, now, since):
                if self.machine == 'mini':
                    observation = self.status['groups']['peer'].get('details', {})
                    peer = observation.get('health', {})
                    if (peer.get('loaded_head') != self.status['loaded_head']
                            or not peer_canary_ok(self.status, peer, now, observation)):
                        return
                rollout.update(phase='active', canary_passed_at=now,
                               last_confirmed_at=now, head=self.status['loaded_head'])
        elif rollout['phase'] == 'active' and complete_cycle(self.status, now):
            rollout['last_confirmed_at'] = now

    def tick(self, monotonic, now):
        for name, (future, started) in list(self.pending.items()):
            if not future.done():
                continue
            try:
                result = validated_result(future.result(), name)
            except Exception as exc:
                result = dict(error=type(exc).__name__, checks=[{
                    'name': name + '_execution', 'passed': False,
                    'execution_error': True, 'message': 'Probe execution failed: ' + type(exc).__name__}])
            result.update(started_at=started, finished_at=now, period=PERIODS[name])
            if name == 'guardian':
                previous = self.status['groups'].get(name, {}).get('details', {}).get('reloads_total', 0)
                details = result.setdefault('details', {})
                details['reloads_total'] = previous + details.get('reloads', 0)
                details['reloads'] = details['reloads_total']
                details['timestamp'] = datetime.fromtimestamp(now, timezone.utc).isoformat()
            self.status['groups'][name] = result
            observe(self.status, self.machine, name, result['checks'], now)
            if execution_succeeded(result):
                # Adapter check names describe health findings, so a later
                # successful execution must explicitly resolve its own old
                # scheduler incident without changing those semantic checks.
                observe(self.status, self.machine, name, [{
                    'name': name + '_execution', 'passed': True,
                    'message': 'Probe execution completed within its deadline'}], now)
            if result.get('repair_attempted'):
                self.status['repair_after'][name] = now + 300
            event('health', 'group_complete', machine=self.machine, group=name,
                  duration=now - started, error=result.get('error'),
                  failed=sum(not c.get('passed') for c in result['checks']))
            del self.pending[name]
        live = self.status['rollout']['phase'] in ('active', 'canary')
        for name in PERIODS:
            if name in self.pending or monotonic < self.due[name]:
                continue
            permit = live and now >= self.status['repair_after'].get(name, 0)
            pool = self.fast if name in ('guardian', 'flow') else self.slow
            self.pending[name] = (pool.submit(execute, name, self.machine, permit), now)
            self.due[name] = monotonic + PERIODS[name]
        self.status['timestamp'] = now
        self.status['in_flight'] = list(self.pending)
        atomic_json(self.root / 'health-status.json', self.status)
        if live or self.status['rollout']['phase'] == 'transition-failed':
            render_alerts(self.root, self.status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--machine', choices=('mini', 'nucbox'), required=True)
    args = parser.parse_args()
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.health-scheduler.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True, timeout=5).strip()
        scheduler = Scheduler(root, args.machine, head)
        stopped = []
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stopped.append(True))
        while not stopped:
            scheduler.tick(time.monotonic(), time.time())
            scheduler.rollout_tick(time.monotonic(), time.time())
            time.sleep(5)
        scheduler.fast.shutdown(wait=True)
        scheduler.slow.shutdown(wait=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
