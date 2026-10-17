from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from live import agent


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


class ActiveWorktreeTests(unittest.TestCase):
    def test_finds_lane_checkouts_under_root_and_skips_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("lane-a", "lane-b"):
                (root / name).mkdir()
                _git(root / name, "init", "-q")
            (root / "not-a-repo").mkdir()
            old = time.time() - 10 * 3600
            os.utime(root / "lane-b", (old, old))
            for f in [root / "lane-b" / ".git", *(root / "lane-b" / ".git").rglob("*")]:
                os.utime(f, (old, old))
            found = {p.name for p in agent.active_worktrees([root])}
            self.assertEqual(found, {"lane-a"})

    def test_committed_delta_counts_lines_from_new_commits(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _git(r, "init", "-q")
            _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "a")
            prev: dict = {}
            self.assertEqual(agent.committed_delta(prev, r), (0, 0))
            (r / "f.txt").write_text("1\n2\n3\n")
            _git(r, "add", "f.txt")
            _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "b")
            self.assertEqual(agent.committed_delta(prev, r), (3, 0))
            self.assertEqual(agent.committed_delta(prev, r), (0, 0))


class ScreenRcTests(unittest.TestCase):
    def test_screen_call_ignores_nonzero_exit(self):
        src = Path(agent.__file__).read_text()
        self.assertIn('subprocess.run(\n                ["screen", "-ls"]', src)


if __name__ == "__main__":
    unittest.main()


class GateTotalsTests(unittest.TestCase):
    def test_dedupes_by_pr_and_sha_latest_wins(self):
        from live import aggregator
        gates = [
            {"pr": 142, "sha": "60aa507b", "verdict": "FAIL"},
            {"pr": 142, "sha": "0c0128c2", "verdict": "PASS"},
            {"pr": 142, "sha": "0c0128c2", "verdict": "PASS"},
            {"pr": 146, "sha": "513c2e5b", "verdict": "REQUESTED"},
        ]
        out = aggregator.gate_totals({"merged_today": 3}, gates)
        self.assertEqual((out["gates_pass"], out["gates_fail"], out["merged_today"]), (1, 1, 3))


class ReadHeadTests(unittest.TestCase):
    def test_reads_head_without_git_subprocess(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _git(r, "init", "-q")
            _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "a")
            want = subprocess.check_output(["git", "-C", td, "rev-parse", "HEAD"], text=True).strip()
            self.assertEqual(agent.read_head_sha(r), want)
            _git(r, "pack-refs", "--all")
            self.assertEqual(agent.read_head_sha(r), want)
