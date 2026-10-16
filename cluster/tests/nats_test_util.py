"""Start ephemeral nats-server for integration tests."""

from __future__ import annotations

import unittest
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path


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
    def __init__(self) -> None:
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
""",
            encoding="utf-8",
        )
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
