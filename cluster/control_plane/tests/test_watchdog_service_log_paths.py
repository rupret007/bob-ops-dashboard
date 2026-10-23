"""Health observes actual rotated writer output and preserves unknown states."""
import os
import time
from datetime import datetime, timezone

import pytest

from cluster.control_plane import watchdog
from cluster.control_plane.dispatcher import Dispatcher, DispatchDecision
from cluster.control_plane.health_state import unknown
from cluster.control_plane.shadow_runner import ShadowRunner, ShadowDecision


def configured_path(root, service):
    return root / watchdog.SERVICE_LOG_FILES[service].relative_to(watchdog.LOG_DIR)


@pytest.mark.parametrize('service', ['dispatch', 'shadow'])
def test_missing_service_evidence_stays_unconfirmed(tmp_path, service):
    check = watchdog.check_service_stuck(service, configured_path(tmp_path, service))
    assert unknown(check.to_dict())


@pytest.mark.parametrize('service', ['dispatch', 'shadow'])
def test_empty_service_directory_cannot_look_healthy(tmp_path, service):
    (tmp_path / service).mkdir()
    check = watchdog.check_service_stuck(service, configured_path(tmp_path, service))
    assert unknown(check.to_dict())


@pytest.mark.parametrize('service', ['dispatch', 'shadow'])
def test_actual_rotating_writer_output_is_known_then_stale(tmp_path, service):
    directory = tmp_path / service
    if service == 'dispatch':
        writer = Dispatcher('nucbox', directory, budget_tracker=object(), job_ledger=object())
        decision = DispatchDecision(datetime.now(timezone.utc).isoformat(), 'skip', 'No eligible work', 'nucbox')
    else:
        writer = ShadowRunner('nucbox', directory, budget_tracker=object())
        decision = ShadowDecision(datetime.now(timezone.utc).isoformat(), 'dispatch', 'would_skip', 'No eligible work', 'nucbox')
    old = directory / (service + '-2000-01-01.jsonl')
    old.write_text('{}\n')
    stale = time.time() - (watchdog.STUCK_THRESHOLD_MINUTES + 1) * 60
    os.utime(old, (stale, stale))
    writer._write_decision(decision)
    check = watchdog.check_service_stuck(service, configured_path(tmp_path, service))
    assert check.passed and not unknown(check.to_dict())
    os.utime(writer._log_file(), (stale, stale))
    os.utime(directory, None)  # Directory activity cannot disguise stale logs.
    check = watchdog.check_service_stuck(service, configured_path(tmp_path, service))
    assert not check.passed and not unknown(check.to_dict())
    assert check.repair_action == 'restart_service'
