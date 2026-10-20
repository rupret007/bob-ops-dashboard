"""Fast failure cannot authorize publication or remove full-suite coverage."""
import subprocess
from unittest.mock import Mock

import pytest

from cluster.control_plane import worker_test_feedback as feedback

FILES = ['tests/test_early.py', 'tests/test_take_export.py', 'tests/test_unrelated.py']


@pytest.fixture(autouse=True)
def no_real_log(monkeypatch):
    monkeypatch.setattr(feedback, 'event', Mock())


def runtime():
    return Mock(test_command=lambda files: ['qualified-python', 'fixed-runner', *files])


def test_changed_export_module_runs_its_regressions_before_unchanged_full_suite():
    run = Mock(return_value='complete')
    command = ['original', 'complete', *FILES]
    env = {'HOME': 'preserved'}
    assert feedback.run_worker_tests(run, command, 'owned-tree', env, runtime=runtime(),
        files=FILES, changed_paths=['project/take_export.py']) == 'complete'
    assert run.call_count == 2
    assert run.call_args_list[0].args == (['qualified-python', 'fixed-runner', FILES[1]], 'owned-tree')
    assert run.call_args_list[1].args[0] is command
    assert all(call.kwargs['env'] is env for call in run.call_args_list)


def test_failed_precheck_retains_original_failure_for_normal_feedback_and_blocks_full_run():
    failure = subprocess.CalledProcessError(1, ['fixed-runner'],
        output='FAILED tests/test_take_export.py::test_source_change_is_refused')
    run = Mock(side_effect=failure)
    with pytest.raises(subprocess.CalledProcessError) as error:
        feedback.run_worker_tests(run, ['full', *FILES], 'owned-tree', {}, runtime=runtime(),
            files=FILES, prior_failed_tests=['tests/test_take_export.py::test_source_change_is_refused'])
    assert error.value is failure
    assert run.call_count == 1
    assert run.call_args.args[0] == ['qualified-python', 'fixed-runner', FILES[1]]


def test_passing_precheck_cannot_hide_failure_in_an_unrelated_full_suite_module():
    failure = subprocess.CalledProcessError(1, ['full'], output='unrelated failure')
    run = Mock(side_effect=['precheck passes', failure])
    command = ['full', *FILES]
    with pytest.raises(subprocess.CalledProcessError) as error:
        feedback.run_worker_tests(run, command, 'owned-tree', {}, runtime=runtime(),
            files=FILES, changed_paths=['tests/test_take_export.py'])
    assert error.value is failure
    assert run.call_args.args[0] is command


def test_prior_failures_take_priority_and_selection_has_a_fixed_bound():
    files = ['tests/test_%02d.py' % index for index in range(20)]
    selected = feedback.select_prechecks(files, files, [files[-1] + '::test_failed'])
    assert selected[0] == files[-1]
    assert len(selected) == feedback.MAX_PRECHECK_MODULES == 8
    assert set(selected).issubset(files)


def test_source_paths_and_failure_text_cannot_supply_new_commands_or_tests():
    assert feedback.select_prechecks(FILES,
        ['/private/take_export.py', '../take_export.py', 'x/../take_export.py',
         'x//take_export.py', 'take_export.py;execute', 'take_export.py\n'],
        ['arbitrary-command', 'tests/test_unknown.py::test_missing']) == []


@pytest.mark.parametrize('qualified', [False, True])
def test_no_selected_prechecks_preserves_the_original_full_command(qualified):
    command = ['full', *FILES]
    run = Mock()
    feedback.run_worker_tests(run, command, 'owned-tree', {},
        runtime=runtime() if qualified else None, files=FILES)
    run.assert_called_once()
    assert run.call_args.args == (command, 'owned-tree')


def test_prechecking_every_module_does_not_run_the_same_full_plan_twice():
    run = Mock()
    command = ['full', *FILES]
    feedback.run_worker_tests(run, command, 'owned-tree', {}, runtime=runtime(),
        files=FILES, changed_paths=FILES)
    run.assert_called_once()
    assert run.call_args.args[0] is command
