"""Public data migration, renderer escaping and paired artifact regression tests."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from offline_qa_fixtures import prepare_generator, fixture_environment, assert_fixture_calls
from render_dashboard import render_dashboard

ROOT = Path(__file__).resolve().parent


class DashboardTests(unittest.TestCase):
    def test_embedded_json_cannot_close_its_script(self):
        payload = {"generated_at_display": '</script><script>alert("test")</script>', "llm_work": [], "projects": []}
        page = render_dashboard(payload, ROOT)
        self.assertNotIn(payload["generated_at_display"], page)
        self.assertEqual(page.count("</script>"), 2)
        self.assertIn('\\u003c/script\\u003e', page)

    def test_refresh_migrates_legacy_snapshot_and_preserves_public_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            generator = scratch / "generator"
            prepare_generator(ROOT, generator)
            (generator / "status.json").write_text(json.dumps({
                "songs": [{"private_title": "RETIRED_CONTENT"}],
                "song_status": {"secret": "RETIRED_CONTENT"},
                "verify": {"hash": "RETIRED_AUTH"},
                "decisions": [{"id": "songs", "songs": "RETIRED_CONTENT"},
                              {"id": "adoptiq-live-cisco", "songs": "RETIRED_CONTENT", "decision": "hold"}],
            }))
            (generator / "llm-work-now.json").write_text(json.dumps({"work": [
                {"id": "codex", "repo": "rupret007/AdoptIQ", "task_title": "PRIVATE_CONTENT",
                 "goal": "PRIVATE_CONTENT", "status": "running", "source": "worker",
                 "pr_url": "https://github.com/rupret007/AdoptIQ/pull/42"},
                {"id": "grok", "task_title": "UNSUPPORTED_PROVIDER", "status": "idle"},
            ]}))
            env = fixture_environment(scratch)
            result = subprocess.run(["bash", str(generator / "refresh.sh")], cwd=generator,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            assert_fixture_calls(Path(env["BOB_DASHBOARD_FIXTURE_LOG"]))
            status = json.loads((generator / "status.json").read_text())
            for name in ("index.html", "status.json", "llm-work-now.json"):
                blob = (generator / name).read_text()
                for forbidden in ("RETIRED_CONTENT", "RETIRED_AUTH", "PRIVATE_CONTENT", "UNSUPPORTED_PROVIDER", '"songs"', '"song_status"', '"verify"'):
                    self.assertNotIn(forbidden, blob, name)
            self.assertEqual([r["id"] for r in status["llm_work"]], ["codex", "claude", "cursor", "gemini", "minimax"])
            self.assertNotEqual(status["llm_work"][0]["status"], "running")
            self.assertEqual(status["llm_work"][0]["pr_url"], "")
            webjam = next(p for p in status["projects"] if p["id"] == "webjam")
            self.assertEqual(webjam["draft_prs"], [{"number": 42, "url": "https://github.com/rupret007/webjam/pull/42", "draft": True}])
            self.assertEqual(webjam["open_prs"], 0, "Drafts must not inflate ready PRs")
            for p in status["projects"]:
                if p.get("private"):
                    self.assertNotIn("draft_prs", p)
                    self.assertNotIn("tip_sha", p)
            paired = json.loads((generator / "llm-work-now.json").read_text())
            self.assertEqual(status["llm_work_now_freshness"]["generated_at"], paired["generated_at"])

    def test_renderer_failure_keeps_all_published_artifacts_byte_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            generator = scratch / "generator"
            prepare_generator(ROOT, generator)
            env = fixture_environment(scratch)
            node = scratch / "fixture-bin" / "node"
            node.write_text("#!/bin/sh\nexit 9\n")
            node.chmod(0o755)
            before = {name: (generator / name).read_bytes() for name in
                      ("index.html", "status.json", "llm-work-now.json")}
            result = subprocess.run(["bash", str(generator / "refresh.sh")], cwd=generator,
                                    env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            for name, original in before.items():
                self.assertEqual((generator / name).read_bytes(), original, name)


if __name__ == "__main__":
    unittest.main()
