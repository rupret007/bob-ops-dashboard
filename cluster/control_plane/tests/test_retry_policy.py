"""Retry regressions use isolated state and a real temporary JetStream server."""
import asyncio
import json
import time
from unittest.mock import patch, MagicMock

import pytest

from cluster.control_plane import fix_worker, dispatcher
from cluster.control_plane.coordination import ClaimClient, ClaimLost, CoordinationError
from cluster.control_plane.job_ledger import JobLedger
from cluster.control_plane.dashboard.tachometer import EngineCollector
from cluster.tests.nats_test_util import EphemeralNats


def test_missing_lockfile_retains_real_ledger_reason_and_never_calls_model(tmp_path):
    ledger = JobLedger(tmp_path / 'jobs')
    job = ledger.create_job('owner/repo', 7, 'a' * 40, 'codex', 'nucbox')
    lease = MagicMock()
    lease.client.retry_state.return_value = {'attempts': 1, 'retry_after': 0, 'code': 'transient'}
    def command(args, cwd=None, **kwargs):
        if args[:3] == ['gh', 'pr', 'view']:
            return json.dumps(dict(headRefName='fix/one', headRefOid='a' * 40,
                                   isCrossRepository=False, state='OPEN'))
        if args[:3] == ['git', 'worktree', 'add']:
            tree = __import__('pathlib').Path(args[-2]); tree.mkdir(parents=True)
            (tree / 'package.json').write_text(json.dumps({'scripts': {'test': 'jest'}}))
        assert args[0] != 'codex', 'Model must never run without a lockfile'
        return ''
    with patch.object(fix_worker, 'get_ledger', return_value=ledger), \
         patch('cluster.control_plane.node_control.is_draining', return_value=False), \
         patch('cluster.control_plane.coordination.Lease', return_value=lease), \
         patch.object(ClaimClient, 'from_env'), \
         patch.object(fix_worker, '_find_codex', return_value='codex'), \
         patch.object(fix_worker, 'event'), \
         patch.object(fix_worker, 'findings_for_pr', return_value=['repair']), \
         patch.object(fix_worker, 'run', side_effect=command):
        assert fix_worker.execute(job.job_id) == 1
    saved = ledger._load_jobs()[0]
    assert saved.state == 'failed'
    assert 'package-lock.json' in saved.error
    assert saved.failure_code == 'missing-lockfile' and saved.retryable is False
    assert lease.client.retry_state.call_args.args[1]['code'] == 'missing-lockfile'


@pytest.mark.parametrize('code', ['missing-lockfile', 'protected-pr', 'head-changed', 'unsupported-tests'])
def test_all_deterministic_codes_block_forever_at_same_head(code):
    from cluster.control_plane.retry_policy import reserve
    state, reason = reserve(dict(attempts=1, code=code, retry_after=0), now=10**12)
    assert state is None and reason.startswith('blocked:')


def test_transient_backoff_cap_and_fresh_sha_budget():
    from cluster.control_plane.retry_policy import reserve
    state, reason = reserve(now=100)
    assert state['attempts'] == 1 and not reason
    assert reserve(state, now=101)[0] is None
    state, _ = reserve(state, now=state['retry_after'])
    assert state['attempts'] == 2 and state['retry_after'] == 1000
    state, _ = reserve(state, now=1000)
    assert state['attempts'] == 3
    assert 'retry limit' in reserve(state, now=100000)[1]
    assert reserve(None, now=100000)[0]['attempts'] == 1
    assert 'retry limit' in reserve(None, historical_attempts=8)[1]


@pytest.fixture
def client():
    server = EphemeralNats(); server.start()
    client = ClaimClient(server.url, ttl=2)
    client.call('bootstrap', bootstrap=True)
    try:
        yield client
    finally:
        server.stop()


def test_retry_state_survives_claim_ttl_and_node_change_and_new_head_is_fresh(client):
    claim = client.acquire('owner/repo', 7, 'a' * 40, 'mini')
    value = dict(attempts=1, retry_after=0, code='missing-lockfile')
    assert client.retry_state(claim) is None
    client.retry_state(claim, value)
    client.call('release', claim)
    time.sleep(2.2)
    successor = client.acquire('owner/repo', 7, 'a' * 40, 'nucbox')
    assert client.retry_state(successor) == value
    client.call('release', successor)
    fresh = client.acquire('owner/repo', 7, 'b' * 40, 'nucbox')
    assert client.retry_state(fresh) is None
    with pytest.raises(ClaimLost):
        client.retry_state(claim, value)


def test_retry_store_outage_stops_dispatch_without_job(tmp_path):
    worker = dispatcher.Dispatcher('nucbox', tmp_path / 'log', job_ledger=JobLedger(tmp_path / 'jobs'))
    client = MagicMock()
    client.retry_state.side_effect = CoordinationError('Durable retry coordination unavailable; new fixes paused')
    client.acquire.return_value = {'sha': 'a' * 40}
    with patch.object(worker, 'check_machine_guards', return_value=(True, 'ok', {})), \
         patch.object(worker, 'select_provider', return_value='codex'), \
         patch.object(dispatcher, 'REPOS', ['owner/repo']), \
         patch.object(dispatcher, '_get_open_prs', return_value=[dict(number=7, headRefOid='a' * 40)]), \
         patch.object(dispatcher, '_get_active_lanes', return_value={}), \
         patch.object(fix_worker, 'findings_for_pr', return_value=['repair']), \
         patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(dispatcher, '_spawn_worker') as spawn:
        results = worker.evaluate(live=True)
    spawn.assert_not_called()
    assert worker.ledger._load_jobs() == []
    assert 'paused' in results[0].reason
    assert client.call.call_args.args[0] == 'release'


def test_second_machine_skips_blocked_sha_and_dashboard_explains_why(client, tmp_path):
    claim = client.acquire('owner/repo', 7, 'a' * 40, 'mini')
    assert client.retry_state(claim) is None
    client.retry_state(claim, dict(attempts=1, retry_after=0, code='missing-lockfile'))
    client.call('release', claim)
    worker = dispatcher.Dispatcher('nucbox', tmp_path / 'dispatch', job_ledger=JobLedger(tmp_path / 'jobs'))
    with patch.object(worker, 'check_machine_guards', return_value=(True, 'ok', {})), \
         patch.object(worker, 'select_provider', return_value='codex'), \
         patch.object(dispatcher, 'REPOS', ['owner/repo']), \
         patch.object(dispatcher, '_get_open_prs', return_value=[dict(number=7, headRefOid='a' * 40)]), \
         patch.object(dispatcher, '_get_active_lanes', return_value={}), \
         patch.object(fix_worker, 'findings_for_pr', return_value=['repair']), \
         patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(dispatcher, '_spawn_worker') as spawn:
        decisions = worker.evaluate(live=True)
    spawn.assert_not_called()
    assert decisions[0].action == 'skip_blocked'
    assert 'package-lock.json' in decisions[0].reason
    result = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()
    assert len(result['blocked_fixes']) == 1
    assert 'package-lock.json' in result['blocked_fixes'][0]['reason']
    from cluster.control_plane.dashboard.server import render_dashboard
    from cluster.control_plane.dashboard.data_collector import DashboardData
    page = render_dashboard(DashboardData(timestamp='', machine='nucbox', live_mode=True))
    assert 'Fixes needing attention' in page and 'engine-blocked' in page
    assert "b.reason" in page and "el('li',null" in page


def test_retry_record_compare_and_swap_rejects_a_late_writer(client):
    claim = client.acquire('owner/repo', 7, 'a' * 40, 'mini')
    assert client.retry_state(claim) is None
    state = dict(attempts=1, retry_after=0, code='transient')
    client.retry_state(claim, state)
    stale = dict(claim)
    client.retry_state(claim, dict(state, code='missing-lockfile'))
    with pytest.raises(CoordinationError):
        asyncio.run(client._retry_operation('put', stale, state))
    assert client.retry_state(claim)['code'] == 'missing-lockfile'


def test_dashboard_shows_fixed_reason_and_clears_it_for_a_newer_head(tmp_path):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    ledger = tmp_path / 'jobs' / 'ledger.jsonl'; ledger.parent.mkdir()
    blocked = dict(job_id='a', repo='owner/repo', pr_number=7, head_sha='a'*40,
        machine='nucbox', state='failed', retryable=False,
        error='blocked: needs package-lock.json for reproducible tests',
        created_at=now.isoformat(), end_time=now.isoformat())
    ledger.write_text(json.dumps(blocked)+'\n')
    first = EngineCollector(tmp_path, tmp_path, 'nucbox').collect()
    assert first['blocked_fixes'][0]['reason'] == blocked['error']
    newer = dict(blocked, job_id='b', head_sha='b'*40, state='running', retryable=True,
                 created_at=(now + timedelta(seconds=1)).isoformat())
    ledger.write_text(json.dumps(blocked)+'\n'+json.dumps(newer)+'\n')
    assert EngineCollector(tmp_path, tmp_path, 'nucbox').collect()['blocked_fixes'] == []


@pytest.mark.parametrize('change,code', [
    ({'headRefName': 'main'}, 'protected-pr'),
    ({'headRefOid': 'b'*40}, 'head-changed'),
    ({'state': 'CLOSED'}, 'protected-pr'),
    ({'isCrossRepository': True}, 'protected-pr'),
])
def test_actual_pr_preflights_are_nonretryable_before_checkout(tmp_path, change, code):
    from cluster.control_plane.retry_policy import PreflightFailure
    pr = dict(headRefName='fix/one', headRefOid='a'*40, state='OPEN', isCrossRepository=False)
    pr.update(change)
    with patch.object(fix_worker, '_find_codex', return_value='codex'), \
         patch.object(fix_worker, 'run', return_value=json.dumps(pr)) as command:
        with pytest.raises(PreflightFailure) as error:
            fix_worker.fix_pr('owner/repo', 7, 'a'*40, ['repair'])
    assert error.value.code == code and command.call_count == 1


def test_unsupported_test_command_is_nonretryable(tmp_path):
    from cluster.control_plane.retry_policy import PreflightFailure
    with pytest.raises(PreflightFailure) as error:
        fix_worker.test_command(tmp_path)
    assert error.value.code == 'unsupported-tests'


def test_verified_local_fix_blocks_redispatch_when_durable_terminal_write_was_lost(tmp_path):
    ledger = JobLedger(tmp_path / 'jobs')
    job = ledger.create_job('owner/repo', 7, 'a' * 40, 'codex', 'nucbox')
    ledger.complete_job(job.job_id, 'commit_pushed', 'b' * 40, verified=True)
    worker = dispatcher.Dispatcher('nucbox', tmp_path / 'dispatch', job_ledger=ledger)
    client = MagicMock()
    client.retry_state.return_value = dict(attempts=1, retry_after=0, code='transient')
    with patch.object(worker, 'check_machine_guards', return_value=(True, 'ok', {})), \
         patch.object(worker, 'select_provider', return_value='codex'), \
         patch.object(dispatcher, 'REPOS', ['owner/repo']), \
         patch.object(dispatcher, '_get_open_prs', return_value=[dict(number=7, headRefOid='a' * 40)]), \
         patch.object(dispatcher, '_get_active_lanes', return_value={}), \
         patch.object(fix_worker, 'findings_for_pr', return_value=['repair']), \
         patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(dispatcher, '_spawn_worker') as spawn:
        decisions = worker.evaluate(live=True)
    spawn.assert_not_called()
    assert len(ledger._load_jobs()) == 1
    assert decisions[0].action == 'skip_blocked'
    assert 'verified fix' in decisions[0].reason


@pytest.mark.parametrize('age_minutes', [10, 2880])
def test_dashboard_block_persists_after_logs_roll_over_and_new_head_clears_it(tmp_path, age_minutes):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    worker = dispatcher.Dispatcher('nucbox', tmp_path / 'dispatch', job_ledger=JobLedger(tmp_path / 'jobs'))
    old = now - timedelta(minutes=age_minutes)
    decision = dispatcher.DispatchDecision(timestamp=old.isoformat(), action='skip_blocked',
        reason='blocked: needs package-lock.json', machine='nucbox', live=True,
        repo='owner/repo', pr_number=7, head_sha='a' * 40)
    worker._write_decision(decision)
    # Remove all log evidence: the projection must still explain the block.
    for log in (tmp_path / 'dispatch').glob('*.jsonl'):
        log.unlink()
    assert EngineCollector(tmp_path, tmp_path, 'nucbox').collect()['blocked_fixes'][0]['reason'] == decision.reason
    # Restore the stale row: a later full-head observation must beat it too.
    worker._write_decision(decision)
    worker._write_decision(dispatcher.DispatchDecision(timestamp=now.isoformat(), action='skip',
        reason='No Karen findings available', machine='nucbox', live=True,
        repo='owner/repo', pr_number=7, head_sha='b' * 40))
    assert EngineCollector(tmp_path, tmp_path, 'nucbox').collect()['blocked_fixes'] == []


@pytest.mark.parametrize('corruption', [
    {}, {'repo': ['owner/repo']}, {'pr_number': []}, {'pr_number': True},
    {'pr_number': 0}, {'head_sha': None}, {'head_sha': 'unknown'},
    {'reason': {'unsafe': 'shape'}}, {'timestamp': 'not-a-time'},
])
def test_dashboard_ignores_corrupt_block_projection_without_hiding_valid_blocks(tmp_path, corruption):
    from datetime import datetime, timezone
    directory = tmp_path / 'dispatch' / 'blocked-fixes'
    directory.mkdir(parents=True)
    valid = dict(repo='owner/repo', pr_number=7, head_sha='a' * 40,
                 reason='blocked: needs package-lock.json', timestamp=datetime.now(timezone.utc).isoformat())
    (directory / 'valid.json').write_text(json.dumps(valid))
    invalid = {} if not corruption else dict(valid, **corruption)
    (directory / 'corrupt.json').write_text(json.dumps(invalid))
    (directory / 'invalid-json.json').write_text('{broken')
    assert EngineCollector(tmp_path, tmp_path, 'nucbox').collect()['blocked_fixes'] == [valid]
