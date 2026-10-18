"""Ephemeral nats-server running a rendered mesh (accounts) config."""

from __future__ import annotations

import signal
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from cluster.tests.nats_test_util import find_nats_binary, free_port


class MeshNats:
    def __init__(self) -> None:
        self.bin = find_nats_binary()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cfg_dir = self.dir / "cfg"
        self.conf = self.cfg_dir / "nats.mesh.conf"
        self.store = str(self.dir / "js")
        self.port = free_port()
        self.http = f"127.0.0.1:{free_port()}"
        self.url = f"nats://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        if not self.bin:
            self.tmp.cleanup()
            raise unittest.SkipTest("nats-server binary not found; run cluster/install-nats.sh")
        self.proc = subprocess.Popen([self.bin, "-c", str(self.conf)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            if self.proc.poll() is not None:
                raise RuntimeError("nats-server exited early (bad config?)")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("nats-server did not become ready")

    def reload(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGHUP)
            time.sleep(0.6)

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.tmp.cleanup()
