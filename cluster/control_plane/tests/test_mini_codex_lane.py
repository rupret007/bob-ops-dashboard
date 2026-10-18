from unittest.mock import MagicMock
from cluster.control_plane import dispatcher as d


def _disp(machine):
    cls = next(c for c in vars(d).values()
               if isinstance(c, type) and hasattr(c, "check_provider_allowed"))
    x = cls.__new__(cls)
    x.machine = machine
    x.budget = MagicMock()
    x.budget.weekly_pct.return_value = 10.0
    x.budget.can_use.return_value = (True, "ok")
    return x


def _disabled_mini_lane():
    mini = _disp("mini")
    assert mini.check_provider_allowed("codex", {})[0]
    assert not mini.check_provider_allowed("codex", {"codex": 1})[0]


def test_nucbox_keeps_two_codex_lanes():
    nuc = _disp("nucbox")
    assert nuc.check_provider_allowed("codex", {"codex": 1})[0]
    assert not nuc.check_provider_allowed("codex", {"codex": 2})[0]


def test_weekly_cap_still_blocks_mini():
    mini = _disp("mini")
    mini.budget.weekly_pct.return_value = 80.0
    assert not mini.check_provider_allowed("codex", {})[0]


def test_mini_codex_lane_off_until_shared_claims_exist():
    assert not _disp("mini").check_provider_allowed("codex", {})[0]
