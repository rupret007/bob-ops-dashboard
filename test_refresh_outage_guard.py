#!/usr/bin/env python3
"""Integration proof that an incomplete public collection cannot replace the board."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from offline_qa_fixtures import assert_fixture_calls, fixture_environment, prepare_generator


ROOT = Path(__file__).resolve().parent


class RefreshOutageGuardTests(unittest.TestCase):
    def test_public_metadata_outage_leaves_generated_artifacts_byte_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            generator = scratch / "generator"
            prepare_generator(ROOT, generator)
            before = {
                name: (generator / name).read_bytes()
                for name in ("index.html", "status.json")
            }
            env = fixture_environment(scratch, scenario="metadata-outage")
            result = subprocess.run(
                ["bash", str(generator / "refresh.sh")],
                cwd=generator,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing to replace the last truthful dashboard", result.stderr)
            self.assertIn("rupret007/webjam", result.stderr)
            calls = assert_fixture_calls(Path(env["BOB_DASHBOARD_FIXTURE_LOG"]))
            self.assertTrue(any(row["outcome"] == "fixture-outage" for row in calls))
            for name, expected in before.items():
                self.assertEqual((generator / name).read_bytes(), expected, name)


if __name__ == "__main__":
    unittest.main()
