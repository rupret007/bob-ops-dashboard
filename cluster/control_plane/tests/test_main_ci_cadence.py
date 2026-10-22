"""Resumable CI receipts must fit the freshness window and account-wide budget."""
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane import github_budget as budget, health_adapters, main_ci as m


@pytest.mark.parametrize('argv,resource,background', [
    (['gh', 'api', 'repos/owner/repo/commits/main/status'], None, True),
    (['gh', 'pr', 'view', '1', '--repo', 'owner/repo'], None, True),
    (['gh', 'search', 'prs', '--owner', 'owner'], 'search', True),
    (['gh', 'pr', 'create', '--repo', 'owner/repo'], None, False),
])
@pytest.mark.parametrize('scheduled', [False, True])
def test_every_github_command_uses_shared_admission(argv, resource, background, scheduled):
    token = m._deadline.set(220 if scheduled else None)
    with patch.object(m.github_reads, 'run', return_value=subprocess.CompletedProcess(argv, 0, 'value')) as run, \
         patch.object(m.subprocess, 'run') as raw, patch.object(m.time, 'monotonic', return_value=100):
        try:
            assert m.command(argv) == 'value'
        finally:
            m._deadline.reset(token)
    run.assert_called_once_with(argv[1:], timeout=120, resource=resource,
                                deadline=220 if scheduled else None,
                                background=background and scheduled)
    raw.assert_not_called()


def test_git_commands_stay_local():
    with patch.object(m.github_reads, 'run') as shared, \
         patch.object(m.subprocess, 'run', return_value=SimpleNamespace(stdout='sha')) as local:
        assert m.command(['git', 'rev-parse', 'HEAD']) == 'sha'
    shared.assert_not_called()
    assert local.call_args.args[0] == ['git', 'rev-parse', 'HEAD']


@pytest.mark.parametrize('after_admission,expected_timeout', [(119, 1), (120, None)])
def test_shared_admission_time_cannot_extend_whole_batch_deadline(after_admission, expected_timeout):
    with patch.object(m.github_reads.budget, 'reserve'), \
         patch.object(m.github_reads.time, 'monotonic', return_value=after_admission), \
         patch.object(m.github_reads.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
        if expected_timeout is None:
            with pytest.raises(subprocess.TimeoutExpired):
                m.github_reads.run(['api', 'example'], timeout=120, deadline=120)
            run.assert_not_called()
        else:
            m.github_reads.run(['api', 'example'], timeout=120, deadline=120)
            assert run.call_args.kwargs['timeout'] == expected_timeout


def test_minute_deferral_has_controlled_retry_identity_and_preserves_limit():
    state = {'version': 1, 'requests': [100] * 40, 'resources': {}}
    with pytest.raises(budget.GitHubDeferred) as raised:
        budget._mutate(state, 'reserve', 'core', {}, False, 101)
    assert raised.value.code == 'minute-exhausted'
    assert raised.value.retry_after == 59
    assert budget.MINUTE_LIMIT == 40 and len(state['requests']) == 40


@pytest.mark.parametrize('code', ['other', 'primary', 'backoff', 'unavailable'])
def test_primary_backoff_or_unknown_deferrals_never_wait_or_retry(code):
    token = m._deadline.set(120)
    try:
        with patch.object(m, 'time', Mock(monotonic=lambda: 1)) as clock:
            assert not m._wait_for_minute(code, 60)
        clock.sleep.assert_not_called()
    finally:
        m._deadline.reset(token)


def test_discovery_deferral_remains_unconfirmed_without_overwriting_progress(tmp_path):
    (tmp_path / 'main-ci-progress.json').write_text(json.dumps({'records': {
        'owner/repo#1': {'repo': 'owner/repo', 'pr': 1, 'main_ci': 'success', 'checked_at': 100}}}))
    before = (tmp_path / 'main-ci-progress.json').read_bytes()
    with patch.object(m.ClaimClient, 'from_env'), patch.object(m, 'recent_prs',
            side_effect=budget.GitHubDeferred('primary quota reserve reached')):
        result = m.reconcile_batch(tmp_path)
    assert result['discovery_deferred'] and result['unconfirmed'] > 0
    assert result['results'] == [] and (tmp_path / 'main-ci-progress.json').read_bytes() == before


@pytest.mark.parametrize('status,checked_at,expected_unconfirmed', [
    ('deferred', 550, 0), ('deferred', 399, 1), ('unknown', 550, 1), ('pending', 550, 1)])
def test_renewal_cannot_extend_old_receipt_or_hide_new_unknown_evidence(
        tmp_path, status, checked_at, expected_unconfirmed):
    previous = dict(repo='owner/repo', pr=1, main_ci='success', checked_at=checked_at, attempted_at=checked_at)
    (tmp_path / 'main-ci-progress.json').write_text(json.dumps({'records': {'owner/repo#1': previous}}))
    result = dict(repo='owner/repo', pr=1, main_ci=status, budget_code='other')
    # The old receipt is already due for renewal but may still be valid.
    with patch.object(m, 'time', Mock(monotonic=lambda: 0, time=lambda: 1100)), \
         patch.object(m.ClaimClient, 'from_env'), \
         patch.object(m, 'recent_prs', return_value=[dict(number=1, repository={'nameWithOwner': 'owner/repo'})]), \
         patch.object(m, 'reconcile_one', return_value=result):
        observed = m.reconcile_batch(tmp_path)
    assert observed['unconfirmed'] == expected_unconfirmed
    saved = json.loads((tmp_path / 'main-ci-progress.json').read_text())['records']['owner/repo#1']
    if status == 'deferred' and expected_unconfirmed == 0:
        assert saved['checked_at'] == checked_at  # No timestamp refresh without new evidence.
    elif status in ('unknown', 'pending'):
        assert saved['main_ci'] == status


def test_63_receipts_stay_fresh_with_other_traffic_and_no_more_than40_per_minute(tmp_path):
    class Clock:
        now = 0
        active_leases = 0
        peak = 0
        seeded_minutes = set()
        state = {'version': 1, 'requests': [], 'resources': {}}

        def monotonic(self):
            return self.now

        def time(self):
            return 10000 + self.now

        def sleep(self, duration):
            assert self.active_leases == 0  # Never hold a PR claim while awaiting quota.
            assert 0 < duration <= 5
            self.now += duration

        def request(self):
            if self.now >= m._deadline.get():
                raise subprocess.TimeoutExpired(['gh'], 0)
            minute = int(self.now // 60)
            if minute not in self.seeded_minutes:
                self.seeded_minutes.add(minute)
                # Other StoryPool services reserve eight requests each minute.
                for _ in range(8):
                    budget._mutate(self.state, 'reserve', 'core', {}, False, self.time())
            budget._mutate(self.state, 'reserve', 'core', {}, False, self.time())
            self.peak = max(self.peak, len(self.state['requests']))
            self.now += 1

    clock = Clock()
    receipts = [dict(number=n, repository={'nameWithOwner': 'owner/repo'}) for n in range(1, 64)]

    def recent():
        clock.request()
        return receipts

    def merged(repo, number):
        clock.request()
        return dict(state='MERGED', headRefOid=f'{number + 100:040x}',
                    mergeCommit={'oid': f'{number:040x}'}, baseRefName='main')

    def check(repo, sha):
        assert clock.active_leases == 1
        clock.request()
        clock.request()
        return True, 'success'

    class Lease:
        def __init__(self, *args):
            pass

        def __enter__(self):
            clock.active_leases += 1
            return self

        def __exit__(self, *args):
            clock.active_leases -= 1

        def renew(self):
            pass

    results = []
    with patch.object(m, 'time', clock), patch.object(m.ClaimClient, 'from_env'), \
         patch.object(m, 'Lease', Lease), patch.object(m, 'recent_prs', side_effect=recent), \
         patch.object(m, 'merged_pr', side_effect=merged), patch.object(m, 'check_ci', side_effect=check):
        for tick in range(16):
            clock.now = max(clock.now, tick * health_adapters.PERIODS['main_ci'])
            started = clock.now
            result = m.reconcile_batch(tmp_path)
            assert clock.now - started <= 120
            results.append(result)
    assert results[0]['unconfirmed'] > 0  # Partial work is never prematurely green.
    assert any(result['unconfirmed'] == 0 for result in results[:4])
    assert all(result['unconfirmed'] == 0 for result in results[4:])
    assert clock.peak <= 40
    assert m.RECEIPT_SECONDS == 600 and m.RENEW_AFTER_SECONDS == 480
    assert health_adapters.PERIODS['main_ci'] == 120
