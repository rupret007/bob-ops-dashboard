import copy
import unittest
from unittest.mock import patch

from cluster.control_plane.owner_task_scheduler import (
    CHECKS, CoordinationError, POLICY, consume_pending, stage_owner_draft_task, validate_admission)


class MemoryInbox:
    def __init__(self):
        self.rows = {}
        self.available = True

    def call(self, operation, key=None, value=None, revision=None):
        if not self.available:
            raise CoordinationError('unavailable')
        if operation == 'create':
            if key in self.rows:
                raise CoordinationError('exists')
            self.rows[key] = (copy.deepcopy(value), 1)
            return 1
        if operation == 'get':
            return copy.deepcopy(self.rows.get(key))
        if operation == 'keys':
            return list(self.rows)
        if operation == 'update':
            if self.rows[key][1] != revision:
                raise CoordinationError('stale')
            self.rows[key] = (copy.deepcopy(value), revision + 1)
            return revision + 1


class OwnerInboxTests(unittest.TestCase):
    def setUp(self):
        self.request = dict(contract=1, request_id='webbob-' + 'a' * 64,
            repo='rupret007/bob-brain', head_sha='b' * 40,
            branch='draft/webbob-task-' + 'a' * 24, provider='codex',
            policy=POLICY, description='Add chat tests')
        self.inbox = MemoryInbox()

    def test_durable_dedup_and_pending_never_claims_guard_success(self):
        first = stage_owner_draft_task(self.request, inbox=self.inbox)
        self.assertEqual(first, stage_owner_draft_task(self.request, inbox=self.inbox))
        self.assertEqual(len(self.inbox.rows), 1)
        self.assertEqual(first['state'], 'awaiting_admission')
        self.assertNotIn('checks', first)
        self.assertNotIn('codex_weekly_pct', first)
        self.request['description'] = 'Different work'
        with self.assertRaises(CoordinationError):
            stage_owner_draft_task(self.request, inbox=self.inbox)

    def test_outage_and_invalid_scope_fail_closed(self):
        self.inbox.available = False
        with self.assertRaises(CoordinationError):
            stage_owner_draft_task(self.request, inbox=self.inbox)
        self.assertEqual(self.inbox.rows, {})
        self.inbox.available = True
        for key, value in [('repo', 'rupret007/rad-dad-show-night'), ('branch', 'main'),
                           ('head_sha', 'b' * 12), ('description', 'send mail')]:
            request = dict(self.request, **{key: value})
            with self.assertRaises(CoordinationError):
                stage_owner_draft_task(request, inbox=self.inbox)
        self.assertEqual(self.inbox.rows, {})

    def test_real_runtime_must_verify_every_gate_and_quota(self):
        receipt = dict(self.request, state='queued', receipt_id='owner-1', codex_weekly_pct=20,
                       admitted_at=1000, checks={k: True for k in CHECKS})
        validate_admission(receipt, self.request, 1000)
        for key in CHECKS:
            bad = copy.deepcopy(receipt)
            bad['checks'][key] = False
            with self.assertRaises(CoordinationError):
                validate_admission(bad, self.request, 1000)
        for quota in (75, True, float('nan')):
            with self.assertRaises(CoordinationError):
                validate_admission(dict(receipt, codex_weekly_pct=quota), self.request, 1000)

    def test_consumer_leaves_pending_if_runtime_unavailable_or_unverified(self):
        stage_owner_draft_task(self.request, inbox=self.inbox)
        from unittest.mock import Mock, MagicMock
        client = Mock()
        with patch('cluster.control_plane.owner_task_scheduler.Lease') as lease:
            lease.return_value = MagicMock()
            runtime = Mock(side_effect=CoordinationError('quota unavailable'))
            with self.assertRaises(CoordinationError):
                consume_pending(runtime, inbox=self.inbox, client=client, machine='nucbox')
        row = next(iter(self.inbox.rows.values()))[0]
        self.assertEqual(row['state'], 'awaiting_admission')
        self.assertEqual(client.acquire_task.call_count, 2)
        self.assertTrue(any('branch:' in c.args[1] for c in client.acquire_task.call_args_list))

    def test_valid_runtime_receipt_consumed_once(self):
        stage_owner_draft_task(self.request, inbox=self.inbox)
        from unittest.mock import Mock, MagicMock
        runtime = Mock(return_value=dict(self.request, state='queued', receipt_id='owner-1', codex_weekly_pct=20,
            admitted_at=1000, checks={k: True for k in CHECKS}))
        with patch('cluster.control_plane.owner_task_scheduler.Lease', return_value=MagicMock()), patch(
                'cluster.control_plane.owner_task_scheduler.time.time', return_value=1000):
            self.assertEqual(consume_pending(runtime, inbox=self.inbox, client=Mock(), machine='nucbox'), 1)
            self.assertEqual(consume_pending(runtime, inbox=self.inbox, client=Mock(), machine='mini'), 0)
        runtime.assert_called_once()
