"""Start ephemeral nats-server for integration tests."""

from __future__ import annotations

import asyncio
import functools
import unittest
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path


def strict_mode() -> bool:
    ci = os.environ.get("CI", "").lower()
    if ci in ("1", "true", "yes"):
        return True
    if os.environ.get("BOB_REQUIRE_NATS") == "1":
        return True
    return False


def require_or_skip(condition: bool, reason: str):
    if condition:
        def decorator(obj):
            return obj
        return decorator

    def decorator(obj):
        if strict_mode():
            if isinstance(obj, type):
                @classmethod
                def setUpClass(cls):
                    raise AssertionError(f"required dependency missing in strict mode: {reason}")
                obj.setUpClass = setUpClass
                return obj
            else:
                if asyncio.iscoroutinefunction(obj):
                    @functools.wraps(obj)
                    async def async_wrapper(*args, **kwargs):
                        raise AssertionError(f"required dependency missing in strict mode: {reason}")
                    return async_wrapper
                else:
                    @functools.wraps(obj)
                    def sync_wrapper(*args, **kwargs):
                        raise AssertionError(f"required dependency missing in strict mode: {reason}")
                    return sync_wrapper
        else:
            return unittest.skip(reason)(obj)
    return decorator


def find_nats_binary() -> str | None:
    for cand in (
        os.environ.get("NAT_SERVER_BIN"),
        shutil.which("nats-server"),
        str(Path.home() / ".local/bin/nats-server"),
    ):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class EphemeralNats:
    def __init__(self, secured: bool = False, extra_conf: str = "") -> None:
        self.secured = secured
        self.extra_conf = extra_conf
        self.passwords = {node: secrets.token_urlsafe(24) for node in ("mini", "macbook", "nucbox", "dashboard", "coord", "coordop")}
        self.bin = find_nats_binary()
        self.proc: subprocess.Popen | None = None
        self.tmp: tempfile.TemporaryDirectory | None = None
        self.port = 0
        self.http_port = 0
        self.url = ""

    def start(self) -> None:
        if not self.bin:
            raise unittest.SkipTest("nats-server binary not found; run cluster/install-nats.sh")
        self.tmp = tempfile.TemporaryDirectory()
        store = Path(self.tmp.name) / "js"
        store.mkdir()
        self.port = free_port()
        self.http_port = free_port()
        conf = Path(self.tmp.name) / "nats.conf"
        conf.write_text(
            f"""
port: {self.port}
host: 127.0.0.1
http: {self.http_port}
jetstream {{
  store_dir: "{store}"
  max_mem_store: 67108864
  max_file_store: 134217728
}}
{self.extra_conf}
""",
            encoding="utf-8",
        )
        if self.secured:
            template = (Path(__file__).resolve().parents[1] / "nats.conf.template").read_text()
            template = re.sub(r"(?m)^port:.*$", f"port: {self.port}", template)
            template = re.sub(r"(?m)^http:.*$", f'http: "127.0.0.1:{self.http_port}"', template)
            template = template.replace("{{JS_STORE_DIR}}", str(store))
            for node, password in self.passwords.items():
                template = template.replace("{{" + node.upper() + "_TOKEN}}", password)
            conf.write_text(template, encoding="utf-8")
        self.proc = subprocess.Popen(
            [self.bin, "-c", str(conf)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.url = f"nats://127.0.0.1:{self.port}"
        for _ in range(50):
            if self.proc.poll() is not None:
                raise RuntimeError("nats-server exited early")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("nats-server did not become ready")

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.tmp:
            self.tmp.cleanup()
