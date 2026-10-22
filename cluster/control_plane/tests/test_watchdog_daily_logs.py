"""Watchdog configuration must observe the services' daily JSON log files."""

import os
from datetime import datetime, timedelta, timezone

import pytest

from cluster.control_plane import watchdog


@pytest.fixture(params=["shadow", "dispatch"])
def daily_logs(request, tmp_path):
    service = request.param
    directory = tmp_path / service
    directory.mkdir()
    # Translate the real configured path, preserving any obsolete filename.
    configured = tmp_path / watchdog.SERVICE_LOG_FILES[service].relative_to(watchdog.LOG_DIR)
    today = datetime.now(timezone.utc)
    previous = directory / f"{service}-{(today - timedelta(days=1)):%Y-%m-%d}.jsonl"
    previous.write_text("Traceback\n" * 6)
    old = (today - timedelta(hours=2)).timestamp()
    os.utime(previous, (old, old))
    current = directory / f"{service}-{today:%Y-%m-%d}.jsonl"
    return service, configured, current


@pytest.mark.parametrize("age_minutes, passed", [(5, True), (31, False)])
def test_daily_writer_activity_uses_existing_stale_threshold(daily_logs, age_minutes, passed):
    service, configured, current = daily_logs
    current.write_text('{"event":"cycle_finished"}\n')
    modified = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).timestamp()
    os.utime(current, (modified, modified))

    result = watchdog.check_service_stuck(service, configured)

    assert result.passed is passed
    assert result.name == f"{service}_stuck"
    assert ("last active" if passed else "not updated") in result.message
    if not passed:
        assert result.can_repair
        assert result.repair_action == "restart_service"


@pytest.mark.parametrize("tracebacks, passed", [(0, True), (6, False)])
def test_daily_writer_tracebacks_ignore_yesterdays_log(daily_logs, tracebacks, passed):
    service, configured, current = daily_logs
    current.write_text("Traceback\n" * tracebacks + '{"event":"cycle_finished"}\n')

    result = watchdog.check_tracebacks(service, configured)

    assert result.passed is passed
    assert result.name == f"{service}_tracebacks"
    assert "No log" not in result.message
    if not passed:
        assert result.can_repair
        assert result.repair_action == "restart_service"
