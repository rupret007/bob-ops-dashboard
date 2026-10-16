"""Speed index 0–100: shared formula and weights."""
import math

WEIGHTS = {
    "loc_per_min": 0.30,
    "test_runs_per_min": 0.20,
    "actions_per_min": 0.20,
    "cpu_pct": 0.15,
    "active_lanes": 0.15,
}

FULL_SCALE = {
    "loc_per_min": 500.0,
    "test_runs_per_min": 6.0,
    "actions_per_min": 60.0,
    "cpu_pct": 100.0,
    "active_lanes": 12.0,
}


def _component_loc(loc_per_min: float) -> float:
    if loc_per_min <= 0:
        return 0.0
    # log scale: 500 loc/min => 1.0
    return min(1.0, math.log1p(loc_per_min) / math.log1p(FULL_SCALE["loc_per_min"]))


def _component_linear(value: float, full: float) -> float:
    if full <= 0:
        return 0.0
    return min(1.0, max(0.0, value / full))


def compute_speed_index(
    loc_per_min: float,
    test_runs_per_min: float,
    actions_per_min: float,
    cpu_pct: float,
    active_lanes: int,
) -> dict:
    parts = {
        "loc_per_min": _component_loc(loc_per_min),
        "test_runs_per_min": _component_linear(
            test_runs_per_min, FULL_SCALE["test_runs_per_min"]
        ),
        "actions_per_min": _component_linear(
            actions_per_min, FULL_SCALE["actions_per_min"]
        ),
        "cpu_pct": _component_linear(cpu_pct, FULL_SCALE["cpu_pct"]),
        "active_lanes": _component_linear(
            float(active_lanes), FULL_SCALE["active_lanes"]
        ),
    }
    score = sum(WEIGHTS[k] * parts[k] for k in WEIGHTS) * 100.0
    score = max(0.0, min(100.0, score))
    return {
        "score": round(score, 1),
        "weights": WEIGHTS,
        "full_scale": FULL_SCALE,
        "components": {k: round(parts[k] * 100.0, 1) for k in parts},
        "inputs": {
            "loc_per_min": loc_per_min,
            "test_runs_per_min": test_runs_per_min,
            "actions_per_min": actions_per_min,
            "cpu_pct": cpu_pct,
            "active_lanes": active_lanes,
        },
    }
