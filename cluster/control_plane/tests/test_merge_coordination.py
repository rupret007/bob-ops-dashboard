"""Merge admission uses the same cluster PR claim and rechecks Karen under it."""
import concurrent.futures
import subprocess
import threading
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane.tests.test_coordination import client
from cluster.control_plane.coordination import ClaimClient, CoordinationError
from cluster.control_plane import conductor as c


def setup_runner(tmp_path):
    runner = c.Conductor(log_dir=tmp_path/'logs', verdict_dir=tmp_path/'verdicts',
                         budget_tracker=Mock(), metrics_tracker=Mock())
    runner._record_merge = Mock()
    runner._check_main_ci_after_merge = Mock(return_value=(True, 'green'))
    runner._record_ci_result = Mock()
    return runner


def action():
    return c.ValidatedAction(c.ProposedAction('merge_pr', repo='owner/repo',
                             pr_number=7, head_sha='a'*40), True, 'Previously validated')


def test_two_conductors_only_one_merge_command(client, tmp_path):
    runners = [setup_runner(tmp_path/name) for name in ['mini', 'nucbox']]
    for runner in runners:
        runner.validate_action = Mock(side_effect=lambda proposal, _: c.ValidatedAction(proposal, True, 'fresh gate'))
    entered, finish = threading.Event(), threading.Event()
    def merge(*args, **kwargs):
        entered.set()
        assert finish.wait(5)
        return subprocess.CompletedProcess(args, 0)
    with patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(c.subprocess, 'run', side_effect=merge) as command, \
         patch.object(c.time, 'sleep'):
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            first = pool.submit(runners[0].execute_action, action(), True)
            try:
                assert entered.wait(3)
                second = pool.submit(runners[1].execute_action, action(), True).result(3)
                assert not second.executed
            finally:
                finish.set()
            assert first.result(3).executed
    assert command.call_count == 1
    assert '--match-head-commit' in command.call_args.args[0]
    assert command.call_args.args[0][-1] == 'a' * 40
    assert client.acquire('owner/repo', 7, 'a'*40, 'mini')


@pytest.mark.parametrize('blocked', ['outage', 'review-active'])
def test_coordination_failure_or_review_claim_blocks_merge(client, tmp_path, blocked):
    runner = setup_runner(tmp_path)
    if blocked == 'review-active':
        client.acquire('owner/repo', 7, 'a'*40, 'mini')
    else:
        client = ClaimClient('nats://127.0.0.1:1')
    with patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(c.subprocess, 'run') as command:
        result = runner.execute_action(action(), True)
    assert not result.executed
    command.assert_not_called()
    runner._record_merge.assert_not_called()
    runner.metrics.record_merge.assert_not_called()


def test_revalidates_karen_after_acquiring_claim(client, tmp_path):
    runner = setup_runner(tmp_path)
    with patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(runner, '_check_merge_rate_limit', return_value=(True, 'ok')), \
         patch.object(c, 'check_merge_allowed', return_value=(True, 'ok')), \
         patch.object(c, '_read_verdict', return_value=None), \
         patch.object(c.subprocess, 'run') as command:
        result = runner.execute_action(action(), True)
    assert not result.executed and 'KARENPASS' in result.execution_result
    command.assert_not_called()
    assert client.acquire('owner/repo', 7, 'a'*40, 'mini')


def test_missing_github_state_cannot_merge(client, tmp_path):
    runner = setup_runner(tmp_path)
    with patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(runner, '_check_merge_rate_limit', return_value=(True, 'ok')), \
         patch.object(c, 'check_merge_allowed', return_value=(True, 'ok')), \
         patch.object(c, '_read_verdict', return_value={'verdict':'KARENPASS'}), \
         patch.object(c, '_get_pr_details', return_value=None), \
         patch.object(c.subprocess, 'run') as command:
        result = runner.execute_action(action(), True)
    assert not result.executed and 'Cannot fetch' in result.execution_result
    command.assert_not_called()


@pytest.mark.parametrize('state', ['MERGED', 'CLOSED'])
def test_completed_pr_cannot_be_merged_again(client, tmp_path, state):
    runner = setup_runner(tmp_path)
    with patch.object(ClaimClient, 'from_env', return_value=client), \
         patch.object(runner, '_check_merge_rate_limit', return_value=(True, 'ok')), \
         patch.object(c, 'check_merge_allowed', return_value=(True, 'ok')), \
         patch.object(c, '_read_verdict', return_value={'verdict':'KARENPASS'}), \
         patch.object(c, '_get_pr_details', return_value={'state':state}), \
         patch.object(c.subprocess, 'run') as command:
        result = runner.execute_action(action(), True)
    assert not result.executed and 'no longer open' in result.execution_result
    command.assert_not_called()
