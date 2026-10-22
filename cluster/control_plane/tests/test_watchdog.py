"""Tests for watchdog self-healing service."""

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

import pytest

from cluster.control_plane import watchdog


def test_peer_health_requires_configured_url():
    with mock.patch.dict(watchdog.PEER_URLS, {"nucbox": ""}), \
            mock.patch("urllib.request.urlopen") as request:
        assert watchdog.sync_peer_health("mini") is False
        request.assert_not_called()


def test_peer_health_uses_configured_url_and_preserves_timestamp(tmp_path):
    timestamp = "2026-10-22T00:00:00+00:00"
    response = mock.MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps({
        "machines": {"nucbox": {"timestamp": timestamp, "state": "green", "detail": "Healthy"}}
    }).encode()
    with mock.patch.dict(watchdog.PEER_URLS, {"nucbox": "http://peer.example.invalid:8790/"}), \
            mock.patch.object(watchdog, "LOG_DIR", tmp_path), \
            mock.patch("urllib.request.urlopen", return_value=response) as request:
        assert watchdog.sync_peer_health("mini") is True
        request.assert_called_once_with("http://peer.example.invalid:8790/api/engine", timeout=5)
    row = json.loads((tmp_path / "machines/nucbox/watchdog.log").read_text())
    assert row["timestamp"] == timestamp
    assert row["checks"][0]["passed"] is True


class TestHealthChecks:
    """Tests for individual health check functions."""

    def test_check_service_stuck_file_not_found(self, tmp_path: Path):
        """Non-existent log file should pass."""
        result = watchdog.check_service_stuck("test", tmp_path / "nonexistent.log")
        assert result.passed is True
        assert "not found" in result.message.lower()

    def test_check_service_stuck_recent_log(self, tmp_path: Path):
        """Recently updated log should pass."""
        log_file = tmp_path / "test.log"
        log_file.write_text("test log entry\n")
        
        result = watchdog.check_service_stuck("test", log_file)
        assert result.passed is True
        assert "min ago" in result.message.lower()

    def test_check_service_stuck_old_log(self, tmp_path: Path):
        """Log not updated in 30+ min should fail."""
        log_file = tmp_path / "test.log"
        log_file.write_text("test log entry\n")
        
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=35)).timestamp()
        import os
        os.utime(log_file, (old_time, old_time))
        
        result = watchdog.check_service_stuck("test", log_file)
        assert result.passed is False
        assert "35" in result.message or "not updated" in result.message
        assert result.can_repair is True
        assert result.repair_action == "restart_service"

    def test_check_service_stuck_dir_with_logs(self, tmp_path: Path):
        """Directory with log files should check most recent."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "old.log").write_text("old")
        (log_dir / "new.jsonl").write_text("new")
        
        result = watchdog.check_service_stuck("test", log_dir)
        assert result.passed is True

    def test_check_tracebacks_none(self, tmp_path: Path):
        """Log with no tracebacks should pass."""
        log_file = tmp_path / "test.log"
        log_file.write_text("INFO: Normal operation\nINFO: More logs\n")
        
        result = watchdog.check_tracebacks("test", log_file)
        assert result.passed is True
        assert "0" in result.message

    def test_check_tracebacks_many(self, tmp_path: Path):
        """Log with many tracebacks should fail."""
        log_file = tmp_path / "test.log"
        lines = ["Traceback (most recent call last):\n"] * 10
        log_file.write_text("".join(lines))
        
        result = watchdog.check_tracebacks("test", log_file)
        assert result.passed is False
        assert "10" in result.message or "traceback" in result.message.lower()
        assert result.can_repair is True

    @mock.patch("shutil.which", return_value="/usr/local/bin/gh")
    def test_check_gh_available(self, mock_which):
        """gh in PATH should pass."""
        result = watchdog.check_gh_in_path()
        assert result.passed is True
        assert "/usr/local/bin/gh" in result.message

    @mock.patch("shutil.which", return_value=None)
    @mock.patch("os.path.isfile", return_value=False)
    def test_check_gh_missing(self, mock_isfile, mock_which):
        """Missing gh should fail."""
        result = watchdog.check_gh_in_path()
        assert result.passed is False
        assert result.can_repair is False

    @mock.patch("shutil.which", return_value="/usr/local/bin/codex")
    def test_check_codex_available(self, mock_which):
        """codex in PATH should pass."""
        result = watchdog.check_codex_in_path()
        assert result.passed is True

    @mock.patch("subprocess.run")
    def test_check_labels_exist_all_present(self, mock_run):
        """All required labels present should pass."""
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout=json.dumps([
                {"name": "needs-karen"},
                {"name": "karen-pass"},
                {"name": "karen-fail"},
            ])
        )
        
        result = watchdog.check_labels_exist("rupret007/test")
        assert result.passed is True

    @mock.patch("subprocess.run")
    def test_check_labels_missing(self, mock_run):
        """Missing labels should fail and offer repair."""
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout=json.dumps([{"name": "other-label"}])
        )
        
        result = watchdog.check_labels_exist("rupret007/test")
        assert result.passed is False
        assert "needs-karen" in result.message or "Missing" in result.message
        assert result.can_repair is True
        assert result.repair_action == "create_labels"


class TestRepairFunctions:
    """Tests for repair functions."""

    @mock.patch("subprocess.run")
    def test_repair_restart_service_mini(self, mock_run):
        """Test launchctl restart on Mini."""
        mock_run.return_value = mock.Mock(returncode=0)
        
        result = watchdog.repair_restart_service("conductor", "mini")
        assert result is True
        
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "launchctl" in call_args
        assert "kickstart" in call_args

    @mock.patch("subprocess.run")
    def test_repair_restart_service_nucbox(self, mock_run):
        """Test systemctl restart on NucBox."""
        mock_run.return_value = mock.Mock(returncode=0)
        
        result = watchdog.repair_restart_service("conductor", "nucbox")
        assert result is True
        
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "systemctl" in call_args
        assert "restart" in call_args

    @mock.patch("subprocess.run")
    def test_repair_create_labels(self, mock_run):
        """Test label creation."""
        mock_run.return_value = mock.Mock(returncode=0)
        
        result = watchdog.repair_create_labels("rupret007/test")
        assert result is True
        
        assert mock_run.call_count == 3


class TestWatchdogRun:
    """Tests for full watchdog run."""

    @mock.patch.object(watchdog, "check_service_stuck")
    @mock.patch.object(watchdog, "check_tracebacks")
    @mock.patch.object(watchdog, "check_gh_in_path")
    @mock.patch.object(watchdog, "check_codex_in_path")
    @mock.patch.object(watchdog, "check_labels_exist")
    @mock.patch.object(watchdog, "check_install_stale")
    @mock.patch.object(watchdog, "_write_watchdog_log")
    def test_run_watchdog_all_pass(
        self,
        mock_log,
        mock_stale,
        mock_labels,
        mock_codex,
        mock_gh,
        mock_tb,
        mock_stuck,
    ):
        """All checks passing should report no issues."""
        passing = watchdog.HealthCheck(name="test", passed=True, message="ok")
        mock_stuck.return_value = passing
        mock_tb.return_value = passing
        mock_gh.return_value = passing
        mock_codex.return_value = passing
        mock_labels.return_value = passing
        mock_stale.return_value = passing
        
        result = watchdog.run_watchdog(machine="nucbox")
        
        assert result.repairs_attempted == 0
        assert result.repairs_successful == 0
        assert result.escalated_to_jeff == []

    @mock.patch.object(watchdog, "check_service_stuck")
    @mock.patch.object(watchdog, "check_tracebacks")
    @mock.patch.object(watchdog, "check_gh_in_path")
    @mock.patch.object(watchdog, "check_codex_in_path")
    @mock.patch.object(watchdog, "check_labels_exist")
    @mock.patch.object(watchdog, "check_install_stale")
    @mock.patch.object(watchdog, "repair_restart_service", return_value=True)
    @mock.patch.object(watchdog, "_write_watchdog_log")
    def test_run_watchdog_with_repair(
        self,
        mock_log,
        mock_repair,
        mock_stale,
        mock_labels,
        mock_codex,
        mock_gh,
        mock_tb,
        mock_stuck,
    ):
        """Repairable issue should be auto-fixed."""
        passing = watchdog.HealthCheck(name="test", passed=True, message="ok")
        failing = watchdog.HealthCheck(
            name="conductor_stuck",
            passed=False,
            message="stuck",
            can_repair=True,
            repair_action="restart_service",
        )
        
        mock_stuck.return_value = failing
        mock_tb.return_value = passing
        mock_gh.return_value = passing
        mock_codex.return_value = passing
        mock_labels.return_value = passing
        mock_stale.return_value = passing
        
        result = watchdog.run_watchdog(machine="nucbox")
        
        assert result.repairs_attempted >= 1
        assert result.repairs_successful >= 1
        mock_repair.assert_called()


class TestEscalation:
    """Tests for NEEDS_JEFF.md escalation."""

    def test_escalate_to_jeff(self, tmp_path: Path, monkeypatch):
        """Test writing to NEEDS_JEFF.md."""
        monkeypatch.setattr(watchdog, "NEEDS_JEFF_FILE", tmp_path / "NEEDS_JEFF.md")
        
        watchdog._escalate_to_jeff("Test Issue", "Test details")
        
        content = (tmp_path / "NEEDS_JEFF.md").read_text()
        assert "Test Issue" in content
        assert "Test details" in content
        assert "Watchdog" in content

    def test_escalate_appends_to_existing(self, tmp_path: Path, monkeypatch):
        """Escalation should prepend to existing file."""
        needs_jeff = tmp_path / "NEEDS_JEFF.md"
        needs_jeff.write_text("# NEEDS_JEFF\n\nExisting content\n")
        monkeypatch.setattr(watchdog, "NEEDS_JEFF_FILE", needs_jeff)
        
        watchdog._escalate_to_jeff("New Issue", "New details")
        
        content = needs_jeff.read_text()
        assert "New Issue" in content
        assert "Existing content" in content
        assert content.index("New Issue") < content.index("Existing content")


class TestWatchdogLog:
    """Tests for watchdog logging."""

    def test_write_watchdog_log(self, tmp_path: Path, monkeypatch):
        """Test appending to watchdog.log."""
        log_dir = tmp_path / "storypool"
        monkeypatch.setattr(watchdog, "LOG_DIR", log_dir)
        monkeypatch.setattr(watchdog, "WATCHDOG_LOG", log_dir / "watchdog.log")
        
        entry = {"timestamp": "2026-10-18T06:00:00Z", "status": "ok"}
        watchdog._write_watchdog_log(entry)
        
        log_content = (log_dir / "watchdog.log").read_text()
        assert "2026-10-18" in log_content
        assert "ok" in log_content

    def test_watchdog_log_appends(self, tmp_path: Path, monkeypatch):
        """Multiple entries should append."""
        log_dir = tmp_path / "storypool"
        log_dir.mkdir(parents=True)
        monkeypatch.setattr(watchdog, "LOG_DIR", log_dir)
        monkeypatch.setattr(watchdog, "WATCHDOG_LOG", log_dir / "watchdog.log")
        
        watchdog._write_watchdog_log({"entry": 1})
        watchdog._write_watchdog_log({"entry": 2})
        
        lines = (log_dir / "watchdog.log").read_text().strip().split("\n")
        assert len(lines) == 2


def test_directory_age_does_not_hide_active_log(tmp_path):
    import os
    log = tmp_path / 'conductor.jsonl'
    log.write_text('active\n')
    old = (datetime.now(timezone.utc) - timedelta(hours=4)).timestamp()
    os.utime(tmp_path, (old, old))
    assert watchdog.check_service_stuck('conductor', tmp_path).passed
    os.utime(log, (old, old))
    assert not watchdog.check_service_stuck('conductor', tmp_path).passed


def test_hourly_conductor_grace_period(tmp_path):
    import os
    log = tmp_path / 'conductor.jsonl'
    log.write_text('tick\n')
    old = (datetime.now(timezone.utc) - timedelta(minutes=60)).timestamp()
    os.utime(log, (old, old))
    assert watchdog.check_service_stuck('conductor', log).passed
    assert not watchdog.check_service_stuck('dispatch', log).passed
