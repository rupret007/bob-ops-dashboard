"""Tests for configuration."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cluster.control_plane.local_karen.config import (
    DEFAULT_REVIEWER_ORDER,
    KarenConfig,
    NEVER_TOUCH_REPOS,
)


class TestKarenConfig(unittest.TestCase):
    """Tests for KarenConfig."""

    def test_default_values(self):
        config = KarenConfig()

        self.assertEqual(config.reviewer_order, list(DEFAULT_REVIEWER_ORDER))
        self.assertEqual(config.codex_host, "nuc")
        self.assertEqual(config.codex_effort_default, "low")
        self.assertEqual(config.codex_effort_high_risk, "high")
        self.assertEqual(config.codex_weekly_cap_pct, 75.0)
        self.assertFalse(config.claude_enabled)
        self.assertEqual(config.claude_daily_cap, 3)
        self.assertEqual(config.budget_per_run_usd, 0.50)
        self.assertEqual(config.poll_interval_minutes, 10)
        self.assertEqual(config.poll_label, "needs-karen")

    def test_never_touch_repos(self):
        config = KarenConfig()
        self.assertIn("rupret007/rad-dad-show-night", config.never_touch_repos)

    def test_is_blocked_repo(self):
        config = KarenConfig()

        self.assertTrue(config.is_blocked_repo("rupret007", "rad-dad-show-night"))
        self.assertFalse(config.is_blocked_repo("rupret007", "webjam"))

    def test_custom_reviewer_order(self):
        config = KarenConfig(reviewer_order=["gemini", "minimax", "codex"])
        self.assertEqual(config.reviewer_order, ["gemini", "minimax", "codex"])

    def test_load_from_file(self):
        tmpdir = tempfile.mkdtemp()
        config_path = Path(tmpdir) / "config.json"

        config_data = {
            "reviewer_order": ["gemini", "codex"],
            "codex_host": "local",
            "claude_enabled": True,
            "claude_daily_cap": 5,
            "never_touch_repos": ["test/blocked"],
        }
        config_path.write_text(json.dumps(config_data))

        config = KarenConfig.load(config_path)

        self.assertEqual(config.reviewer_order, ["gemini", "codex"])
        self.assertEqual(config.codex_host, "local")
        self.assertTrue(config.claude_enabled)
        self.assertEqual(config.claude_daily_cap, 5)
        self.assertIn("test/blocked", config.never_touch_repos)

    def test_load_nonexistent_returns_defaults(self):
        config = KarenConfig.load(Path("/nonexistent/config.json"))

        self.assertEqual(config.reviewer_order, list(DEFAULT_REVIEWER_ORDER))

    def test_save_and_load(self):
        tmpdir = tempfile.mkdtemp()
        config_path = Path(tmpdir) / "saved_config.json"

        original = KarenConfig(
            reviewer_order=["minimax", "codex"],
            codex_host="custom",
            claude_enabled=True,
        )
        original.save(config_path)

        loaded = KarenConfig.load(config_path)

        self.assertEqual(loaded.reviewer_order, ["minimax", "codex"])
        self.assertEqual(loaded.codex_host, "custom")
        self.assertTrue(loaded.claude_enabled)

    def test_get_verdict_dir(self):
        config = KarenConfig(verdict_dir="~/custom/verdicts")
        verdict_dir = config.get_verdict_dir()

        self.assertIsInstance(verdict_dir, Path)
        self.assertNotIn("~", str(verdict_dir))

    def test_get_state_dir(self):
        config = KarenConfig(state_dir="~/custom/state")
        state_dir = config.get_state_dir()

        self.assertIsInstance(state_dir, Path)
        self.assertNotIn("~", str(state_dir))


class TestConfigEnvironment(unittest.TestCase):
    """Tests for config environment handling."""

    def test_codex_host_from_config(self):
        config = KarenConfig(codex_host="mini")
        self.assertEqual(config.codex_host, "mini")

    def test_effort_levels(self):
        config = KarenConfig(
            codex_effort_default="medium",
            codex_effort_high_risk="max",
        )
        self.assertEqual(config.codex_effort_default, "medium")
        self.assertEqual(config.codex_effort_high_risk, "max")


if __name__ == "__main__":
    unittest.main()
