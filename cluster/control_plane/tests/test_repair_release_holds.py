"""Explicit holds block repair admission and a later hold blocks its push."""
import json
from unittest.mock import MagicMock, patch

import pytest

from cluster.control_plane import dispatcher, fix_worker
from cluster.control_plane.coordination import ClaimClient
from cluster.control_plane.job_ledger import JobLedger
from cluster.control_plane.retry_policy import PreflightFailure

H = 'a' * 40


@pytest.mark.parametrize('held', [dict(title='[HELD] parked'),
    dict(title='Do not merge until approved'), dict(labels=[dict(name='release-hold')])])
def test_discovery_hold_never_claims_reserves_or_creates_a_repair(tmp_path, held):
    ledger = JobLedger(tmp_path / 'jobs')
    worker = dispatcher.Dispatcher('nucbox', tmp_path / 'dispatch', job_ledger=ledger,
                                   budget_tracker=MagicMock())
    pr = dict(dict(number=7, headRefOid=H, isDraft=False, title='Ready', labels=[]), **held)
    with patch.object(worker, 'check_machine_guards', return_value=(True, 'ok', {})), \
         patch.object(worker, 'select_provider', return_value='codex') as provider, \
         patch.object(dispatcher, 'REPOS', ['owner/repo']), \
         patch.object(dispatcher, '_get_open_prs', return_value=[pr]), \
         patch.object(dispatcher, '_get_active_lanes', return_value={}), \
         patch.object(ClaimClient, 'from_env') as claims, \
         patch.object(dispatcher, '_spawn_worker') as spawn:
        decisions = worker.evaluate(live=True)
    assert any((d.extra or {}).get('reason_code') == 'release-held' for d in decisions)
    assert ledger._load_jobs() == []
    provider.assert_not_called()
    claims.assert_not_called()
    spawn.assert_not_called()


@pytest.mark.parametrize('when', ['initial', 'before-push'])
@pytest.mark.parametrize('held', [dict(title='[HELD] parked'),
    dict(labels=[dict(name='hold')]), dict(labels=None)])
def test_current_worker_hold_or_invalid_metadata_never_reaches_push(tmp_path, when, held):
    commands, provider_calls, views = [], [], []
    ready = dict(headRefName='fix/one', headRefOid=H, state='OPEN',
                 isCrossRepository=False, title='Ready', labels=[])

    def command(args, cwd=None, **kwargs):
        commands.append(args)
        if args[:3] == ['gh', 'pr', 'view']:
            views.append(1)
            return json.dumps(dict(ready, **held) if when == 'initial' or len(views) > 1 else ready)
        if args[:3] == ['git', 'worktree', 'add']:
            tree = __import__('pathlib').Path(args[-2])
            (tree / 'tests').mkdir(parents=True)
            (tree / 'tests' / 'test_behavior.py').write_text('def test_behavior(): assert True\n')
        if args[0] == str(tmp_path / 'codex'):
            provider_calls.append(1)
        if args[:3] == ['git', 'rev-parse', 'HEAD']:
            return 'b' * 40 if any(c[:2] == ['git', 'commit'] for c in commands) else H
        if args[:3] == ['git', 'diff', '--cached']:
            return 'fix.py'
        if args[:2] == ['git', 'ls-remote']:
            return 'b' * 40 + ' refs/heads/fix/one'
        return ''

    with patch.object(fix_worker, '_find_codex', return_value=tmp_path / 'codex'), \
         patch.object(fix_worker, 'run', side_effect=command):
        with pytest.raises(PreflightFailure) as failure:
            fix_worker.fix_pr('owner/repo', 7, H, ['repair'])
    assert failure.value.code == 'protected-pr'
    assert not any(c[:2] == ['git', 'push'] for c in commands)
    assert len(provider_calls) == (0 if when == 'initial' else 1)
