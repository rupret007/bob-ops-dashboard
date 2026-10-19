"""Compatibility adapters: keep detailed checks, separate observations/actions."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
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


def collect(group, machine, live=False, *, expected_head=None):
    live = live and not is_draining(machine)
    if group == 'guardian':
        observations = []
        actions = guardian.recover(machine, observe_only=True, observations=observations)
        attempted = False
        if live and actions:
            recovered_observations = []
            attempted, recovered = repair_once('registrations', lambda: guardian.recover(
                machine, observations=recovered_observations))
            if attempted:
                actions = recovered
                observations = recovered_observations
        dashboard = guardian.guard_dashboard(machine, observe_only=True, emit_alert=False)
        if live and not dashboard['ok']:
            repaired, result = repair_once('dashboard', lambda: guardian.guard_dashboard(machine, emit_alert=False))
            attempted = attempted or repaired
            if repaired:
                dashboard = result
        details = dict(machine=machine, actions=actions, dashboard=dashboard,
                       reloads=sum(a['ok'] for a in actions))
        checks = [check('dashboard_http', dashboard['ok'], 'Dashboard HTTP verified' if dashboard['ok'] else 'Dashboard HTTP failed')]
        checks += [check(item['unit'], item['ok'], item['message'],
                         unknown=item.get('unknown', False)) for item in observations]
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
        if machine == 'mini':
            return peer_pipeline_doctor(expected_head)
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


def peer_pipeline_doctor(expected_head):
    """Observe Nuc's own pipeline without depending on its whole health cycle.

    Mini owns reviews rather than Codex fix lanes. Its local service checks stay
    in watchdog; source admission still separately requires the complete Nuc
    canary. Never use a disk cache or repeat another derived cluster verdict.
    """
    from .health_peer import number, observation_time
    from .health_rollout import positive_group
    details = dict(pipeline_scope='nucbox-local-pipeline',
                   watchdog_reference='groups.watchdog')

    def unqualified(reason):
        return dict(checks=[check('pipeline_smoke', False,
            'Nuc pipeline evidence incomplete; direct current-source proof required',
            unknown=True, execution_error=False)], details=dict(details, reason=reason))

    if not isinstance(expected_head, str) or not re.fullmatch(r'[0-9a-f]{40}', expected_head):
        return unqualified('scheduler-source-unavailable')
    try:
        observation = collect_peer('mini')['details']
        if not isinstance(observation.get('clock_observation'), dict):
            return unqualified('peer-source-unqualified')
        now = observation_time(observation, time.time())
        served = observation['clock_observation']['served_at']
        peer = observation['health']
        rollout = peer['rollout']
        timestamp, activated = peer.get('timestamp'), rollout.get('activated_at')
        if (observation.get('machine') != 'nucbox'
                or type(peer.get('schema')) is not int or peer['schema'] != 1
                or peer.get('machine') != 'nucbox'
                or peer.get('loaded_head') != expected_head
                or rollout.get('head') != expected_head
                or rollout.get('phase') not in ('canary', 'active')
                or not all(number(value) for value in (timestamp, activated))
                or not 0 < activated <= served <= now
                or not 0 <= now - timestamp <= 150
                or not 0 <= served - timestamp <= 150):
            return unqualified('peer-source-unqualified')
        doctor = peer['groups']['doctor']
        if (not positive_group(doctor, 'doctor', now, activated)
                or doctor['finished_at'] > timestamp
                or len(doctor['checks']) != 1
                or doctor['checks'][0]['name'] != 'pipeline_smoke'
                or type(doctor.get('details', {}).get('exit_code')) is not int
                or doctor['details']['exit_code'] != 0):
            return unqualified('peer-doctor-unqualified')
        details.update(source_machine='nucbox', source_head=expected_head,
                       source_started_at=doctor['started_at'],
                       source_finished_at=doctor['finished_at'],
                       source_activated_at=activated, clock_mode='direct')
        return dict(checks=[check('pipeline_smoke', True,
            'Direct Nuc pipeline smoke verified for this source',
            unknown=False, execution_error=False)], details=details)
    except Exception:
        return unqualified('peer-observation-unavailable')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('group', choices=PERIODS)
    parser.add_argument('--machine', choices=('mini', 'nucbox'), required=True)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--expected-head', help='Full source SHA loaded by the parent health scheduler')
    args = parser.parse_args()
    os.environ['STORYPOOL_HEALTH_WORKER'] = '1'
    with contextlib.redirect_stdout(sys.stderr):
        # Observation never holds recovery ownership while waiting on APIs.
        # Each local action rechecks authority and maintenance under its lock.
        result = collect(args.group, args.machine, args.live and authoritative(),
                         expected_head=args.expected_head)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
