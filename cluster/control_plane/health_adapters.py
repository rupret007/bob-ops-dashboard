"""Compatibility adapters: keep detailed checks, separate observations/actions."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import guardian, watchdog
from .health_state import authoritative, data_dir, recovery_lock, repair_once
from .node_control import is_draining
from .service_logging import redact

PERIODS = {'guardian': 120, 'watchdog': 600, 'peer': 120,
           'shadow': 900, 'doctor': 3600, 'main_ci': 120, 'flow': 120}
TIMEOUTS = {'guardian': 90, 'watchdog': 180, 'peer': 15,
            'shadow': 600, 'doctor': 120, 'main_ci': 180, 'flow': 20}


def check(name, passed, message='', **kwargs):
    return dict(name=name, passed=passed, message=message, **kwargs)


def collect(group, machine, live=False):
    live = live and not is_draining(machine)
    if group == 'guardian':
        actions = guardian.recover(machine, observe_only=True)
        attempted = False
        if live and actions:
            attempted, recovered = repair_once('registrations', lambda: guardian.recover(machine))
            if attempted:
                actions = recovered
        dashboard = guardian.guard_dashboard(machine, observe_only=True, emit_alert=False)
        if live and not dashboard['ok']:
            repaired, result = repair_once('dashboard', lambda: guardian.guard_dashboard(machine, emit_alert=False))
            attempted = attempted or repaired
            if repaired:
                dashboard = result
        details = dict(machine=machine, actions=actions, dashboard=dashboard,
                       reloads=sum(a['ok'] for a in actions))
        checks = [check('dashboard_http', dashboard['ok'], 'Dashboard HTTP verified' if dashboard['ok'] else 'Dashboard HTTP failed')]
        checks += [check(a['unit'], a['ok'], 'Registration recovery' if live else 'Registration missing') for a in actions]
        return dict(checks=checks, details=details,
                    repair_attempted=attempted)
    if group == 'watchdog':
        result = watchdog.run_watchdog(machine, check_only=True)
        attempted = False
        for item in result.checks:
            if not live or item.passed or item.execution_error:
                continue
            if item.repair_action == 'restart_service':
                service = item.name.split('_')[0]
                if service in watchdog.SERVICE_LOG_FILES:
                    if service == 'dashboard' and guardian.dashboard_responding():
                        continue
                    admitted, repaired = repair_once(service, lambda: watchdog.repair_restart_service(service, machine))
                    attempted = attempted or admitted
                    item.repaired = admitted and repaired
            elif item.repair_action == 'create_labels' and item.name == 'labels_rupret007_webjam':
                admitted, repaired = repair_once(item.name, lambda: watchdog.repair_create_labels('rupret007/webjam'))
                attempted = attempted or admitted
                item.repaired = admitted and repaired
            elif item.repair_action == 'self_update':
                # The normal updater timer performs installation outside this
                # daemon: nested reload while holding recovery ownership deadlocks.
                # Request a run through the existing supervised updater.
                attempted = True
                argv = (['launchctl', 'kickstart', 'gui/{}/com.storypool.selfupdate'.format(os.getuid())]
                        if machine == 'mini' else ['systemctl', '--user', 'start', '--no-block', 'storypool-selfupdate.service'])
                admitted, _ = repair_once('self_update', lambda: guardian.command(argv))
                attempted = attempted or admitted
        return dict(checks=[c.to_dict() for c in result.checks], details=result.to_dict(),
                    repair_attempted=attempted)
    if group == 'doctor':
        result = subprocess.run([sys.executable, '-m', 'cluster.control_plane.smoke'],
                                capture_output=True, text=True, timeout=100)
        return dict(checks=[check('pipeline_smoke', result.returncode == 0,
                                 'Smoke passed' if result.returncode == 0 else 'Pipeline evidence incomplete; inspect diagnostics',
                                 unknown=result.returncode == 1,
                                 execution_error=result.returncode not in (0, 1))],
                    details=dict(exit_code=result.returncode,
                                 diagnostics=redact((result.stdout + result.stderr)[-24000:]),
                                 watchdog_reference='groups.watchdog'))
    if group == 'shadow':
        from .shadow_runner import ShadowRunner
        details = ShadowRunner(machine=machine).run()
        return dict(checks=[check('shadow_evaluation', True, 'Observational simulation completed')], details=details)
    if group == 'peer':
        watchdog.sync_peer_health(machine)
        return collect_peer(machine)
    if group == 'main_ci':
        if not live:
            return dict(checks=[check('main_ci_followup', True, 'Legacy owner retains main-CI followup during observation')], details={})
        from .main_ci import reconcile_batch
        details = reconcile_batch(data_dir())
        failed = any(r['main_ci'] == 'failure' for r in details['results'])
        return dict(checks=[check('main_ci_followup', not failed and not details['unconfirmed'],
                                 'Follow-up observed {} merges; {} unconfirmed'.format(
                                     details['total'], details['unconfirmed']),
                                 unknown=bool(details['unconfirmed']) and not failed)], details=details)
    if group == 'flow':
        from .dashboard.tachometer import EngineCollector
        from .dashboard.engine_flow import flow_reasons
        now = time.time()
        engine = EngineCollector(data_dir(), Path('~/.local/share/karen').expanduser(), machine).collect()
        local = engine['flow_local']
        peers = {}
        peer = 'mini' if machine == 'nucbox' else 'nucbox'
        try:
            value = json.loads((data_dir() / 'machines' / peer / 'engine.json').read_text())
            if 0 <= now - __import__('datetime').datetime.fromisoformat(value['synced_at']).timestamp() < 900:
                peers[peer] = value
        except (OSError, ValueError, KeyError, TypeError):
            pass
        reasons = flow_reasons(local, peers, now)
        return dict(checks=[check('work_flowing', not reasons, '; '.join(reasons), unknown=bool(reasons))], details=local)
    raise ValueError('Unknown health adapter')


def collect_peer(machine):
    """Keep direct source identity/time; never repeat a derived cluster verdict."""
    import secrets
    import urllib.request
    from .health_peer import direct_observation
    peer = 'mini' if machine == 'nucbox' else 'nucbox'
    nonce = secrets.token_hex(16)
    started = time.monotonic()
    request = urllib.request.Request(watchdog.PEER_URLS[peer] + '/api/health?nonce=' + nonce,
                                     headers={'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=5) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    elapsed, received = time.monotonic() - started, time.time()
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('Peer response exceeds limit')
    value = json.loads(raw)
    details = direct_observation(value, peer, nonce, received, elapsed)
    return dict(checks=[check('peer_freshness', True, 'Direct peer report verified')],
                details=details)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('group', choices=PERIODS)
    parser.add_argument('--machine', choices=('mini', 'nucbox'), required=True)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    os.environ['STORYPOOL_HEALTH_WORKER'] = '1'
    with contextlib.redirect_stdout(sys.stderr):
        # Observation never holds recovery ownership while waiting on APIs.
        # Each local action rechecks authority and maintenance under its lock.
        result = collect(args.group, args.machine, args.live and authoritative())
    print(json.dumps(result))


if __name__ == '__main__':
    main()
