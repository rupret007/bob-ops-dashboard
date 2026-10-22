"""Old dispatch decisions must not replace current machine measurements."""
import json
from types import SimpleNamespace
from cluster.control_plane.dashboard.data_collector import DataCollector


def test_current_guard_measurements_survive_old_dispatch_snapshot(tmp_path, monkeypatch):
    # Isolate telemetry freshness from policy changes; production90 is tested separately.
    from functools import partial
    from cluster.control_plane.dashboard.data_collector import GuardValues
    monkeypatch.setattr("cluster.control_plane.dashboard.data_collector.GuardValues",
                        partial(GuardValues, swap_limit=80.0))
    old = tmp_path / 'dispatch' / 'dispatch-status.json'
    old.parent.mkdir()
    old.write_text(json.dumps({'timestamp': '2020-01-01T00:00:00Z', 'summary': {
        'decisions': [{'guard_values': {'load': 99.0, 'disk': 1.0, 'lanes': 0}}]}}))
    monkeypatch.setattr('os.getloadavg', lambda: (2.5, 2.0, 1.0))
    monkeypatch.setattr('cluster.control_plane.guards.get_swap_pct', lambda: 84.0)
    monkeypatch.setattr('shutil.disk_usage', lambda _: SimpleNamespace(free=40 * 1024**3))
    collector = DataCollector(data_dir=tmp_path)
    collector.machine = 'mini'
    guard = collector._collect_guards()
    assert guard.load == 2.5
    assert guard.disk_gb == 40.0
    assert guard.swap_pct == 84.0
    assert guard.swap_limit == 80.0
    assert guard.guards_passed is False
