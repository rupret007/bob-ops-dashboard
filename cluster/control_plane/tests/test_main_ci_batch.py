"""Large merge histories make durable progress within the health probe budget."""
import json
import subprocess
from unittest.mock import Mock, patch

import pytest

from cluster.control_plane import main_ci as m, health_adapters


def prs():
    return [dict(number=n, repository=dict(nameWithOwner='owner/repo')) for n in range(1, 4)]


def test_deadline_preserves_progress_across_restart_without_claiming_missing_checks_pass(tmp_path):
    clock = [0]
    seen = []
    def one(client, pr):
        seen.append(pr['number'])
        clock[0] += 60
        return dict(repo='owner/repo', pr=pr['number'], main_ci='success')
    with patch.object(m, 'time', Mock(monotonic=lambda: clock[0], time=lambda: 10000 + clock[0])), \
         patch.object(m.ClaimClient, 'from_env'), patch.object(m, 'recent_prs', return_value=prs()), \
         patch.object(m, 'reconcile_one', side_effect=one):
        first = m.reconcile_batch(tmp_path)
        assert seen == [1, 2]
        assert first['unconfirmed'] == 1
        assert first['results'][-1]['main_ci'] == 'deferred'
        second = m.reconcile_batch(tmp_path)
        assert seen == [1, 2, 3]
        assert second['unconfirmed'] == 0 and second['attempted'] == 1
        clock[0] += 601
        m.reconcile_batch(tmp_path)
        assert seen[3:] == [1, 2]  # Expired observations cannot be reused as green.
    assert m._deadline.get() is None


def test_slow_failed_first_item_cannot_starve_tail_next_tick(tmp_path):
    clock = [0]
    seen = []
    def one(client, pr):
        seen.append(pr['number'])
        clock[0] += 120
        return dict(repo='owner/repo', pr=pr['number'], main_ci='deferred')
    with patch.object(m, 'time', Mock(monotonic=lambda: clock[0], time=lambda: 10000 + clock[0])), \
         patch.object(m.ClaimClient, 'from_env'), patch.object(m, 'recent_prs', return_value=prs()), \
         patch.object(m, 'reconcile_one', side_effect=one):
        for _ in range(3):
            assert m.reconcile_batch(tmp_path)['unconfirmed'] == 3
    assert seen == [1, 2, 3]


def test_commands_cannot_outlive_remaining_batch_budget():
    token = m._deadline.set(120)
    try:
        with patch.object(m, 'time', Mock(monotonic=lambda: 119)), patch.object(m.subprocess, 'run') as run:
            m.command(['gh', 'api', 'example'])
            assert run.call_args.kwargs['timeout'] == 1
        with patch.object(m, 'time', Mock(monotonic=lambda: 120)), patch.object(m.subprocess, 'run') as run:
            with pytest.raises(subprocess.TimeoutExpired):
                m.command(['gh', 'api', 'example'])
            run.assert_not_called()
    finally:
        m._deadline.reset(token)


@pytest.mark.parametrize('status,unconfirmed', [('failure', 0), ('pending', 1), ('deferred', 1)])
def test_adapter_never_marks_failed_pending_or_partial_followup_green(status, unconfirmed):
    with patch.object(m, 'reconcile_batch', return_value=dict(
            total=1, unconfirmed=unconfirmed, results=[dict(main_ci=status)])):
        result = health_adapters.collect('main_ci', 'nucbox', True)
    assert not result['checks'][0]['passed']
    assert result['checks'][0]['unknown'] == (status != 'failure')
