"""Public watchdog hooks must remain live across implementation modules."""
import json
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cluster.control_plane import self_update, watchdog, watchdog_check


def test_complete_watchdog_source_fits_existing_review_budget():
    from cluster.control_plane.local_karen import source_context

    assert Path(watchdog.__file__).stat().st_size <= source_context.MAX_FILE_BYTES
    from cluster.control_plane import watchdog_io, watchdog_repo_checks, watchdog_log_checks

    paths = [Path(module.__file__) for module in (
        watchdog, watchdog_io, watchdog_repo_checks, watchdog_log_checks)]
    sizes = [path.stat().st_size for path in paths]
    assert all(size <= source_context.MAX_FILE_BYTES for size in sizes)
    assert sum(sizes) <= source_context.MAX_SOURCE_BYTES
    assert len(paths) + 2 <= source_context.MAX_API_CALLS


def test_log_writer_uses_current_public_paths(tmp_path, monkeypatch):
    for name in ("first", "second"):
        directory = tmp_path / name
        monkeypatch.setattr(watchdog, "LOG_DIR", directory)
        monkeypatch.setattr(watchdog, "WATCHDOG_LOG", directory / "watchdog.log")
        watchdog._write_watchdog_log({"marker": name})
        assert json.loads((directory / "watchdog.log").read_text()) == {"marker": name}


def test_install_check_preserves_public_subprocess_binding_and_errors(tmp_path, monkeypatch):
    run = mock.Mock(side_effect=[
        subprocess.CompletedProcess([], 0, "", ""),
        subprocess.CompletedProcess([], 0, "0", ""),
        subprocess.CompletedProcess([], 1, "", ""),
    ])
    monkeypatch.setenv("STORYPOOL_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(watchdog, "subprocess", SimpleNamespace(
        run=run, SubprocessError=subprocess.SubprocessError))
    assert watchdog.check_install_stale().passed
    result = watchdog.check_install_stale()
    assert not result.passed and result.execution_error and not result.can_repair
    assert run.call_args_list[0].args[0] == ["git", "fetch", "origin", "--dry-run"]
    assert run.call_args_list[2].args[0] == ["git", "fetch", "origin", "--dry-run"]


def test_peer_sync_uses_current_public_storage_hook(tmp_path, monkeypatch):
    timestamp = "2026-01-01T00:00:00Z"
    data = {"machines": {"nucbox": {"timestamp": timestamp, "state": "yellow"}}}
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(data).encode()
    store, status = mock.Mock(), mock.Mock()
    monkeypatch.setattr(watchdog, "LOG_DIR", tmp_path)
    monkeypatch.setattr(watchdog, "PEER_URLS", {"nucbox": "https://peer.invalid"})
    monkeypatch.setattr(watchdog, "store_peer_engine", store)
    monkeypatch.setattr(watchdog, "_peer_sync_status", status)
    with mock.patch("urllib.request.urlopen", return_value=response):
        assert watchdog.sync_peer_health("mini")
    store.assert_called_once_with("nucbox", data)
    status.assert_called_once_with("nucbox", True)
    row = json.loads((tmp_path / "machines/nucbox/watchdog.log").read_text())
    assert row["timestamp"] == timestamp


def test_repair_preserves_public_command_binding_and_exit_status(monkeypatch):
    run = mock.Mock(side_effect=[subprocess.CompletedProcess([], 0),
                                 subprocess.CompletedProcess([], 1)])
    monkeypatch.setattr(watchdog, "subprocess", SimpleNamespace(
        run=run, SubprocessError=subprocess.SubprocessError))
    assert watchdog.repair_restart_service("dispatch", "nucbox")
    assert not watchdog.repair_restart_service("dispatch", "nucbox")
    assert run.call_args.args[0] == ["systemctl", "--user", "restart", "storypool-dispatch.service"]


def test_head_lag_uses_current_public_threshold(tmp_path, monkeypatch):
    update_log = tmp_path / "update.jsonl"
    update_log.write_text(json.dumps({
        "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()}))
    monkeypatch.setattr(self_update, "UPDATE_LOG_FILE", update_log)
    monkeypatch.setattr(self_update, "check_head_lag", lambda: {
        "lagging": True, "local_head": "aaaa", "remote_head": "bbbb", "commits_behind": 1})
    monkeypatch.setattr(watchdog, "HEAD_LAG_ALERT_MINUTES", 30)
    assert not watchdog.check_head_lag().passed
    monkeypatch.setattr(watchdog, "HEAD_LAG_ALERT_MINUTES", 60)
    assert watchdog.check_head_lag().passed


def test_dashboard_probe_crosses_real_check_subprocess(tmp_path, monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/health" else 404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("STORYPOOL_DASHBOARD_PORT", str(server.server_port))
    try:
        result = watchdog_check.run_check(watchdog.check_service_stuck,
                                          "dashboard", tmp_path / "absent.log")
        assert result.passed
        assert result.message == "dashboard answering /health"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
