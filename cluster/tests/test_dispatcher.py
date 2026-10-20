"""Tests for the StoryPool lane dispatcher."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cluster.control_plane.dispatcher import (
    PROVIDER_RULES,
    Dispatcher,
    DispatchDecision,
    _now_iso,
)


class TestDispatchDecision(unittest.TestCase):
    """Tests for DispatchDecision dataclass."""

    def test_to_dict_excludes_none(self):
        """to_dict excludes None values."""
        decision = DispatchDecision(
            timestamp="2026-10-17T12:00:00Z",
            action="dispatch",
            reason="test",
            machine="mini",
            pr_number=123,
            pr_url=None,
        )
        d = decision.to_dict()
        self.assertIn("pr_number", d)
        self.assertNotIn("pr_url", d)

    def test_to_dict_includes_extra(self):
        """to_dict includes extra fields."""
        decision = DispatchDecision(
            timestamp="2026-10-17T12:00:00Z",
            action="skip",
            reason="test",
            machine="nucbox",
            extra={"candidate_count": 5},
        )
        d = decision.to_dict()
        self.assertIn("extra", d)
        self.assertEqual(d["extra"]["candidate_count"], 5)


class TestProviderRules(unittest.TestCase):
    """Tests for provider configuration."""

    def test_grok_never_allowed(self):
        """Grok provider has empty machines list and zero concurrency."""
        rules = PROVIDER_RULES["grok"]
        self.assertEqual(rules["machines"], [])
        self.assertEqual(rules["max_concurrent"], 0)

    def test_codex_nucbox_only(self):
        """Codex is only allowed on NucBox."""
        rules = PROVIDER_RULES["codex"]
        self.assertEqual(rules["machines"], ["nucbox"])
        self.assertEqual(rules["weekly_cap_pct"], 75.0)

    def test_cursor_requires_nucbox_qualification(self):
        """Only the native Nuc Cursor worker is qualified."""
        rules = PROVIDER_RULES["cursor"]
        self.assertEqual(["nucbox"], rules["machines"])
        self.assertIsNone(rules["max_concurrent"])

    def test_claude_one_lane_max(self):
        """Claude has max 1 concurrent lane."""
        rules = PROVIDER_RULES["claude"]
        self.assertEqual(rules["max_concurrent"], 1)

    def test_minimax_two_lanes_max(self):
        """MiniMax has max 2 concurrent lanes."""
        rules = PROVIDER_RULES["minimax"]
        self.assertEqual(rules["max_concurrent"], 2)


class TestDispatcher(unittest.TestCase):
    """Tests for Dispatcher class."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.dispatcher = Dispatcher(
            machine="mini",
            log_dir=Path(self.tmpdir),
        )

    def test_writes_decisions_to_jsonl(self):
        """Decisions are written to JSONL file."""
        decision = DispatchDecision(
            timestamp=_now_iso(),
            action="would_dispatch",
            reason="test",
            machine="mini",
            live=False,
        )
        self.dispatcher._write_decision(decision)

        log_file = self.dispatcher._log_file()
        self.assertTrue(log_file.exists())

        with open(log_file) as f:
            line = f.readline()
            parsed = json.loads(line)
            self.assertEqual(parsed["action"], "would_dispatch")

    def test_writes_status_file(self):
        """Status file is written with summary."""
        summary = {"dispatched": 2, "skipped": 1}
        self.dispatcher._write_status(summary)

        status_file = self.dispatcher._status_file()
        self.assertTrue(status_file.exists())

        with open(status_file) as f:
            status = json.load(f)
            self.assertEqual(status["machine"], "mini")
            self.assertEqual(status["summary"]["dispatched"], 2)

    @patch("cluster.control_plane.dispatcher.MachineGuards")
    def test_check_machine_guards_mini(self, MockGuards):
        """Machine guards check returns correct values for mini."""
        mock_instance = MagicMock()
        mock_instance.check_all.return_value = MagicMock(passed=True, reason="ok")
        mock_instance.check_load.return_value = MagicMock(value=5.0)
        mock_instance.check_swap.return_value = MagicMock(value=30.0)
        mock_instance.check_disk.return_value = MagicMock(value=100.0)
        mock_instance.check_lanes.return_value = MagicMock(value=None)
        MockGuards.return_value = mock_instance

        passed, reason, values = self.dispatcher.check_machine_guards()
        
        self.assertTrue(passed)
        self.assertEqual(reason, "ok")
        self.assertEqual(values["load"], 5.0)
        self.assertEqual(values["swap"], 30.0)
        self.assertEqual(values["disk"], 100.0)

    def test_check_provider_grok_never_allowed(self):
        """Grok provider is never allowed."""
        allowed, reason = self.dispatcher.check_provider_allowed("grok", {})
        self.assertFalse(allowed)
        self.assertIn("never used", reason.lower())

    def test_check_provider_codex_mini_not_allowed(self):
        """Codex is not allowed on mini."""
        allowed, reason = self.dispatcher.check_provider_allowed("codex", {})
        self.assertFalse(allowed)
        self.assertIn("not allowed on mini", reason.lower())

    def test_check_provider_codex_nucbox_allowed(self):
        """Codex is allowed on nucbox when under budget."""
        nucbox_dispatcher = Dispatcher(
            machine="nucbox",
            log_dir=Path(self.tmpdir),
        )
        # Mock budget tracker to return allowed (both weekly_pct and can_use)
        with patch.object(nucbox_dispatcher.budget, "weekly_pct", return_value=0.0):
            with patch.object(nucbox_dispatcher.budget, "can_use", return_value=(True, "ok")):
                allowed, reason = nucbox_dispatcher.check_provider_allowed("codex", {})
                self.assertTrue(allowed)

    def test_check_provider_claude_max_concurrent(self):
        """Claude respects max concurrent limit."""
        active_lanes = {"claude": 1}
        allowed, reason = self.dispatcher.check_provider_allowed("claude", active_lanes)
        self.assertFalse(allowed)
        self.assertIn("not implemented", reason.lower())

    def test_priority_score_draft_penalty(self):
        """Draft PRs get lower priority score."""
        pr_draft = {"isDraft": True, "reviewDecision": None}
        pr_ready = {"isDraft": False, "reviewDecision": None}

        score_draft = self.dispatcher._priority_score(pr_draft)
        score_ready = self.dispatcher._priority_score(pr_ready)

        self.assertLess(score_draft, score_ready)

    def test_priority_score_approved_boost(self):
        """Approved PRs get higher priority score."""
        pr_approved = {"isDraft": False, "reviewDecision": "APPROVED"}
        pr_none = {"isDraft": False, "reviewDecision": None}

        score_approved = self.dispatcher._priority_score(pr_approved)
        score_none = self.dispatcher._priority_score(pr_none)

        self.assertGreater(score_approved, score_none)

    def test_priority_score_ci_failing_boost(self):
        """CI-failing PRs get higher priority (need fix)."""
        pr_failing = {
            "isDraft": False,
            "reviewDecision": None,
            "statusCheckRollup": [{"conclusion": "FAILURE"}],
        }
        pr_passing = {
            "isDraft": False,
            "reviewDecision": None,
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
        }

        score_failing = self.dispatcher._priority_score(pr_failing)
        score_passing = self.dispatcher._priority_score(pr_passing)

        self.assertGreater(score_failing, score_passing)

    def test_priority_score_needs_karen_label(self):
        """PRs with needs-karen label get priority boost."""
        pr_needs_karen = {
            "isDraft": False,
            "reviewDecision": None,
            "labels": [{"name": "needs-karen"}],
        }
        pr_no_label = {
            "isDraft": False,
            "reviewDecision": None,
            "labels": [],
        }

        score_karen = self.dispatcher._priority_score(pr_needs_karen)
        score_no_label = self.dispatcher._priority_score(pr_no_label)

        self.assertGreater(score_karen, score_no_label)

    def test_select_provider_never_selects_unimplemented_worker(self):
        """Mini waits; an unauthenticated Cursor cannot replace Codex."""
        pr = {"isDraft": False}
        provider = self.dispatcher.select_provider(pr, {})
        self.assertIsNone(provider)
        self.dispatcher.machine = "nucbox"
        with patch.object(self.dispatcher.budget, "weekly_pct", return_value=0), patch.object(self.dispatcher.budget, "can_use", return_value=(True, "ok")), patch('cluster.control_plane.cursor_worker.available', return_value=False):
            self.assertEqual(self.dispatcher.select_provider(pr, {}), "codex")
            self.assertIsNone(self.dispatcher.select_provider(pr, {"codex": 2}))
        for provider in ("cursor", "claude", "gemini", "minimax"):
            self.assertFalse(self.dispatcher.check_provider_allowed(provider, {})[0])

    def test_select_provider_falls_back_when_cursor_exhausted(self):
        """Provider selection returns None when no providers available."""
        # Mock all providers as over budget or at max concurrent
        def mock_check_provider(provider, active_lanes):
            return False, f"{provider} at max"
        
        with patch.object(self.dispatcher, "check_provider_allowed", side_effect=mock_check_provider):
            pr = {"isDraft": False}
            provider = self.dispatcher.select_provider(pr, {})
            self.assertIsNone(provider)

    @patch("cluster.control_plane.dispatcher._get_open_prs")
    @patch("cluster.control_plane.dispatcher.MachineGuards")
    def test_evaluate_guards_fail(self, MockGuards, mock_get_prs):
        """Dispatch skips when guards fail."""
        mock_instance = MagicMock()
        mock_instance.check_all.return_value = MagicMock(
            passed=False, reason="load 15.0 >= 10"
        )
        mock_instance.check_load.return_value = MagicMock(value=15.0)
        mock_instance.check_swap.return_value = MagicMock(value=0.0)
        mock_instance.check_disk.return_value = MagicMock(value=100.0)
        mock_instance.check_lanes.return_value = MagicMock(value=None)
        MockGuards.return_value = mock_instance

        decisions = self.dispatcher.evaluate(live=False)

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].action, "skip")
        self.assertIn("guards failed", decisions[0].reason.lower())

    @patch("cluster.control_plane.dispatcher._get_open_prs")
    @patch("cluster.control_plane.dispatcher.MachineGuards")
    def test_evaluate_no_prs_skip(self, MockGuards, mock_get_prs):
        """Dispatch skips when no open PRs found."""
        mock_instance = MagicMock()
        mock_instance.check_all.return_value = MagicMock(passed=True, reason="ok")
        mock_instance.check_load.return_value = MagicMock(value=5.0)
        mock_instance.check_swap.return_value = MagicMock(value=30.0)
        mock_instance.check_disk.return_value = MagicMock(value=100.0)
        mock_instance.check_lanes.return_value = MagicMock(value=None)
        MockGuards.return_value = mock_instance
        mock_get_prs.return_value = []

        decisions = self.dispatcher.evaluate(live=False)

        skip_decisions = [d for d in decisions if d.action == "skip"]
        self.assertTrue(any("no open" in d.reason.lower() for d in skip_decisions))

    @patch("cluster.control_plane.dispatcher._get_open_prs")
    @patch("cluster.control_plane.dispatcher.MachineGuards")
    def test_evaluate_shadow_mode_prefix(self, MockGuards, mock_get_prs):
        """Shadow mode uses would_dispatch prefix."""
        mock_instance = MagicMock()
        mock_instance.check_all.return_value = MagicMock(passed=True, reason="ok")
        mock_instance.check_load.return_value = MagicMock(value=5.0)
        mock_instance.check_swap.return_value = MagicMock(value=30.0)
        mock_instance.check_disk.return_value = MagicMock(value=100.0)
        mock_instance.check_lanes.return_value = MagicMock(value=None)
        MockGuards.return_value = mock_instance
        mock_get_prs.return_value = [
            {
                "number": 123,
                "isDraft": False,
                "headRefOid": "abc123",
                "url": "https://github.com/test/repo/pull/123",
            }
        ]

        decisions = self.dispatcher.evaluate(live=False)

        dispatch_decisions = [d for d in decisions if "dispatch" in d.action]
        for d in dispatch_decisions:
            self.assertEqual(d.action, "would_dispatch")
            self.assertFalse(d.live)


class TestNucBoxDispatcher(unittest.TestCase):
    """Tests for NucBox-specific dispatcher behavior."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.dispatcher = Dispatcher(
            machine="nucbox",
            log_dir=Path(self.tmpdir),
        )

    def test_nucbox_lane_capacity_no_codex_work(self):
        """NucBox has full 7 lanes when no Codex work queued."""
        with patch("cluster.control_plane.dispatcher._has_codex_work_queued", return_value=False):
            capacity = self.dispatcher.get_nucbox_lane_capacity({})
            self.assertEqual(capacity, 7)

    def test_nucbox_lane_capacity_with_codex_work(self):
        """NucBox reserves 2 lanes for Codex when Codex work queued."""
        with patch("cluster.control_plane.dispatcher._has_codex_work_queued", return_value=True):
            capacity = self.dispatcher.get_nucbox_lane_capacity({})
            self.assertEqual(capacity, 5)

    def test_nucbox_lane_capacity_codex_using_some(self):
        """Reserved lanes decrease when Codex is already using some."""
        with patch("cluster.control_plane.dispatcher._has_codex_work_queued", return_value=True):
            capacity = self.dispatcher.get_nucbox_lane_capacity({"codex": 1})
            self.assertEqual(capacity, 5)

    def test_codex_allowed_on_nucbox(self):
        """Codex is allowed on NucBox when under budget."""
        # Mock budget tracker to return allowed (both weekly_pct and can_use)
        with patch.object(self.dispatcher.budget, "weekly_pct", return_value=0.0):
            with patch.object(self.dispatcher.budget, "can_use", return_value=(True, "ok")):
                allowed, reason = self.dispatcher.check_provider_allowed("codex", {})
                self.assertTrue(allowed)
                self.assertEqual(reason, "ok")


if __name__ == "__main__":
    unittest.main()
