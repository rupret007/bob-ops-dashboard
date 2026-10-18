"""The root Pages build must exclude internal operational records and code."""

import fnmatch
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class PagesExclusionTests(unittest.TestCase):
    def setUp(self):
        # JSON is a YAML subset, so this config needs no test-only YAML parser.
        self.config = json.loads((ROOT / "_config.yml").read_text())

    def excluded(self, path):
        return any(path == rule or path.startswith(rule + "/") or fnmatch.fnmatchcase(path, rule)
                   for rule in self.config["exclude"])

    def test_internal_documents_and_runtime_sources_are_excluded(self):
        paths = [
            "docs/design/storypool-control-plane.md",
            "docs/incidents/2026-10-17-show-night-leak.md",
            "docs/live-metrics-research.md", "docs/live-metrics.md",
            "docs/research-notes/polling-and-review-patterns.md",
            "docs/scorecards.md", "docs/storypool-scorecard.md",
            "docs/storypool/REPLICATION.md", "docs/storypool/ROADMAP.md",
            "docs/storypool/SECURITY.md", "docs/storypool/scheduler-research.md",
            "docs/usage-machines-collector.md", "docs/validation/pr75-unknown-usage.md",
            "cluster/control_plane/local_karen/checks.py", "live/repos.json",
            "tests/fixtures/scorecards/evidence.json", "refresh.sh", "board_meta.py",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())
                self.assertTrue(self.excluded(path), path + " would be published")
        self.assertFalse(self.config.get("include"), "include rules must not override these exclusions")

    def test_public_board_and_handoffs_remain_available(self):
        for path in ["index.html", "status.json", "llm-work-now.json", "harden-window.json",
                     "docs/AGENT_HANDOFF_RUNBOOK.md"]:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())
                self.assertFalse(self.excluded(path), path + " must remain available")


if __name__ == "__main__":
    unittest.main()
