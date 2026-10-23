"""Synthetic operator intents; no credentials, broker, checkout or model calls."""
from contextvars import copy_context, Context
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from cluster.control_plane.local_karen import packet_scope as scope

AUTH = "a" * 64


def pr(**changes):
    return SimpleNamespace(**dict(owner="example", repo="project", number=1,
                                  head_sha="b" * 40, **changes))


def intent(tmp_path, **changes):
    value = dict(repo="example/project", pr=1, head="b" * 40,
                 issued_at=1000, expires_at=4600, entries=8, packet_bytes=96000,
                 authorization_sha256=AUTH)
    value.update(changes)
    path = tmp_path / "intent.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


def activate(path, **changes):
    options = dict(intent_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                   authorization_sha256=AUTH)
    options.update(changes)
    return scope.operator_packet_scope(pr(), path, **options)


def test_defaults_exact_scope_and_copied_context_close(tmp_path, monkeypatch):
    monkeypatch.setattr(scope.time, "time", lambda: 1000)
    path = intent(tmp_path)
    assert scope.packet_limits(pr()) == (4, 48000)
    with activate(path):
        assert scope.packet_limits(pr()) == (8, 96000)
        assert Context().run(scope.packet_limits, pr()) == (4, 48000)
        copied = copy_context()
    assert scope.packet_limits(pr()) == (4, 48000)
    with pytest.raises(ValueError):
        copied.run(scope.packet_limits, pr())


def test_exception_resets_scope_and_nested_activation_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(scope.time, "time", lambda: 1001)
    path = intent(tmp_path)
    with pytest.raises(RuntimeError):
        with activate(path):
            with pytest.raises(ValueError):
                with activate(path):
                    pytest.fail("Nested activation admitted")
            assert scope.packet_limits(pr()) == (8, 96000)
            raise RuntimeError("operator stopped")
    assert scope.packet_limits(pr()) == (4, 48000)


@pytest.mark.parametrize("field,value", [("owner", "other"), ("repo", "other"),
    ("number", 2), ("head_sha", "c" * 40), ("number", True), ("head_sha", "short")])
def test_identity_mismatch_before_or_during_scope(tmp_path, monkeypatch, field, value):
    monkeypatch.setattr(scope.time, "time", lambda: 1001)
    path = intent(tmp_path)
    changed = pr()
    setattr(changed, field, value)
    with pytest.raises(ValueError):
        with scope.operator_packet_scope(changed, path,
                intent_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                authorization_sha256=AUTH):
            pytest.fail("Mismatched intent admitted")
    with activate(path):
        with pytest.raises(ValueError):
            scope.packet_limits(changed)


@pytest.mark.parametrize("clock", [999, 4600, 4601])
def test_expiry_and_clock_rollback_fail_closed(tmp_path, monkeypatch, clock):
    path = intent(tmp_path)
    monkeypatch.setattr(scope.time, "time", lambda: clock)
    with pytest.raises(ValueError):
        with activate(path):
            pytest.fail("Invalid time admitted")
    monkeypatch.setattr(scope.time, "time", lambda: 1001)
    with activate(path):
        monkeypatch.setattr(scope.time, "time", lambda: clock)
        with pytest.raises(ValueError):
            scope.packet_limits(pr())
    assert scope.packet_limits(pr()) == (4, 48000)


@pytest.mark.parametrize("changes", [dict(entries=9), dict(entries=True),
    dict(packet_bytes=96001), dict(authorization_sha256="c" * 64), dict(pr=True),
    dict(head=123), dict(repo=[]), dict(issued_at=True), dict(issued_at=float("nan")),
    dict(expires_at=float("inf")), dict(expires_at=1000), dict(expires_at=4601),
    dict(extra="untrusted")])
def test_invalid_intents_do_not_activate(tmp_path, monkeypatch, changes):
    monkeypatch.setattr(scope.time, "time", lambda: 1001)
    with pytest.raises(ValueError):
        with activate(intent(tmp_path, **changes)):
            pytest.fail("Invalid intent admitted")
    assert scope.packet_limits(pr()) == (4, 48000)


@pytest.mark.parametrize("kind", ["hash", "digest", "authorization", "mode", "link",
                                  "directory", "fifo", "oversize", "duplicate", "missing", "uid"])
def test_untrusted_file_refused(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(scope.time, "time", lambda: 1001)
    path, options = intent(tmp_path), {}
    if kind in ("hash", "digest"):
        options["intent_sha256"] = "0" * 64 if kind == "hash" else "short"
    elif kind == "authorization":
        options["authorization_sha256"] = "short"
    elif kind == "mode":
        path.chmod(0o644)
    elif kind == "link":
        link = tmp_path / "link"
        link.symlink_to(path)
        path = link
    elif kind in ("directory", "fifo"):
        path.unlink()
        path.mkdir() if kind == "directory" else os.mkfifo(path, 0o600)
        options["intent_sha256"] = "0" * 64
    elif kind in ("oversize", "duplicate", "missing"):
        data = path.read_text()
        path.write_text(" " * 4097 if kind == "oversize" else
                        data[:-1] + ', "pr": 1}' if kind == "duplicate" else "{}")
    elif kind == "uid":
        monkeypatch.setattr(scope.os, "getuid", lambda: -1)
    digest = options.pop("intent_sha256", None)
    if digest is None:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises((ValueError, OSError)):
        with scope.operator_packet_scope(pr(), path, intent_sha256=digest,
                authorization_sha256=options.get("authorization_sha256", AUTH)):
            pytest.fail("Untrusted file admitted")
    assert scope.packet_limits(pr()) == (4, 48000)
