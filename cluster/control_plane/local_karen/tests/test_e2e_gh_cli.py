"""End-to-end tests with fake gh CLI emitting v2.93 field set.

Tests that the installed entrypoint works with the actual gh CLI output format.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


FAKE_GH_PR_VIEW = {
    "number": 88,
    "title": "Test PR for e2e",
    "author": {"login": "testuser"},
    "headRefOid": "abc123def456789012345678901234567890abcd",
    "isDraft": False,
    "changedFiles": 2,
    "deletions": 5,
    "additions": 10,
    "files": [
        {"path": "file1.py", "additions": 5, "deletions": 2},
        {"path": "file2.py", "additions": 5, "deletions": 3},
    ],
}

FAKE_GH_PR_CHECKS_V293 = [
    {"bucket": "pass", "name": "Build", "state": "SUCCESS"},
    {"bucket": "pass", "name": "Test", "state": "SUCCESS"},
    {"bucket": "skipping", "name": "Optional", "state": "SKIPPED"},
]

FAKE_GH_PR_DIFF = """diff --git a/file1.py b/file1.py
index 1234567..abcdefg 100644
--- a/file1.py
+++ b/file1.py
@@ -1,5 +1,8 @@
+# New feature
 def hello():
-    print("hello")
+    print("hello world")
+
+def new_function():
+    return 42
"""


def create_fake_gh_script(tmpdir: Path) -> Path:
    """Create a fake gh CLI that emits v2.93 field set.

    This mimics gh version 2.93.0 which does NOT have 'conclusion' field
    for `gh pr checks`, only has: bucket, completedAt, description, event,
    link, name, startedAt, state, workflow.
    """
    fake_gh = tmpdir / "gh"

    pr_view_json = json.dumps(FAKE_GH_PR_VIEW)
    checks_json = json.dumps(FAKE_GH_PR_CHECKS_V293)

    script = f'''#!/usr/bin/env python3
import sys
import json

args = sys.argv[1:]

if args[:2] == ["pr", "view"]:
    # gh pr view <num> --repo <repo> --json <fields>
    print({repr(pr_view_json)})
elif args[:2] == ["pr", "checks"]:
    # gh pr checks <num> --repo <repo> --json <fields>
    json_fields = None
    for i, arg in enumerate(args):
        if arg == "--json" and i + 1 < len(args):
            json_fields = args[i + 1]
            break

    if json_fields and "conclusion" in json_fields:
        # v2.93 does NOT support 'conclusion' field
        print("Unknown JSON field \\"conclusion\\"", file=sys.stderr)
        print("Available fields: bucket, completedAt, description, event, link, name, startedAt, state, workflow", file=sys.stderr)
        sys.exit(1)

    print({repr(checks_json)})
elif args[:2] == ["pr", "diff"]:
    print("""{FAKE_GH_PR_DIFF}""")
elif args[:2] == ["auth", "status"]:
    sys.exit(0)
else:
    print(f"fake-gh: unknown command: {{args}}", file=sys.stderr)
    sys.exit(1)
'''

    fake_gh.write_text(script)
    fake_gh.chmod(stat.S_IRWXU)
    return fake_gh


class TestE2EWithFakeGH(unittest.TestCase):
    """End-to-end tests using a fake gh CLI."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.fake_gh = create_fake_gh_script(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_github_client_with_v293_fields(self):
        """Test that github_client.py works with gh v2.93 output."""
        from cluster.control_plane.local_karen.github_client import (
            _parse_ci_status,
            _run_gh,
        )

        result = _parse_ci_status(FAKE_GH_PR_CHECKS_V293)

        self.assertEqual(result["Build"], "success")
        self.assertEqual(result["Test"], "success")
        self.assertEqual(result["Optional"], "skipped")

    def test_fetch_pr_info_with_fake_gh(self):
        """Test fetch_pr_info with fake gh that rejects 'conclusion'."""
        import subprocess
        from unittest.mock import patch

        env = os.environ.copy()
        env["PATH"] = f"{self.tmpdir}:{env.get('PATH', '')}"

        def mock_run_gh(args, timeout=30):
            result = subprocess.run(
                [str(self.fake_gh)] + args,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            if result.returncode != 0:
                from cluster.control_plane.local_karen.github_client import GitHubError
                raise GitHubError(f"gh {' '.join(args)} failed: {result.stderr}")
            return result.stdout

        with patch("cluster.control_plane.local_karen.github_client._run_gh", mock_run_gh):
            from cluster.control_plane.local_karen.github_client import fetch_pr_info

            pr = fetch_pr_info("rupret007", "Turdanoid", 88)

            self.assertEqual(pr.number, 88)
            self.assertEqual(pr.head_sha, "abc123def456789012345678901234567890abcd")
            self.assertEqual(pr.title, "Test PR for e2e")
            self.assertFalse(pr.is_draft)
            self.assertEqual(pr.ci_status["Build"], "success")
            self.assertEqual(pr.ci_status["Test"], "success")
            self.assertEqual(pr.ci_status["Optional"], "skipped")

    def test_fake_gh_rejects_conclusion_field(self):
        """Verify that the fake gh actually rejects 'conclusion' field."""
        result = subprocess.run(
            [
                str(self.fake_gh),
                "pr", "checks", "88",
                "--repo", "rupret007/Turdanoid",
                "--json", "name,conclusion,status",
            ],
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown JSON field", result.stderr)
        self.assertIn("conclusion", result.stderr)

    def test_fake_gh_accepts_bucket_state_fields(self):
        """Verify that the fake gh accepts bucket/state fields."""
        result = subprocess.run(
            [
                str(self.fake_gh),
                "pr", "checks", "88",
                "--repo", "rupret007/Turdanoid",
                "--json", "name,state,bucket",
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(len(data), 3)
        self.assertEqual(data[0]["bucket"], "pass")


class TestNoStaleConclusion(unittest.TestCase):
    """Verify no code path still requests 'conclusion' field."""

    def test_github_client_uses_correct_fields(self):
        """Verify github_client.py uses state/bucket, not conclusion."""
        import inspect
        from cluster.control_plane.local_karen import github_client

        source = inspect.getsource(github_client)

        lines_with_json = [
            line for line in source.split("\n")
            if "--json" in line and "pr" in line.lower()
        ]

        for line in lines_with_json:
            self.assertNotIn(
                "conclusion",
                line,
                f"Found 'conclusion' in gh --json call: {line.strip()}"
            )

    def test_no_conclusion_in_local_karen_modules(self):
        """Scan all local_karen Python files for 'conclusion' in gh calls."""
        import cluster.control_plane.local_karen as karen_module

        karen_dir = Path(karen_module.__file__).parent

        for py_file in karen_dir.glob("**/*.py"):
            if "__pycache__" in str(py_file):
                continue

            content = py_file.read_text()

            for i, line in enumerate(content.split("\n"), 1):
                if "--json" in line and "conclusion" in line.lower():
                    if "pr" in line.lower() or "checks" in line.lower():
                        self.fail(
                            f"{py_file.name}:{i} uses 'conclusion' in gh call: {line.strip()}"
                        )


if __name__ == "__main__":
    unittest.main()
