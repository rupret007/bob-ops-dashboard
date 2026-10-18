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
def setup_runtime():
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


def test_real_generic_worker_pipeline_checks_tests_and_creates_only_draft(setup_runtime):
    runtime, request, inbox, authority = setup_runtime
    calls = []
    committed = False
    authority.isolated_codex_command.return_value = ['operator-test-isolation-adapter']
    authority.run_isolated_tests.side_effect = lambda tree, tests, run: run(['isolated-test-adapter'], tree)
    def command(args, cwd=None, **kwargs):
        nonlocal committed
        fix_worker._execution_guard.get()()
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
        assert runtime.execute(request['request_id']) == 'complete'
    assert any(c[0] == 'operator-test-isolation-adapter' for c in calls)
    tests = next(i for i,c in enumerate(calls) if c == ['isolated-test-adapter'])
    push = next(i for i,c in enumerate(calls) if c[:2] == ['git', 'push'])
    assert tests < push
    assert calls[-1][:3] == ['gh', 'pr', 'create']
    assert '--draft' in calls[-1]
    assert calls[push][-1] == 'HEAD:refs/heads/' + request['branch']
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
