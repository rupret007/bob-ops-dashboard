"""Every reported CI result must participate in merge admission."""
from unittest.mock import patch

import pytest

from cluster.control_plane import conductor, shadow_runner
from cluster.control_plane.tests.test_coordination import client
from cluster.control_plane.tests.test_deterministic_merge import SHA, REPO, artifact, pr, runner, validate


UNSAFE_ROLLUPS = [
    [{'name': 'tests', 'conclusion': 'FAILURE'},
     {'name': 'tests', 'conclusion': 'SUCCESS'}],
    [{'name': 'tests', 'conclusion': 'SUCCESS'},
     {'name': 'tests', 'conclusion': 'FAILURE'}],
    [{'name': 'tests', 'status': 'IN_PROGRESS'},
     {'name': 'tests', 'conclusion': 'SUCCESS'}],
    [{'context': 'tests', 'state': 'ERROR'},
     {'name': 'tests', 'conclusion': 'SUCCESS'}],
    [{'name': 'tests', 'conclusion': 'SUCCESS'}, None],
    [{'name': 'tests', 'conclusion': 'SUCCESS'}, 'unreadable'],
    [{'name': 'tests', 'status': 'IN_PROGRESS', 'conclusion': 'SUCCESS'}],
    [{'name': 'tests', 'conclusion': 'SUCCESS'},
     {'name': ['malformed'], 'conclusion': 'SUCCESS'}],
    {'unexpected': 'shape'},
]


@pytest.mark.parametrize('parse', [conductor._ci_status_from_pr, shadow_runner._ci_status_from_pr])
@pytest.mark.parametrize('checks', UNSAFE_ROLLUPS)
def test_ci_parser_cannot_hide_unsafe_check(parse, checks):
    states = parse({'statusCheckRollup': checks})
    assert states and any(state != 'success' for state in states.values())


@pytest.mark.parametrize('checks', UNSAFE_ROLLUPS)
def test_exact_head_karen_pass_cannot_merge_unsafe_ci(tmp_path, checks):
    artifact(tmp_path)
    with patch.object(conductor.subprocess, 'run') as command:
        result = validate(tmp_path, pr(statusCheckRollup=checks))
    assert not result.validated
    assert 'CI not passing' in result.validation_reason
    command.assert_not_called()


@pytest.mark.parametrize('parse', [conductor._ci_status_from_pr, shadow_runner._ci_status_from_pr])
def test_same_named_green_checks_remain_eligible(parse):
    assert parse({'statusCheckRollup': [
        {'name': 'tests', 'status': 'COMPLETED', 'conclusion': 'SUCCESS'},
        {'context': 'tests', 'state': 'SUCCESS'},
    ]}) == {'tests': 'success'}


def test_live_merge_rechecks_colliding_ci_under_shared_claim(client, tmp_path):
    artifact(tmp_path)
    worker = runner(tmp_path)
    action = conductor.ProposedAction('merge_pr', repo=REPO, pr_number=7, head_sha=SHA)
    with patch.object(conductor, '_get_pr_details', return_value=pr()):
        proposal = worker.validate_action(action, None)
    assert proposal.validated
    with patch.object(conductor.ClaimClient, 'from_env', return_value=client), \
         patch.object(conductor, '_get_pr_details', return_value=pr(statusCheckRollup=UNSAFE_ROLLUPS[0])), \
         patch.object(conductor.subprocess, 'run') as command:
        result = worker.execute_action(proposal, live=True)
    assert not result.executed
    assert 'CI not passing' in result.execution_result
    command.assert_not_called()
    # The rejected attempt releases ownership for the next safe owner.
    assert client.acquire(REPO, 7, SHA, 'mini')
