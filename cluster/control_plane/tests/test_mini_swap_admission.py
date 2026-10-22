"""The owner-approved Mini admission cutoff is strict at90percent."""
from unittest.mock import patch

import pytest

from cluster.control_plane import guards


@pytest.mark.parametrize("swap,allowed", [(80.0, True), (84.0, True), (89.99, True),
                                          (90.0, False), (91.0, False), (100.0, False)])
def test_mini_swap_admission_boundary(swap, allowed):
    result = guards.MachineGuards("mini", swap_fn=lambda: swap).check_swap()
    assert result.value == swap
    assert result.limit == 90.0
    assert result.passed is allowed
    if not allowed:
        assert ">= 90.0%" in result.reason


def test_unavailable_mini_swap_still_denies_admission():
    with patch.object(guards.platform, "system", return_value="Darwin"), patch.object(
        guards.subprocess, "run", side_effect=OSError("telemetry unavailable")
    ):
        result = guards.MachineGuards("mini").check_swap()
    assert result.value == 100.0
    assert result.limit == 90.0
    assert not result.passed
