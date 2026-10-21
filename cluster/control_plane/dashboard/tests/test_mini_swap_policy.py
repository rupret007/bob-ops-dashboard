"""The owner-approved swap policy agrees across admission and live display."""
import json
import re
from types import SimpleNamespace
from unittest import mock

import pytest

from cluster.control_plane.guards import MachineGuards, get_swap_pct
from cluster.control_plane.dashboard.data_collector import DataCollector
from cluster.control_plane.dashboard.server import _render_guards


@pytest.mark.parametrize("swap,allowed,color", [
    (59.0, True, "ok"),
    (80.0, True, "warning"),
    (84.0, True, "warning"),
    (89.999, True, "warning"),
    (90.0, False, "error"),
    (90.001, False, "error"),
    (100.0, False, "error"),
])
def test_swap_admission_and_current_dashboard_agree(tmp_path, monkeypatch, swap, allowed, color):
    monkeypatch.setattr("os.getloadavg", lambda: (2.0, 2.0, 2.0))
    monkeypatch.setattr("cluster.control_plane.guards.get_swap_pct", lambda: swap)
    monkeypatch.setattr("shutil.disk_usage", lambda _: SimpleNamespace(free=30 * 1024**3))
    old = tmp_path / "dispatch" / "dispatch-status.json"
    old.parent.mkdir()
    old.write_text(json.dumps({"timestamp": "2020-01-01T00:00:00Z", "summary": {
        "decisions": [{"guard_values": {"load": 99.0, "disk": 1.0, "lanes": 0}}]}}))
    collector = DataCollector(data_dir=tmp_path)
    collector.machine = "mini"
    observed = collector._collect_guards()
    result = MachineGuards("mini", swap_fn=lambda: swap).check_swap()
    assert result.passed is allowed
    assert result.value == swap
    assert result.limit == observed.swap_limit == 90.0
    assert observed.load == 2.0
    assert observed.disk_gb == 30.0
    assert observed.swap_pct == swap
    assert observed.guards_passed is allowed
    rendered = _render_guards(SimpleNamespace(machine="mini", guards=observed))
    swap_bar = re.search(r"Swap</span>.*?progress-fill progress-([a-z]+)", rendered, re.S)
    assert swap_bar and swap_bar.group(1) == color
    if not allowed:
        assert ">= 90.0%" in result.reason


def test_unknown_mini_swap_still_denies_admission():
    with mock.patch("cluster.control_plane.guards.platform.system", return_value="Darwin"), mock.patch(
        "cluster.control_plane.guards.subprocess.run", side_effect=OSError("telemetry unavailable")
    ):
        result = MachineGuards("mini", swap_fn=get_swap_pct).check_swap()
    assert result.value == 100.0
    assert result.limit == 90.0
    assert not result.passed


@pytest.mark.parametrize("load,disk,reason", [(10.0, 30.0, "load"), (2.0, 14.99, "disk")])
def test_swap_change_preserves_other_mini_guards(load, disk, reason):
    result = MachineGuards("mini", load_fn=lambda: load, swap_fn=lambda: 84.0,
                           disk_fn=lambda: disk).check_all()
    assert not result.passed
    assert reason in result.reason


def test_nucbox_does_not_use_mini_swap_policy():
    result = MachineGuards("nucbox", swap_fn=lambda: 100.0).check_swap()
    assert result.passed
    assert result.reason == "swap check not applicable"


def _alternate_namespace(monkeypatch, source, suffix):
    import importlib.util
    import sys
    alias = "cluster.control_plane.dashboard._policy_alias_" + suffix
    spec = importlib.util.spec_from_file_location(alias, source.__file__)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, alias, module)
    spec.loader.exec_module(module)
    monkeypatch.delitem(sys.modules, alias)
    return module


def test_alternate_collector_retains_live_bindings_after_alias_removal(tmp_path, monkeypatch):
    from functools import partial
    from cluster.control_plane.dashboard import data_collector
    module = _alternate_namespace(monkeypatch, data_collector, "collector")
    monkeypatch.setattr(module, "GuardValues", partial(data_collector.GuardValues, swap_limit=85.0))
    monkeypatch.setattr("os.getloadavg", lambda: (2.0, 2.0, 2.0))
    monkeypatch.setattr("cluster.control_plane.guards.get_swap_pct", lambda: 84.0)
    monkeypatch.setattr("shutil.disk_usage", lambda _: SimpleNamespace(free=30 * 1024**3))
    collector = module.DataCollector(data_dir=tmp_path)
    collector.machine = "mini"
    observed = collector._collect_guards()
    assert observed.swap_limit == 85.0
    assert observed.guards_passed
    assert data_collector.GuardValues(machine="mini", load=0.0, load_limit=10.0).swap_limit == 90.0


def test_alternate_renderer_retains_live_bindings_after_alias_removal(monkeypatch):
    from cluster.control_plane.dashboard import server
    module = _alternate_namespace(monkeypatch, server, "renderer")
    monkeypatch.setattr(module, "_escape", lambda value: "ALIAS:" + str(value))
    service = SimpleNamespace(status="running", name="widget", last_run_ago="now", error_count=0)
    rendered = module._render_services(SimpleNamespace(machine="mini", services=[service]))
    assert "ALIAS:widget" in rendered
    assert "ALIAS:now" in rendered
    assert server._escape("widget") == "widget"
