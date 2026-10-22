"""Both doctors share one repair lease before spending or publishing."""
import concurrent.futures
import threading
from unittest.mock import patch

import pytest

from cluster.control_plane import ops_doctor as doctor
from cluster.control_plane.coordination import ClaimClient, ClaimBusy, CoordinationError
from cluster.control_plane.tests.test_coordination import client


def test_task_claims_are_global_across_heads_and_recover_on_release(client):
    first = client.acquire_task(doctor.REPO, 'ops-doctor:' + doctor.BRANCH, 'a' * 40, 'mini')
    assert first['task'] == 'ops-doctor:' + doctor.BRANCH
    assert first['sha'] == 'a' * 40
    with pytest.raises(ClaimBusy):
        client.acquire_task(doctor.REPO, 'ops-doctor:' + doctor.BRANCH, 'b' * 40, 'nucbox')
    client.call('release', first)
    assert client.acquire_task(doctor.REPO, 'ops-doctor:' + doctor.BRANCH, 'b' * 40, 'nucbox')


def test_two_doctors_only_one_repair_and_budget_reservation(client, tmp_path):
    entered, finish = threading.Event(), threading.Event()
    def paid_repair(*args):
        entered.set()
        assert finish.wait(5)
        return 'https://github.com/owner/repo/pull/1'
    for name in ('mini', 'nucbox'):
        (tmp_path / name).mkdir()
    with patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(doctor, 'run', return_value='a' * 40 + '\trefs/heads/' + doctor.BRANCH), \
         patch.object(doctor, 'reserve_attempt', return_value=True) as reserve, \
         patch.object(doctor, '_repair_claimed', side_effect=paid_repair) as repair:
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            first = pool.submit(doctor.repair, tmp_path, tmp_path / 'mini/doctor.log', 'first')
            try:
                assert entered.wait(3)
                second = pool.submit(doctor.repair, tmp_path, tmp_path / 'nucbox/doctor.log', 'second')
                with pytest.raises(ClaimBusy):
                    second.result(3)
            finally:
                finish.set()
            assert first.result(3).endswith('/pull/1')
    assert repair.call_count == reserve.call_count == 1


def test_doctor_missing_configuration_never_spends_or_clones(tmp_path):
    with patch.object(ClaimClient, 'from_env', side_effect=CoordinationError('unavailable')), \
         patch.object(doctor, 'reserve_attempt') as reserve, \
         patch.object(doctor, 'run') as command:
        with pytest.raises(CoordinationError):
            doctor.repair(tmp_path, tmp_path / 'doctor.log', 'test')
    reserve.assert_not_called()
    command.assert_not_called()


def test_doctor_outage_never_spends_or_repairs(tmp_path):
    with patch.object(ClaimClient, 'from_env', return_value=ClaimClient('nats://127.0.0.1:1')), \
         patch.object(doctor, 'run', return_value='a' * 40 + '\trefs/heads/' + doctor.BRANCH), \
         patch.object(doctor, 'reserve_attempt') as reserve, \
         patch.object(doctor, '_repair_claimed') as repair:
        with pytest.raises(CoordinationError):
            doctor.repair(tmp_path, tmp_path / 'doctor.log', 'test')
    reserve.assert_not_called()
    repair.assert_not_called()


def test_doctor_lost_claim_cannot_push(client, tmp_path):
    from cluster.control_plane import fix_worker
    def lose_then_push(*args):
        lease = fix_worker._lease.get()
        assert lease is not None
        client.call('release', lease.claim)
        return fix_worker.run(['git', 'push'])
    with patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(doctor, 'run', return_value='a' * 40 + '\trefs/heads/' + doctor.BRANCH), \
         patch.object(doctor, 'reserve_attempt', return_value=True), \
         patch.object(doctor, '_repair_claimed', side_effect=lose_then_push), \
         patch.object(fix_worker.subprocess, 'Popen') as command:
        with pytest.raises(CoordinationError):
            doctor.repair(tmp_path, tmp_path / 'doctor.log', 'test')
    command.assert_not_called()
    assert fix_worker._lease.get() is None


@pytest.mark.parametrize('repo,task,sha', [
    ('rupret007/rad-dad-show-night', 'ops-doctor', 'a' * 40),
    ('owner/repo', 'ops-doctor', 'unknown'),
    ('owner/repo', '', 'a' * 40),
    ('owner/repo', 'invalid task\nname', 'a' * 40),
])
def test_invalid_task_identity_never_contacts_broker(repo, task, sha):
    with patch.object(ClaimClient, 'call') as operation:
        with pytest.raises(CoordinationError):
            ClaimClient('unused').acquire_task(repo, task, sha, 'mini')
    operation.assert_not_called()


def test_budget_limit_releases_task_without_provider(client, tmp_path):
    with patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(doctor, 'run', return_value='a' * 40 + '\trefs/heads/' + doctor.BRANCH), \
         patch.object(doctor, 'reserve_attempt', return_value=False), \
         patch.object(doctor, '_repair_claimed') as repair:
        with pytest.raises(doctor.RepairLimit):
            doctor.repair(tmp_path, tmp_path / 'doctor.log', 'test')
    repair.assert_not_called()
    assert client.acquire_task(doctor.REPO, 'ops-doctor:' + doctor.BRANCH, 'a' * 40, 'mini')


def test_busy_doctor_defers_without_failed_service_or_jeff_spam(tmp_path, monkeypatch):
    from subprocess import CompletedProcess
    monkeypatch.setenv('STORYPOOL_DOCTOR_DIR', str(tmp_path))
    with patch('cluster.control_plane.node_control.is_draining', return_value=False), \
         patch.object(doctor.subprocess, 'run', return_value=CompletedProcess([], 1)), \
         patch.object(doctor, 'repair', side_effect=ClaimBusy('Task already claimed')), \
         patch.object(doctor, 'escalate') as escalate:
        assert doctor.main([]) == 0
    escalate.assert_not_called()
    assert 'REPAIR DEFERRED: Task already claimed' in next(tmp_path.glob('*.log')).read_text()


def test_task_claim_expires_and_stale_owner_cannot_release_successor(client):
    import time
    from cluster.control_plane.coordination import ClaimLost
    task = 'ops-doctor:' + doctor.BRANCH
    old = client.acquire_task(doctor.REPO, task, 'a' * 40, 'mini')
    deadline = time.monotonic() + 6
    while True:
        try:
            successor = client.acquire_task(doctor.REPO, task, 'b' * 40, 'nucbox')
            break
        except ClaimBusy:
            assert time.monotonic() < deadline, 'Expired doctor claim was not recovered'
            time.sleep(0.2)
    for operation in ('renew', 'release'):
        with pytest.raises(ClaimLost):
            client.call(operation, old)
    assert client.call('renew', successor)['task'] == task


@pytest.mark.parametrize('remote', ['', 'unknown\trefs/heads/' + doctor.BRANCH,
                                    'a' * 40 + '\trefs/heads/main'])
def test_unpinned_doctor_base_never_reserves_or_repairs(client, tmp_path, remote):
    with patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(doctor, 'run', return_value=remote), \
         patch.object(client, 'call') as broker, \
         patch.object(doctor, 'reserve_attempt') as reserve, \
         patch.object(doctor, '_repair_claimed') as repair:
        with pytest.raises(CoordinationError):
            doctor.repair(tmp_path, tmp_path / 'doctor.log', 'test')
    broker.assert_not_called()
    reserve.assert_not_called()
    repair.assert_not_called()
