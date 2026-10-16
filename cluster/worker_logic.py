"""Pure logic for worker job handling (testable without NATS)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from cluster.sanitize import sanitize_tail
from cluster.spec import SpecError, validate_job_spec


class JobAction(str, Enum):
    ACK = "ack"
    NAK = "nak"
    TERM = "term"
    SKIP = "skip"


@dataclass
class JobDecision:
    action: JobAction
    delay_s: float = 0.0
    reason: str = ""


def parse_job_payload(raw: bytes, supported_classes: set[str]) -> tuple[dict[str, Any] | None, JobDecision]:
    try:
        import json

        data = json.loads(raw.decode("utf-8"))
        spec = validate_job_spec(data)
    except (SpecError, ValueError, UnicodeDecodeError) as e:
        return None, JobDecision(JobAction.TERM, reason=str(e))

    if spec["class"] not in supported_classes:
        return spec, JobDecision(JobAction.SKIP, reason="unsupported class")

    return spec, JobDecision(JobAction.ACK, reason="accepted")


def check_load_ceiling(load_ceiling: float, load1: float | None = None) -> JobDecision | None:
    if load1 is None:
        load1 = os.getloadavg()[0]
    if load1 > load_ceiling:
        return JobDecision(JobAction.NAK, delay_s=30.0, reason=f"load1 {load1} > ceiling {load_ceiling}")
    return None


def build_result(
    job_id: str,
    status: str,
    rc: int,
    duration_s: float,
    tail: str,
) -> dict[str, Any]:
    return {
        "id": job_id,
        "status": status,
        "rc": rc,
        "duration_s": round(duration_s, 3),
        "tail": sanitize_tail(tail),
    }


def classify_run_outcome(rc: int, timed_out: bool) -> tuple[str, JobAction]:
    if timed_out:
        return "timeout", JobAction.NAK
    if rc == 0:
        return "ok", JobAction.ACK
    if rc < 0:
        return "error", JobAction.NAK
    return "failed", JobAction.TERM


RedeliveryTracker = Callable[[str], int]


def should_stop_redelivery(delivery_count: int, max_deliver: int = 3) -> bool:
    return delivery_count >= max_deliver
