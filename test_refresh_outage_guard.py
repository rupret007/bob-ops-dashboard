#!/usr/bin/env python3
"""Integration proof that an incomplete public collection cannot replace the board."""
from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class RefreshOutageGuardTests(unittest.TestCase):
    def test_public_metadata_outage_leaves_generated_artifacts_byte_stable(self):
        before = {
            name: (ROOT / name).read_bytes()
            for name in ("index.html", "status.json")
        }

        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp)
            fake_gh = fake_bin / "gh"
            fake_gh.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json
                    import sys

                    if len(sys.argv) < 3 or sys.argv[1] != "api":
                        raise SystemExit(2)
                    path = sys.argv[2].split("?", 1)[0]
                    parts = path.split("/")
                    if len(parts) < 3 or parts[0] != "repos":
                        raise SystemExit(2)

                    owner, repo = parts[1], parts[2]
                    if len(parts) == 3:
                        if owner == "rupret007" and repo == "webjam":
                            raise SystemExit(1)
                        print(json.dumps({
                            "name": repo,
                            "private": False,
                            "default_branch": "main",
                            "html_url": f"https://github.com/{owner}/{repo}",
                        }))
                    elif parts[3] == "commits":
                        print(json.dumps({
                            "sha": "0123456789abcdef",
                            "commit": {
                                "committer": {"date": "2026-09-03T20:00:00Z"},
                                "message": "fixture tip",
                            },
                        }))
                    elif parts[3] == "pulls":
                        print("[]")
                    elif parts[3:5] == ["actions", "runs"]:
                        print('{"workflow_runs": []}')
                    elif parts[3] == "releases":
                        print("[]")
                    else:
                        raise SystemExit(2)
                    """
                )
            )
            fake_gh.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
            env["BOB_DASHBOARD_APPLY_DECISIONS"] = "0"
            env.pop("GH_TOKEN", None)
            env.pop("GITHUB_TOKEN", None)

            result = subprocess.run(
                ["bash", str(ROOT / "refresh.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to replace the last truthful dashboard", result.stderr)
        self.assertIn("rupret007/webjam", result.stderr)
        for name, expected in before.items():
            self.assertEqual((ROOT / name).read_bytes(), expected, name)


if __name__ == "__main__":
    unittest.main()
