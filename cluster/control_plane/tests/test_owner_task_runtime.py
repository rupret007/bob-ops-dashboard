"""Runtime authorities are injected test doubles, never production attestations."""
import copy
import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from cluster.control_plane import fix_worker
from cluster.control_plane.coordination import CoordinationError
from cluster.control_plane.owner_task_runtime import OwnerTaskRuntime, create_owner_draft
from cluster.control_plane.owner_task_scheduler import POLICY, consume_pending, stage_owner_draft_task
from cluster.control_plane.tests.test_owner_task_scheduler import MemoryInbox


@pytest.fixture
def setup_runtime(monkeypatch):
    # Candidate-boundary unit tests are not production execution authorization.
    monkeypatch.setattr('cluster.control_plane.owner_task_runtime.EXECUTION_REVIEW_BLOCKED', False)
    request = dict(contract=1, request_id='webbob-' + 'a' * 64,
        repo='rupret007/bob-brain', head_sha='b' * 40,
        branch='draft/webbob-task-' + 'a' * 24, provider='codex',
        policy=POLICY, description='Add phone chat regression tests')
    authority = MagicMock()
    authority.read_controls.side_effect = lambda _: dict(scope='cluster', observed_at=time.time(),
        global_paused=False, codex_paused=False, node_state='active')
    authority.read_codex_quota.side_effect = lambda: dict(scope='cluster', observed_at=time.time(), weekly_pct=20)
    guards = MagicMock()
    guards.check_all.return_value.passed = True
    inbox, client = MemoryInbox(), MagicMock()
    runtime = OwnerTaskRuntime(client=client, inbox=inbox, machine='nucbox', authority=authority, guards=guards)
    return runtime, request, inbox, authority


def test_pending_to_guarded_execution_and_replay_is_at_most_once(setup_runtime):
    runtime, request, inbox, authority = setup_runtime
    stage_owner_draft_task(request, inbox=inbox)
    with patch('cluster.control_plane.owner_task_scheduler.Lease'), patch('cluster.control_plane.owner_task_runtime.Lease'):
        assert consume_pending(runtime, inbox=inbox, client=runtime.client, machine='nucbox') == 1
        with patch('cluster.control_plane.owner_task_runtime.create_owner_draft', return_value={'sha': 'c'*40}) as worker:
            assert runtime.execute(request['request_id']) == 'complete'
            assert runtime.execute(request['request_id']) == 'complete'
            worker.assert_called_once()
    authority.reserve_lane.assert_called_once_with('nucbox', 'codex', request['request_id'])


@pytest.mark.parametrize('failure', ['absent', 'stale', 'paused', 'quota', 'guards', 'lane', 'claim', 'inbox'])
def test_fail_closed_before_execution(setup_runtime, failure):
    runtime, request, inbox, authority = setup_runtime
    with patch('cluster.control_plane.owner_task_runtime.Lease'):
        runtime(request, branch_lease=MagicMock())
        if failure == 'absent':
            runtime.authority = None
        elif failure == 'stale':
            authority.read_controls.side_effect = lambda _: dict(scope='cluster', observed_at=0)
        elif failure == 'paused':
            authority.read_controls.side_effect = lambda _: dict(scope='cluster', observed_at=time.time(), global_paused=True)
        elif failure == 'quota':
            authority.read_codex_quota.side_effect = lambda: dict(scope='cluster', observed_at=time.time(), weekly_pct=75)
        elif failure == 'guards':
            runtime.guards.check_all.return_value.passed = False
        elif failure == 'lane':
            authority.check_lane.side_effect = CoordinationError('full')
        elif failure == 'claim':
            runtime.client.acquire_task.side_effect = CoordinationError('unavailable')
        else:
            inbox.available = False
        with patch('cluster.control_plane.owner_task_runtime.create_owner_draft') as worker:
            with pytest.raises(CoordinationError):
                runtime.execute(request['request_id'])
            worker.assert_not_called()


def test_uncertain_side_effect_never_replays_after_claim_expiry(setup_runtime):
    runtime, request, inbox, _ = setup_runtime
    with patch('cluster.control_plane.owner_task_runtime.Lease'):
        runtime(request, branch_lease=MagicMock())
        with patch('cluster.control_plane.owner_task_runtime.create_owner_draft', side_effect=OSError('ambiguous')) as worker:
            with pytest.raises(OSError):
                runtime.execute(request['request_id'])
            assert runtime.execute(request['request_id']) == 'uncertain'
            worker.assert_called_once()


def test_pause_at_actual_command_prevents_subprocess_start():
    guard = MagicMock(side_effect=CoordinationError('paused'))
    token = fix_worker._execution_guard.set(guard)
    try:
        with patch('cluster.control_plane.fix_worker.subprocess.Popen') as start, patch('cluster.control_plane.fix_worker.subprocess.run') as unclaimed:
            with pytest.raises(CoordinationError):
                fix_worker.run(['git', 'push', 'origin', 'HEAD:refs/heads/draft/example'])
            start.assert_not_called()
            unclaimed.assert_not_called()
    finally:
        fix_worker._execution_guard.reset(token)


def test_owner_worker_never_runs_outside_runtime(setup_runtime):
    _, request, _, authority = setup_runtime
    with pytest.raises(CoordinationError, match='guarded runtime'):
        create_owner_draft(request, authority)


@pytest.mark.parametrize('attack', ['none', 'missing_evidence', 'mutated_tests'])
def test_real_generic_worker_pipeline_checks_tests_and_creates_only_draft(setup_runtime, attack):
    runtime, request, inbox, authority = setup_runtime
    calls = []
    committed = False
    authority.isolated_codex_command.return_value = ['operator-test-isolation-adapter']
    def observed_tests(tree, tests, run, **kwargs):
        run(['isolated-test-adapter'], tree)
        required = kwargs['required_regressions']
        assert kwargs['immutable_snapshot'].digest == kwargs['snapshot_sha256']
        if attack == 'missing_evidence':
            return None
        if attack == 'mutated_tests':
            (tree / 'untested.py').write_text('untested mutation')
        return dict(snapshot_sha256=kwargs['snapshot_sha256'],
                    observer='trusted-outside-sandbox-v1', baseline_sha=kwargs['baseline_sha'],
                    candidate_suite_passed=True, baseline={test: 'failed' for test in required},
                    candidate={test: 'passed' for test in required})
    authority.run_isolated_tests.side_effect = observed_tests
    def command(args, cwd=None, **kwargs):
        nonlocal committed
        fix_worker._execution_guard.get()()
        raw = args
        if args[0] == 'git':
            operation = next(i for i, arg in enumerate(args[1:], 1)
                if arg in ('clone', 'checkout', 'rev-parse', 'ls-remote', 'diff', 'add', 'commit', 'push'))
            args = ['git'] + args[operation:]
            if args[1] != 'clone':
                assert any(a.startswith('--git-dir=') for a in raw)
                assert any(a.startswith('--work-tree=') for a in raw)
                assert 'core.hooksPath=/dev/null' in raw
            else:
                from pathlib import Path
                git_dir = Path(next(a.split('=',1)[1] for a in raw if a.startswith('--separate-git-dir=')))
                git_dir.mkdir()
                tree = Path(args[-1])
                tree.mkdir()
                (tree / '.git').write_text('gitdir: ' + str(git_dir) + '\n')
        calls.append(args)
        if args[:3] == ['git', 'rev-parse', 'HEAD']:
            return 'c'*40 if committed else request['head_sha']
        if args[:2] == ['git', 'commit']:
            committed = True
        if args[:2] == ['git', 'ls-remote']:
            return 'c'*40 + ' refs/heads/' + request['branch'] if committed else ''
        if args[:3] == ['git', 'diff', '--cached']:
            if '--diff-filter=D' in args:
                return ''
            if '--name-only' in args:
                return 'tests/test_chat.py'
            return 'diff --git a/tests/test_chat.py b/tests/test_chat.py\n--- a/tests/test_chat.py\n+++ b/tests/test_chat.py\n@@ -0,0 +1,2 @@\n+def test_chat():\n+    assert 1 == 1\n'
        if args[:3] == ['gh', 'pr', 'create']:
            return 'https://github.com/rupret007/bob-brain/pull/123'
        return ''
    with patch('cluster.control_plane.owner_task_runtime.Lease'), patch.object(fix_worker, 'run', side_effect=command), patch.object(fix_worker, 'test_command', return_value=['pytest']):
        runtime(request, branch_lease=MagicMock())
        if attack != 'none':
            with pytest.raises(CoordinationError, match='regression evidence|workspace changed'):
                runtime.execute(request['request_id'])
            assert not any(c[:2] == ['git', 'commit'] or c[:2] == ['git', 'push'] for c in calls)
            return
        assert runtime.execute(request['request_id']) == 'complete'
    assert any(c[0] == 'operator-test-isolation-adapter' for c in calls)
    tests = next(i for i,c in enumerate(calls) if c == ['isolated-test-adapter'])
    push = next(i for i,c in enumerate(calls) if c[:2] == ['git', 'push'])
    assert tests < push
    assert calls[-1][:3] == ['gh', 'pr', 'create']
    assert '--draft' in calls[-1]
    assert calls[push][-1] == 'HEAD:refs/heads/' + request['branch']
    assert calls[push][-2] == 'https://github.com/rupret007/bob-brain.git'
    assert not any(c[:2] == ['gh', 'merge'] or '--force' in c for c in calls)


@pytest.mark.parametrize('added,deleted', [
    ('+@pytest.mark.skip\n+def test_chat():\n+    assert True', ''),
    ('+def test_chat():\n+    assert True', 'tests/test_old.py'),
    ('+def test_chat():\n+    os.system("rm -rf /")', ''),
    ('+def test_chat():\n+    secret = "this-is-a-real-production-credential"', ''),
])
def test_draft_safety_rejects_test_skips_deletions_and_secrets(setup_runtime, added, deleted):
    from cluster.control_plane.owner_task_runtime import check_draft_diff
    _, request, _, _ = setup_runtime
    diff = 'diff --git a/tests/test_chat.py b/tests/test_chat.py\n--- a/tests/test_chat.py\n+++ b/tests/test_chat.py\n@@ -0,0 +1,3 @@\n' + added + '\n'
    def command(args, cwd=None):
        if '--diff-filter=D' in args:
            return deleted
        if '--name-only' in args:
            return 'tests/test_chat.py'
        return diff
    with patch.object(fix_worker, 'run', side_effect=command), pytest.raises(CoordinationError):
        check_draft_diff(None, request)


@pytest.mark.parametrize('attack', ['config', 'hook', 'pointer', 'symlink'])
def test_untrusted_git_mutation_blocks_all_privileged_followups(setup_runtime, attack):
    from pathlib import Path
    runtime, request, _, authority = setup_runtime
    git_dir, tree = None, None
    model_ran = False
    privileged_after_model = []
    def command(args, cwd=None, **kwargs):
        nonlocal git_dir, tree, model_ran
        fix_worker._execution_guard.get()()
        if args[0] == 'git' and 'clone' in args:
            git_dir = Path(next(a.split('=',1)[1] for a in args if a.startswith('--separate-git-dir=')))
            git_dir.mkdir()
            (git_dir / 'config').write_text('[remote "origin"]\n url = https://github.com/rupret007/bob-brain.git\n')
            tree = Path(args[-1])
            tree.mkdir()
            (tree / '.git').write_text('gitdir: ' + str(git_dir) + '\n')
        elif args[0] == 'git':
            if model_ran:
                privileged_after_model.append(args)
            if 'rev-parse' in args:
                return request['head_sha']
        elif args == ['malicious-model-test-adapter']:
            model_ran = True
            if attack == 'config':
                (git_dir / 'config').write_text('[credential]\n helper = !steal-credentials\n[remote "origin"]\n url = https://github.com/rupret007/rad-dad-show-night.git\n')
            elif attack == 'hook':
                (git_dir / 'hooks').mkdir()
                (git_dir / 'hooks' / 'pre-commit').write_text('steal-credentials')
            elif attack == 'pointer':
                (tree / '.git').write_text('gitdir: /tmp/hostile-git\n')
            else:
                (tree / 'hidden').symlink_to(git_dir / 'config')
        return ''
    authority.isolated_codex_command.return_value = ['malicious-model-test-adapter']
    with patch('cluster.control_plane.owner_task_runtime.Lease'), patch.object(fix_worker, 'run', side_effect=command), patch.object(fix_worker, 'test_command', return_value=['pytest']):
        runtime(request, branch_lease=MagicMock())
        with pytest.raises(CoordinationError, match='metadata|Symlink'):
            runtime.execute(request['request_id'])
    assert model_ran
    assert privileged_after_model == []
    authority.run_isolated_tests.assert_not_called()
    _, kwargs = authority.isolated_codex_command.call_args
    assert git_dir in kwargs['denied_paths']
    assert tree / '.git' in kwargs['denied_paths']


def test_review_latch_blocks_execution_without_test_override(setup_runtime, monkeypatch):
    runtime, request, _, _ = setup_runtime
    monkeypatch.setattr('cluster.control_plane.owner_task_runtime.EXECUTION_REVIEW_BLOCKED', True)
    with patch('cluster.control_plane.owner_task_runtime.create_owner_draft') as worker:
        with pytest.raises(CoordinationError, match='execution blocked'):
            runtime.execute(request['request_id'])
        worker.assert_not_called()
    with pytest.raises(CoordinationError, match='admission blocked'):
        runtime(request, branch_lease=MagicMock())
    runtime.client.acquire_task.assert_not_called()


def test_pause_during_lease_renew_never_starts_push():
    paused = False
    def pause():
        nonlocal paused
        paused = True
    def guard():
        if paused:
            raise CoordinationError('paused during renewal')
    lease = MagicMock()
    lease.renew.side_effect = pause
    lease_token = fix_worker._lease.set(lease)
    guard_token = fix_worker._execution_guard.set(guard)
    try:
        with patch('cluster.control_plane.fix_worker.subprocess.Popen') as spawn:
            with pytest.raises(CoordinationError, match='paused during renewal'):
                fix_worker.run(['git', 'push', 'origin', 'HEAD:refs/heads/draft/example'])
            spawn.assert_not_called()
    finally:
        fix_worker._execution_guard.reset(guard_token)
        fix_worker._lease.reset(lease_token)


@pytest.mark.parametrize('filepath,added', [
    ('tests/conftest.py', '+def pytest_collection_modifyitems(items):\n+    for item in items:\n+        item.add_marker(pytest.mark.skip(reason="skip suite"))'),
    ('tests/test_chat.py', '+def test_chat():\n+    assert True\n+def pytest_collection_modifyitems(items):\n+    items.clear()'),
    ('tests/helper.py', '+def helper():\n+    return True'),
])
def test_collection_suppression_and_helper_only_are_rejected(setup_runtime, filepath, added):
    from cluster.control_plane.owner_task_runtime import check_draft_diff
    _, request, _, _ = setup_runtime
    diff = 'diff --git a/' + filepath + ' b/' + filepath + '\n--- a/' + filepath + '\n+++ b/' + filepath + '\n@@ -0,0 +1,5 @@\n' + added + '\n'
    def command(args, cwd=None):
        if '--diff-filter=D' in args:
            return ''
        if '--name-only' in args:
            return filepath
        return diff
    with patch.object(fix_worker, 'run', side_effect=command), pytest.raises(CoordinationError):
        check_draft_diff(None, request)
