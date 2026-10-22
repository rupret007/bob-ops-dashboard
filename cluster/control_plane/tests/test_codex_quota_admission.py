"""Worker admission must obey the measured plan cap, including unknown quota."""
import pytest
from cluster.control_plane import budget_tracker as b, codex_quota as q


@pytest.mark.parametrize('pct,allowed', [(0.0, True), (74.999, True), (75.0, False),
                                       (100.0, False), (None, False)])
def test_budget_admission_uses_plan_percentage_and_stops_at_75(tmp_path, monkeypatch, pct, allowed):
    quota = dict(q._empty(), status='ok' if pct is not None else 'unknown', weekly_pct=pct)
    monkeypatch.setattr(b, 'read_codex_quota', lambda: quota)
    tracker = b.BudgetTracker(tmp_path, b.BudgetCaps(codex_weekly_pct=90))
    tracker.record('codex', cost_usd=49)
    assert tracker.weekly_pct('codex') == pct
    assert tracker.can_use('codex')[0] is allowed
    summary = tracker.summary()['codex']
    assert summary['cost_usd'] is None
    assert summary['weekly_pct'] == pct
    assert summary['quota'] == quota
    assert tracker.get_usage('codex').cost_usd == 49
    tracker.record('cursor', cost_usd=25)
    assert tracker.weekly_pct('cursor') == 50
    assert tracker.can_use('cursor') == (True, 'ok')


def test_unknown_codex_quota_blocks_dispatcher_and_conductor(tmp_path, monkeypatch):
    from cluster.control_plane.conductor import Conductor
    from cluster.control_plane.dispatcher import Dispatcher
    monkeypatch.setattr(b, 'read_codex_quota', q._empty)
    tracker = b.BudgetTracker(tmp_path)
    dispatcher, conductor = Dispatcher.__new__(Dispatcher), Conductor.__new__(Conductor)
    dispatcher.machine, dispatcher.budget = 'nucbox', tracker
    conductor.budget = tracker
    for allowed, reason in (dispatcher.check_provider_allowed('codex', {}),
                            conductor._can_use_codex()):
        assert allowed is False
        assert 'quota unknown' in reason
