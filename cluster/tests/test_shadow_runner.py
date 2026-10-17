"""Tests for shadow runner."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cluster.control_plane.shadow_runner import (
    ShadowDecision,
    ShadowRunner,
    _ci_status_from_pr,
    _now_iso,
    _reviews_from_pr,
)


class TestShadowDecision(unittest.TestCase):
    """Tests for ShadowDecision dataclass."""

    def test_to_dict_excludes_none(self):
        """to_dict excludes None values."""
        decision = ShadowDecision(
            timestamp="2026-10-17T12:00:00Z",
            decision_type="dispatch",
            action="would_dispatch",
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
        decision = ShadowDecision(
            timestamp="2026-10-17T12:00:00Z",
            decision_type="gate",
            action="would_fail",
            reason="test",
            machine="nucbox",
            extra={"ci_status": {"build": "failure"}},
        )
        d = decision.to_dict()
        self.assertIn("extra", d)
        self.assertEqual(d["extra"]["ci_status"]["build"], "failure")


class TestCIStatusParsing(unittest.TestCase):
    """Tests for CI status parsing."""

    def test_success_status(self):
        """Success conclusion maps to success."""
        pr = {"statusCheckRollup": [{"name": "build", "conclusion": "SUCCESS"}]}
        status = _ci_status_from_pr(pr)
        self.assertEqual(status["build"], "success")

    def test_failure_status(self):
        """Failure conclusion maps to failure."""
        pr = {"statusCheckRollup": [{"name": "test", "conclusion": "FAILURE"}]}
        status = _ci_status_from_pr(pr)
        self.assertEqual(status["test"], "failure")

    def test_pending_status(self):
        """Pending state maps to pending."""
        pr = {"statusCheckRollup": [{"name": "lint", "state": "PENDING"}]}
        status = _ci_status_from_pr(pr)
        self.assertEqual(status["lint"], "pending")

    def test_skipped_status(self):
        """Skipped conclusion maps to success."""
        pr = {"statusCheckRollup": [{"name": "optional", "conclusion": "SKIPPED"}]}
        status = _ci_status_from_pr(pr)
        self.assertEqual(status["optional"], "success")

    def test_empty_rollup(self):
        """Empty rollup returns empty dict."""
        pr = {"statusCheckRollup": []}
        status = _ci_status_from_pr(pr)
        self.assertEqual(status, {})


class TestReviewsParsing(unittest.TestCase):
    """Tests for reviews parsing."""

    def test_parses_approved(self):
        """Parses APPROVED review state."""
        pr = {"reviews": [{"state": "APPROVED", "author": {"login": "user"}}]}
        reviews = _reviews_from_pr(pr)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["state"], "APPROVED")

    def test_empty_reviews(self):
        """Empty reviews returns empty list."""
        pr = {"reviews": []}
        reviews = _reviews_from_pr(pr)
        self.assertEqual(reviews, [])


class TestShadowRunner(unittest.TestCase):
    """Tests for ShadowRunner."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.runner = ShadowRunner(
            machine="mini",
            log_dir=Path(self.tmpdir),
        )

    def test_writes_decisions_to_jsonl(self):
        """Decisions are written to JSONL file."""
        decision = ShadowDecision(
            timestamp=_now_iso(),
            decision_type="dispatch",
            action="would_skip",
            reason="test",
            machine="mini",
        )
        self.runner._write_decision(decision)

        log_file = self.runner._log_file()
        self.assertTrue(log_file.exists())

        with open(log_file) as f:
            line = f.readline()
            parsed = json.loads(line)
            self.assertEqual(parsed["action"], "would_skip")

    def test_check_machine_guards_mini(self):
        """Machine guards check returns correct values for mini."""
        with patch("cluster.control_plane.shadow_runner.MachineGuards") as MockGuards:
            mock_instance = MagicMock()
            mock_instance.check_all.return_value = MagicMock(passed=True, reason="ok")
            mock_instance.check_load.return_value = MagicMock(value=5.0)
            mock_instance.check_swap.return_value = MagicMock(value=30.0)
            mock_instance.check_disk.return_value = MagicMock(value=100.0)
            mock_instance.check_lanes.return_value = MagicMock(value=None)
            MockGuards.return_value = mock_instance

            passed, reason, values = self.runner.check_machine_guards()
            self.assertTrue(passed)
            self.assertEqual(reason, "ok")
            self.assertEqual(values["load"], 5.0)

    def test_priority_score_draft_penalty(self):
        """Draft PRs get lower priority score."""
        pr_draft = {"isDraft": True, "reviewDecision": None}
        pr_ready = {"isDraft": False, "reviewDecision": None}

        score_draft = self.runner._priority_score(pr_draft)
        score_ready = self.runner._priority_score(pr_ready)

        self.assertLess(score_draft, score_ready)

    def test_priority_score_approved_boost(self):
        """Approved PRs get higher priority score."""
        pr_approved = {"isDraft": False, "reviewDecision": "APPROVED"}
        pr_none = {"isDraft": False, "reviewDecision": None}

        score_approved = self.runner._priority_score(pr_approved)
        score_none = self.runner._priority_score(pr_none)

        self.assertGreater(score_approved, score_none)

    def test_priority_score_changes_requested(self):
        """Changes requested PRs get highest priority (need fix)."""
        pr_changes = {"isDraft": False, "reviewDecision": "CHANGES_REQUESTED"}
        pr_approved = {"isDraft": False, "reviewDecision": "APPROVED"}

        score_changes = self.runner._priority_score(pr_changes)
        score_approved = self.runner._priority_score(pr_approved)

        self.assertGreater(score_changes, score_approved)

    @patch("cluster.control_plane.shadow_runner._get_open_prs")
    @patch("cluster.control_plane.shadow_runner.MachineGuards")
    def test_evaluate_dispatch_guards_fail(self, MockGuards, mock_get_prs):
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

        decisions = self.runner.evaluate_dispatch()

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].action, "would_skip")
        self.assertIn("guards failed", decisions[0].reason.lower())

    @patch("cluster.control_plane.shadow_runner._get_pr_details")
    def test_evaluate_gate_draft_fails(self, mock_get_details):
        """Gate fails for draft PRs."""
        mock_get_details.return_value = {
            "number": 123,
            "title": "Test PR",
            "author": {"login": "user"},
            "headRefOid": "abc123",
            "isDraft": True,
            "url": "https://github.com/test/repo/pull/123",
            "statusCheckRollup": [],
            "reviews": [],
        }

        decision = self.runner.evaluate_gate("test/repo", 123)

        self.assertEqual(decision.action, "would_fail")
        self.assertIn("draft", decision.reason.lower())

    @patch("cluster.control_plane.shadow_runner._get_pr_details")
    def test_evaluate_gate_ci_fails(self, mock_get_details):
        """Gate fails when CI fails."""
        mock_get_details.return_value = {
            "number": 123,
            "title": "Test PR",
            "author": {"login": "user"},
            "headRefOid": "abc123",
            "isDraft": False,
            "url": "https://github.com/test/repo/pull/123",
            "statusCheckRollup": [{"name": "build", "conclusion": "FAILURE"}],
            "reviews": [{"state": "APPROVED", "author": {"login": "reviewer"}}],
        }

        decision = self.runner.evaluate_gate("test/repo", 123)

        self.assertEqual(decision.action, "would_fail")
        self.assertIn("ci", decision.reason.lower())

    @patch("cluster.control_plane.shadow_runner._get_pr_details")
    def test_evaluate_gate_needs_llm(self, mock_get_details):
        """Gate needs LLM when deterministic checks pass."""
        mock_get_details.return_value = {
            "number": 123,
            "title": "Test PR",
            "author": {"login": "user"},
            "headRefOid": "abc123",
            "isDraft": False,
            "url": "https://github.com/test/repo/pull/123",
            "statusCheckRollup": [{"name": "build", "conclusion": "SUCCESS"}],
            "reviews": [{"state": "APPROVED", "author": {"login": "reviewer"}}],
        }

        decision = self.runner.evaluate_gate("test/repo", 123)

        self.assertEqual(decision.action, "would_need_llm")
        self.assertIn("claude", decision.reason.lower())


if __name__ == "__main__":
    unittest.main()
