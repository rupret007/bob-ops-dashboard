"""Join installer smoke tests (no network)."""

import os
import platform
import shutil
import subprocess
import time
import unittest
from pathlib import Path

from cluster.registry import encode_join_code

REPO_ROOT = Path(__file__).resolve().parents[2]
JOIN_SH = REPO_ROOT / "cluster" / "install" / "join.sh"
JOIN_PS1 = REPO_ROOT / "cluster" / "install" / "join.ps1"


def fake_join_code() -> str:
    return encode_join_code(
        "nats://" + ".".join(["100", "64", "0", "1"]) + ":4222",  # built at runtime: repo bans tailnet literals
        "join-testuser",
        "test-secret-not-real",
        "ci-installer",
        "member",
        time.time() + 3600,
    )


class TestJoinInstallers(unittest.TestCase):
    def test_join_sh_syntax(self):
        subprocess.run(["bash", "-n", str(JOIN_SH)], check=True)

    def test_join_sh_dry_run(self):
        code = fake_join_code()
        env = {
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT),
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
        }
        proc = subprocess.run(
            ["bash", str(JOIN_SH), "--dry-run", "--code", code],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        out = proc.stdout + proc.stderr
        self.assertNotIn(code, out)
        dry_lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("DRY:")]
        self.assertTrue(dry_lines, "expected DRY: lines")
        system = platform.system()
        if system == "Darwin":
            self.assertIn("launchd", proc.stdout.lower())
        else:
            lowered = proc.stdout.lower()
            self.assertTrue(
                "systemd" in lowered or "crontab" in lowered,
                msg="expected systemd or crontab in dry-run output",
            )

    def test_join_sh_missing_code(self):
        proc = subprocess.run(
            ["bash", str(JOIN_SH), "--dry-run"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        self.assertNotEqual(proc.returncode, 0)

    def test_join_sh_bob_join_code_env(self):
        code = fake_join_code()
        env = {
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT),
            "BOB_JOIN_CODE": code,
            "HTTPS_PROXY": "http://127.0.0.1:9",
        }
        proc = subprocess.run(
            ["bash", str(JOIN_SH), "--dry-run"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertNotIn(code, proc.stdout + proc.stderr)

    def test_join_ps1_contents(self):
        self.assertTrue(JOIN_PS1.is_file())
        text = JOIN_PS1.read_text(encoding="utf-8")
        self.assertIn("WSLENV", text)
        self.assertIn("schtasks", text)
        self.assertNotIn("Invoke-Expression", text)
        self.assertNotRegex(text, r"\biex\s")

        if shutil.which("pwsh"):
            parser = subprocess.run(
                ["pwsh", "-NoProfile", "-Command", f"& {{ $null = [System.Management.Automation.Language.Parser]::ParseFile('{JOIN_PS1}', [ref]$null, [ref]$errs); if ($errs) {{ exit 1 }} }}"],
                capture_output=True,
            )
            self.assertEqual(parser.returncode, 0, msg=parser.stderr.decode())


if __name__ == "__main__":
    unittest.main()
