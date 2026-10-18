"""Real JetStream races between separate machine ledgers, never live state."""
import concurrent.futures
import json
import os
import sys
import threading
import time
from unittest.mock import patch

import pytest

from cluster.control_plane.coordination import ClaimClient, ClaimBusy, ClaimLost, CoordinationError, Lease
from cluster.control_plane import dispatcher, fix_worker
from cluster.control_plane.job_ledger import JobLedger
from cluster.tests.nats_test_util import EphemeralNats


@pytest.fixture
def client():
    server = EphemeralNats()
    server.start()
    client = ClaimClient(server.url, ttl=2)
    client.call('bootstrap', bootstrap=True)
    try:
        yield client
    finally:
        server.stop()


def test_two_machines_exactly_one_claim_even_for_different_heads(client):
    barrier = threading.Barrier(2)
    def claim(machine):
        barrier.wait()
        try:
            return client.acquire('owner/repo', 7, ('a' if machine == 'mini' else 'b') * 40, machine)
        except ClaimBusy:
            return None
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(claim, ['mini', 'nucbox']))
    assert sum(r is not None for r in results) == 1


def test_expired_owner_cannot_renew_or_delete_successor(client):
    old = client.acquire('owner/repo', 7, 'a' * 40, 'mini')
    deadline = time.monotonic() + 6
    while True:
        try:
            new = client.acquire('owner/repo', 7, 'b' * 40, 'nucbox')
            break
        except ClaimBusy:
            assert time.monotonic() < deadline, 'Expired claim was not recoverable'
            time.sleep(0.2)
    for operation in ['renew', 'release']:
        with pytest.raises(ClaimLost):
            client.call(operation, old)
    client.call('renew', new)


def test_heartbeat_extends_lease_and_release_allows_next_owner(client):
    claim = client.acquire('owner/repo', 7, 'a' * 40, 'mini')
    with Lease(client, claim):
        time.sleep(2.2)
        with pytest.raises(ClaimBusy):
            client.acquire('owner/repo', 7, 'a' * 40, 'nucbox')
    assert client.acquire('owner/repo', 7, 'a' * 40, 'nucbox')


def test_lost_lease_fences_next_command(client):
    claim = client.acquire('owner/repo', 7, 'a' * 40, 'mini')
    with Lease(client, claim) as lease:
        client.call('release', lease.claim)
        token = fix_worker._lease.set(lease)
        try:
            with patch.object(fix_worker.subprocess, 'run') as command:
                with pytest.raises(CoordinationError):
                    fix_worker.run(['git', 'push'])
                command.assert_not_called()
        finally:
            fix_worker._lease.reset(token)


def test_claim_loss_terminates_running_command_group(tmp_path):
    marker = tmp_path / 'must-not-exist'
    class LostLease:
        lost = False
    lease = LostLease()
    timer = threading.Timer(0.15, lambda: setattr(lease, 'lost', True))
    timer.start()
    try:
        with pytest.raises(ClaimLost):
            fix_worker._run_claimed([sys.executable, '-c',
                'import time,pathlib; time.sleep(2); pathlib.Path(' + repr(str(marker)) + ').touch()'], None, lease)
    finally:
        timer.join()
    time.sleep(1.2)
    assert not marker.exists()


def dispatch_once(machine, tmp_path):
    ledger = JobLedger(tmp_path / machine)
    worker = dispatcher.Dispatcher(machine, tmp_path / ('logs-' + machine), job_ledger=ledger)
    worker.check_machine_guards = lambda: (True, 'ok', {})
    worker.select_provider = lambda *args: 'codex'
    return worker, ledger


def test_two_dispatchers_separate_ledgers_create_one_job(client, tmp_path):
    workers = [dispatch_once(name, tmp_path) for name in ['mini', 'nucbox']]
    barrier = threading.Barrier(2)
    def evaluate(pair):
        barrier.wait()
        return pair[0].evaluate(live=True)
    with patch.object(dispatcher, 'REPOS', ['owner/repo']), \
         patch.object(dispatcher, '_get_open_prs', return_value=[{'number': 7, 'headRefOid': 'a' * 40}]), \
         patch.object(dispatcher, '_get_active_lanes', return_value={}), \
         patch.object(fix_worker, 'findings_for_pr', return_value=['fix a regression']), \
         patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(dispatcher, '_spawn_worker', return_value=0) as spawn:
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(evaluate, workers))
    assert spawn.call_count == 1
    jobs = [j for _, ledger in workers for j in ledger._load_jobs()]
    assert len(jobs) == 1
    assert jobs[0].cluster_claim['sha'] == 'a' * 40
    assert sum(d.action == 'dispatch' for result in results for d in result) == 1


@pytest.mark.parametrize('configured', [False, True])
def test_outage_or_missing_configuration_stops_dispatch(tmp_path, monkeypatch, configured):
    monkeypatch.delenv('STORYPOOL_COORD_URL', raising=False)
    if configured:
        monkeypatch.setenv('STORYPOOL_COORD_URL', 'nats://127.0.0.1:1')
    worker, ledger = dispatch_once('nucbox', tmp_path)
    with patch.object(dispatcher, 'REPOS', ['owner/repo']), \
         patch.object(dispatcher, '_get_open_prs', return_value=[{'number': 7, 'headRefOid': 'a' * 40}]), \
         patch.object(dispatcher, '_get_active_lanes', return_value={}), \
         patch.object(fix_worker, 'findings_for_pr', return_value=['fix']), \
         patch.object(dispatcher, '_spawn_worker') as spawn:
        decisions = worker.evaluate(live=True)
    spawn.assert_not_called()
    assert ledger._load_jobs() == []
    assert decisions[0].action == 'dispatch_failed'
    assert 'paused' in decisions[0].reason


def test_spawn_failure_releases_cluster_claim(client, tmp_path):
    worker, ledger = dispatch_once('nucbox', tmp_path)
    with patch.object(dispatcher, 'REPOS', ['owner/repo']), \
         patch.object(dispatcher, '_get_open_prs', return_value=[{'number': 7, 'headRefOid': 'a' * 40}]), \
         patch.object(dispatcher, '_get_active_lanes', return_value={}), \
         patch.object(fix_worker, 'findings_for_pr', return_value=['fix']), \
         patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(dispatcher, '_spawn_worker', side_effect=OSError('manager unavailable')):
        worker.evaluate(live=True)
    assert ledger._load_jobs()[0].state == 'failed'
    assert client.acquire('owner/repo', 7, 'a' * 40, 'mini')


def test_no_secret_details_in_connection_error():
    credential_fixture = 'synthetic-private-value'
    client = ClaimClient('nats://127.0.0.1:1', password=credential_fixture)
    with pytest.raises(CoordinationError) as error:
        client.acquire('owner/repo', 7, 'a' * 40, 'mini')
    assert 'synthetic-private-value' not in str(error.value)


@pytest.mark.parametrize('repo,sha', [('rupret007/rad-dad-show-night', 'a' * 40), ('owner/repo', 'unknown')])
def test_never_touch_and_unpinned_heads_never_contact_broker(repo, sha):
    with patch.object(ClaimClient, 'call') as call:
        with pytest.raises(CoordinationError):
            ClaimClient('unused').acquire(repo, 7, sha, 'mini')
    call.assert_not_called()


def test_missing_claim_worker_never_launches_provider(tmp_path, monkeypatch):
    worker, ledger = dispatch_once('nucbox', tmp_path)
    job = ledger.create_job('owner/repo', 7, 'a' * 40, 'codex', 'nucbox')
    monkeypatch.setenv('STORYPOOL_COORD_URL', 'nats://127.0.0.1:1')
    with patch.object(fix_worker, 'get_ledger', return_value=ledger), \
         patch.object(fix_worker, 'fix_pr') as fix:
        assert fix_worker.execute(job.job_id) == 1
    fix.assert_not_called()
    assert ledger._load_jobs()[0].state == 'failed'
