"""Real nonblocking locks and exact-target canary admission; no live mutations."""
import copy
import fcntl
import subprocess
from unittest.mock import Mock

import pytest

from cluster.control_plane import self_update as updater
from cluster.control_plane import health_adapters, health_peer
from cluster.control_plane.health_adapters import PERIODS

OLD, TARGET, NEXT = 'a' * 40, 'b' * 40, 'c' * 40


@pytest.fixture
def update(tmp_path, monkeypatch):
    data = tmp_path / 'state'
    data.mkdir()
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(data))
    monkeypatch.setattr(updater, '_log_update_outcome', Mock())
    state = dict(head=OLD, remote=TARGET)
    monkeypatch.setattr(updater, 'get_current_head', lambda _: state['head'])
    monkeypatch.setattr(updater, 'get_current_branch', lambda _: updater.BRANCH)
    monkeypatch.setattr(updater, 'get_remote_head', lambda _: state['remote'])
    monkeypatch.setattr(updater, 'is_working_tree_dirty', lambda _: False)
    for name in ('fetch_branch', 'run_tests', 'target_canary_ready', 'reinstall',
                 'copy_karen_module', 'migrate_config'):
        monkeypatch.setattr(updater, name, Mock(return_value=True))
    monkeypatch.setattr(updater, 'clear_pycache', Mock(return_value=0))
    monkeypatch.setattr(updater, 'can_fast_forward', Mock(side_effect=lambda _, sha: (True, sha)))
    def forward(_, sha):
        state['head'] = sha
        return True, OLD
    monkeypatch.setattr(updater, 'fast_forward', Mock(side_effect=forward))
    return tmp_path, data, state


@pytest.mark.parametrize('operator', [False, True])
def test_mutex_blocks_every_caller_even_operator(update, operator):
    repo, data, _ = update
    with (data / '.self-update.lock').open('a') as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = updater.self_update('nucbox', repo, operator=operator,
                                     expected_head=TARGET if operator else None)
    assert result['outcome'] == 'SKIPPED'
    updater.fetch_branch.assert_not_called()
    updater.reinstall.assert_not_called()


def test_overseer_blocks_automatic_but_explicit_target_operator_still_runs(update):
    repo, data, _ = update
    with (data / 'overseer.lock').open('a') as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert updater.self_update('nucbox', repo)['outcome'] == 'SKIPPED'
        updater.fetch_branch.assert_not_called()
        assert updater.self_update('nucbox', repo, operator=True,
                                   expected_head=TARGET)['outcome'] == 'UPDATED'
    updater.run_tests.assert_called_once_with(repo)
    updater.reinstall.assert_called_once_with(repo, 'nucbox')


def test_automatic_admission_and_mutex_remain_held_through_installer(update):
    repo, data, _ = update
    def install(*_):
        for name in ('overseer.lock', '.self-update.lock'):
            with (data / name).open('a') as contender:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    updater.reinstall.side_effect = install
    assert updater.self_update('nucbox', repo)['outcome'] == 'UPDATED'
    with (data / '.self-update.lock').open('a') as next_owner:
        fcntl.flock(next_owner, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize('head', [None, '', 'b' * 12, TARGET.upper(), True])
def test_operator_requires_explicit_full_target_before_any_work(update, head):
    repo, _, _ = update
    assert updater.self_update('nucbox', repo, operator=True,
                               expected_head=head)['outcome'] == 'FAILED'
    updater.fetch_branch.assert_not_called()


def test_expected_head_mismatch_cannot_apply_different_remote(update):
    repo, _, _ = update
    assert updater.self_update('nucbox', repo, operator=True,
                               expected_head=NEXT)['outcome'] == 'FAILED'
    updater.fast_forward.assert_not_called()
    updater.reinstall.assert_not_called()


def test_missing_canary_defers_before_fast_forward_even_for_operator(update):
    repo, _, _ = update
    updater.target_canary_ready.return_value = False
    assert updater.self_update('mini', repo, operator=True,
                               expected_head=TARGET)['outcome'] == 'SKIPPED'
    updater.fast_forward.assert_not_called()
    updater.copy_karen_module.assert_not_called()
    updater.reinstall.assert_not_called()


@pytest.mark.parametrize('loss', [[True, False], [True, True, False]])
def test_canary_loss_preserves_pending_and_same_head_retry(update, loss):
    repo, _, state = update
    updater.target_canary_ready.side_effect = loss
    assert updater.self_update('mini', repo)['outcome'] == 'SKIPPED'
    assert state['head'] == TARGET
    assert (repo / '.storypool-reinstall-pending').exists()
    updater.reinstall.assert_not_called()
    updater.target_canary_ready.side_effect = None
    assert updater.self_update('mini', repo)['outcome'] == 'UPDATED'
    assert not (repo / '.storypool-reinstall-pending').exists()
    assert updater.fast_forward.call_count == 1


def test_same_head_pending_install_cannot_bypass_canary(update):
    repo, _, state = update
    state['head'] = TARGET
    pending = repo / '.storypool-reinstall-pending'
    pending.touch()
    updater.target_canary_ready.return_value = False
    assert updater.self_update('mini', repo)['outcome'] == 'SKIPPED'
    assert pending.exists()
    updater.reinstall.assert_not_called()


def test_remote_ref_moving_does_not_change_literal_merge_target(update):
    repo, _, state = update
    def forward(_, sha):
        state.update(remote=NEXT, head=sha)
        return True, OLD
    updater.fast_forward.side_effect = forward
    assert updater.self_update('mini', repo)['outcome'] == 'UPDATED'
    updater.can_fast_forward.assert_called_once_with(repo, TARGET)
    updater.fast_forward.assert_called_once_with(repo, TARGET)
    assert state['head'] == TARGET
    assert all(call.args[1] == TARGET for call in updater.target_canary_ready.call_args_list)


@pytest.mark.parametrize('stage', ['tests', 'preparation'])
def test_changed_head_never_installs_and_keeps_pending(update, stage):
    repo, _, state = update
    def change(*_):
        state['head'] = NEXT
        return True
    getattr(updater, 'run_tests' if stage == 'tests' else 'copy_karen_module').side_effect = change
    assert updater.self_update('mini', repo)['outcome'] == 'FAILED'
    updater.reinstall.assert_not_called()
    assert (repo / '.storypool-reinstall-pending').exists()


def observed():
    peer = dict(schema=1, machine='nucbox', loaded_head=TARGET, timestamp=1000,
                observation=dict(nonce='d' * 32, served_at=1000),
                rollout=dict(phase='active', head=TARGET, activated_at=990, last_confirmed_at=1000),
                groups={name: dict(started_at=1000, finished_at=1000, period=period,
                                   checks=[dict(name=name, passed=True)])
                        for name, period in PERIODS.items()})
    return health_peer.direct_observation(peer, 'nucbox', 'd' * 32, 1000, .1)


@pytest.mark.parametrize('change', ['none', 'wrong-loaded', 'wrong-receipt', 'canary',
                                 'wrong-machine', 'stale', 'future-confirmation', 'legacy',
                                 'group-error', 'missing-group', 'stale-group', 'old-cycle',
                                 'missing-activation', 'semantic-yellow'])
def test_target_gate_requires_direct_fresh_exact_active_observation(monkeypatch, change):
    details = copy.deepcopy(observed())
    peer = details['health']
    if change == 'wrong-loaded': peer['loaded_head'] = OLD
    if change == 'wrong-receipt': peer['rollout']['head'] = OLD
    if change == 'canary': peer['rollout']['phase'] = 'canary'
    if change == 'wrong-machine': peer['machine'] = 'mini'
    if change == 'stale': details['received_at'] = 1
    if change == 'future-confirmation': peer['rollout']['last_confirmed_at'] = 1001
    if change == 'legacy': details.pop('clock_observation')
    if change == 'group-error': peer['groups']['guardian']['error'] = 'TimeoutError'
    if change == 'missing-group': peer['groups'].pop('guardian')
    if change == 'stale-group': peer['groups']['guardian']['finished_at'] = 1
    if change == 'old-cycle': peer['groups']['guardian']['started_at'] = 989
    if change == 'missing-activation': peer['rollout'].pop('activated_at')
    if change == 'semantic-yellow': peer['groups']['guardian']['checks'][0].update(passed=False, unknown=True)
    monkeypatch.setattr(health_adapters, 'collect_peer', lambda _: dict(details=details))
    monkeypatch.setattr(updater.time, 'time', lambda: 1001)
    assert updater.target_canary_ready('mini', TARGET) is (change in ('none', 'semantic-yellow'))


def test_unavailable_peer_fails_closed_without_logging_raw_values(monkeypatch, caplog):
    monkeypatch.setattr(health_adapters, 'collect_peer', Mock(side_effect=OSError('private endpoint secret')))
    assert not updater.target_canary_ready('mini', TARGET)
    assert 'private endpoint secret' not in caplog.text


def test_nuc_can_update_without_waiting_for_mini(monkeypatch):
    collect = Mock(side_effect=AssertionError('must not contact Mini'))
    monkeypatch.setattr(health_adapters, 'collect_peer', collect)
    assert updater.target_canary_ready('nucbox', TARGET)
    collect.assert_not_called()


def test_git_helpers_use_literal_target_instead_of_movable_remote_ref(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, 'get_current_head', lambda _: OLD)
    run = Mock(return_value=subprocess.CompletedProcess([], 0, TARGET + '\n', ''))
    monkeypatch.setattr(updater.subprocess, 'run', run)
    assert updater.can_fast_forward(tmp_path, TARGET) == (True, TARGET)
    assert run.call_args_list[-1].args[0] == ['git', 'merge-base', '--is-ancestor', 'HEAD', TARGET]
    assert updater.fast_forward(tmp_path, TARGET) == (True, OLD)
    assert run.call_args_list[-1].args[0] == ['git', 'merge', '--ff-only', TARGET]


def test_exception_releases_both_locks(update):
    repo, data, _ = update
    updater.run_tests.side_effect = RuntimeError('fixture failure')
    with pytest.raises(RuntimeError):
        updater.self_update('nucbox', repo)
    for name in ('overseer.lock', '.self-update.lock'):
        with (data / name).open('a') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
