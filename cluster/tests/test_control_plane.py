"""Tests for the StoryPool control plane modules."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from cluster.control_plane.budget_tracker import (
    BudgetCaps,
    BudgetTracker,
    ProviderUsage,
)
from cluster.control_plane.gate_runner import (
    ApprovalChecker,
    CIStatusChecker,
    ClaudeGateChecker,
    DraftChecker,
    GateResult,
    GateRunner,
    GateVerdict,
    MiniMaxSecondJudge,
    PRContext,
    validate_merge_gate,
)
from cluster.control_plane.guards import (
    MINI_DISK_MIN_GB,
    MINI_LOAD_LIMIT,
    MINI_SWAP_LIMIT_PCT,
    NUCBOX_LOAD_LIMIT,
    NUCBOX_MAX_LANES,
    MachineGuards,
    mini_guards_pass,
    nucbox_guards_pass,
)
from cluster.control_plane.llm_router import (
    LLMResponse,
    LLMRole,
    LLMRouter,
)


class TestBudgetTracker(unittest.TestCase):
    """Tests for BudgetTracker."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.tracker = BudgetTracker(data_dir=Path(self.tmpdir))

    def test_record_usage(self):
        """Recording usage updates the provider's stats."""
        self.tracker.record("claude", tokens_in=100, tokens_out=50, cost_usd=0.01)
        usage = self.tracker.get_usage("claude")
        self.assertEqual(usage.tokens_in, 100)
        self.assertEqual(usage.tokens_out, 50)
        self.assertEqual(usage.cost_usd, 0.01)
        self.assertEqual(usage.calls, 1)

    def test_record_accumulates(self):
        """Multiple records accumulate usage."""
        self.tracker.record("gemini", cost_usd=1.0)
        self.tracker.record("gemini", cost_usd=2.0)
        usage = self.tracker.get_usage("gemini")
        self.assertEqual(usage.cost_usd, 3.0)
        self.assertEqual(usage.calls, 2)

    def test_can_use_within_budget(self):
        """Provider within budget returns allowed."""
        allowed, reason = self.tracker.can_use("claude")
        self.assertTrue(allowed)
        self.assertEqual(reason, "ok")

    def test_can_use_over_budget(self):
        """Provider over budget returns not allowed with reason."""
        self.tracker.record("grok", cost_usd=2.0)
        allowed, reason = self.tracker.can_use("grok")
        self.assertFalse(allowed)
        self.assertIn("exceeded", reason.lower())

    def test_weekly_pct(self):
        """Weekly percentage calculation."""
        self.tracker.record("codex", cost_usd=25.0)
        pct = self.tracker.weekly_pct("codex")
        self.assertEqual(pct, 50.0)

    def test_unknown_provider_raises(self):
        """Unknown provider raises ValueError."""
        with self.assertRaises(ValueError):
            self.tracker.record("unknown_provider", cost_usd=1.0)

    def test_summary(self):
        """Summary returns all providers."""
        self.tracker.record("claude", cost_usd=5.0)
        summary = self.tracker.summary()
        self.assertIn("claude", summary)
        self.assertIn("grok", summary)
        self.assertEqual(summary["claude"]["cost_usd"], 5.0)

    def test_persistence(self):
        """Usage persists across tracker instances."""
        self.tracker.record("minimax", cost_usd=3.0)
        new_tracker = BudgetTracker(data_dir=Path(self.tmpdir))
        usage = new_tracker.get_usage("minimax")
        self.assertEqual(usage.cost_usd, 3.0)


class TestMachineGuards(unittest.TestCase):
    """Tests for machine guards."""

    def test_mini_load_pass(self):
        """Mini passes when load is below limit."""
        passed, reason = mini_guards_pass(
            load_fn=lambda: 5.0,
            swap_fn=lambda: 30.0,
            disk_fn=lambda: 50.0,
        )
        self.assertTrue(passed)
        self.assertEqual(reason, "all guards passed")

    def test_mini_load_fail(self):
        """Mini fails when load exceeds limit."""
        passed, reason = mini_guards_pass(
            load_fn=lambda: MINI_LOAD_LIMIT + 1,
            swap_fn=lambda: 0.0,
            disk_fn=lambda: 100.0,
        )
        self.assertFalse(passed)
        self.assertIn("load", reason)

    def test_mini_swap_fail(self):
        """Mini fails when swap exceeds limit."""
        passed, reason = mini_guards_pass(
            load_fn=lambda: 1.0,
            swap_fn=lambda: MINI_SWAP_LIMIT_PCT + 1,
            disk_fn=lambda: 100.0,
        )
        self.assertFalse(passed)
        self.assertIn("swap", reason)

    def test_mini_disk_fail(self):
        """Mini fails when disk is below minimum."""
        passed, reason = mini_guards_pass(
            load_fn=lambda: 1.0,
            swap_fn=lambda: 0.0,
            disk_fn=lambda: MINI_DISK_MIN_GB - 1,
        )
        self.assertFalse(passed)
        self.assertIn("disk", reason)

    def test_nucbox_load_pass(self):
        """NucBox passes when load is below limit."""
        passed, reason = nucbox_guards_pass(
            load_fn=lambda: 8.0,
            lane_count_fn=lambda: 3,
        )
        self.assertTrue(passed)

    def test_nucbox_load_fail(self):
        """NucBox fails when load exceeds limit."""
        passed, reason = nucbox_guards_pass(
            load_fn=lambda: NUCBOX_LOAD_LIMIT + 1,
            lane_count_fn=lambda: 0,
        )
        self.assertFalse(passed)
        self.assertIn("load", reason)

    def test_nucbox_lanes_fail(self):
        """NucBox fails when lanes exceed maximum."""
        passed, reason = nucbox_guards_pass(
            load_fn=lambda: 1.0,
            lane_count_fn=lambda: NUCBOX_MAX_LANES + 1,
        )
        self.assertFalse(passed)
        self.assertIn("lanes", reason)


class TestGateCheckers(unittest.TestCase):
    """Tests for gate checker classes."""

    def _make_context(self, **overrides) -> PRContext:
        defaults = {
            "owner": "rupret007",
            "repo": "webjam",
            "number": 123,
            "title": "Test PR",
            "author": "testuser",
            "head_sha": "abc123def456",
            "base_branch": "main",
            "is_draft": False,
            "diff": "diff --git a/file.py b/file.py\n+print('hello')",
            "test_files": ["tests/test_file.py"],
            "ci_status": {"build": "success", "test": "success"},
            "reviews": [{"state": "APPROVED", "user": {"login": "reviewer"},
                         "commit": {"oid": "abc123def456"}}],
        }
        defaults.update(overrides)
        return PRContext(**defaults)

    def test_draft_checker_passes_non_draft(self):
        """DraftChecker passes when PR is not a draft."""
        context = self._make_context(is_draft=False)
        result = DraftChecker().check(context)
        self.assertIsNone(result)

    def test_draft_checker_fails_draft(self):
        """DraftChecker fails when PR is a draft."""
        context = self._make_context(is_draft=True)
        result = DraftChecker().check(context)
        self.assertIsNotNone(result)
        self.assertEqual(result.verdict, GateVerdict.FAIL)
        self.assertIn("draft", result.reason.lower())

    def test_ci_checker_passes_all_success(self):
        """CIStatusChecker passes when all CI checks succeed."""
        context = self._make_context(
            ci_status={"build": "success", "test": "success", "lint": "skipped"}
        )
        result = CIStatusChecker().check(context)
        self.assertIsNone(result)

    def test_ci_checker_fails_pending(self):
        """CIStatusChecker fails when a CI check is pending."""
        context = self._make_context(ci_status={"build": "pending"})
        result = CIStatusChecker().check(context)
        self.assertIsNotNone(result)
        self.assertEqual(result.verdict, GateVerdict.FAIL)
        self.assertIn("incomplete", result.reason.lower())

    def test_ci_checker_fails_failure(self):
        """CIStatusChecker fails when a CI check fails."""
        context = self._make_context(ci_status={"build": "failure"})
        result = CIStatusChecker().check(context)
        self.assertIsNotNone(result)
        self.assertEqual(result.verdict, GateVerdict.FAIL)
        self.assertIn("failed", result.reason.lower())

    def test_approval_checker_passes_approved(self):
        """ApprovalChecker passes when PR has approval."""
        context = self._make_context(
            reviews=[{"state": "APPROVED", "user": {"login": "reviewer"},
                      "commit": {"oid": "abc123def456"}}]
        )
        result = ApprovalChecker().check(context)
        self.assertIsNone(result)

    def test_approval_checker_fails_no_approval(self):
        """ApprovalChecker fails when PR has no approval."""
        context = self._make_context(reviews=[])
        result = ApprovalChecker().check(context)
        self.assertIsNotNone(result)
        self.assertEqual(result.verdict, GateVerdict.FAIL)
        self.assertIn("approved", result.reason.lower())

    def test_approval_checker_fails_changes_requested(self):
        """ApprovalChecker fails when only changes are requested."""
        context = self._make_context(
            reviews=[{"state": "CHANGES_REQUESTED", "user": {"login": "reviewer"}}]
        )
        result = ApprovalChecker().check(context)
        self.assertIsNotNone(result)
        self.assertEqual(result.verdict, GateVerdict.FAIL)


class TestClaudeGateChecker(unittest.TestCase):
    """Tests for ClaudeGateChecker."""

    def _make_context(self) -> PRContext:
        return PRContext(
            owner="rupret007",
            repo="webjam",
            number=123,
            title="Test PR",
            author="testuser",
            head_sha="abc123def456",
            base_branch="main",
            is_draft=False,
            diff="diff --git a/file.py b/file.py\n+print('hello')",
            test_files=["tests/test_file.py"],
            ci_status={"build": "success"},
            reviews=[{"state": "APPROVED"}],
        )

    def test_parses_pass_verdict(self):
        """Parses PASS verdict from LLM response."""
        mock_llm = MagicMock()
        mock_llm.complete.return_value = "PASS: ready to merge"
        checker = ClaudeGateChecker(mock_llm)

        context = self._make_context()
        result = checker.check(context)

        self.assertEqual(result.verdict, GateVerdict.PASS)
        self.assertEqual(result.reason, "ready to merge")
        self.assertEqual(result.reviewer, "claude")

    def test_parses_fail_verdict(self):
        """Parses FAIL verdict from LLM response."""
        mock_llm = MagicMock()
        mock_llm.complete.return_value = "FAIL: missing tests for new endpoint"
        checker = ClaudeGateChecker(mock_llm)

        context = self._make_context()
        result = checker.check(context)

        self.assertEqual(result.verdict, GateVerdict.FAIL)
        self.assertIn("missing tests", result.reason.lower())

    def test_parses_needs_review_verdict(self):
        """Parses NEEDS_REVIEW verdict from LLM response."""
        mock_llm = MagicMock()
        mock_llm.complete.return_value = "NEEDS_REVIEW: borderline security concern"
        checker = ClaudeGateChecker(mock_llm)

        context = self._make_context()
        result = checker.check(context)

        self.assertEqual(result.verdict, GateVerdict.NEEDS_REVIEW)

    def test_handles_unparseable_response(self):
        """Returns NEEDS_REVIEW when response cannot be parsed."""
        mock_llm = MagicMock()
        mock_llm.complete.return_value = "This is a weird response"
        checker = ClaudeGateChecker(mock_llm)

        context = self._make_context()
        result = checker.check(context)

        self.assertEqual(result.verdict, GateVerdict.NEEDS_REVIEW)
        self.assertIn("could not parse", result.reason.lower())


class TestGateRunner(unittest.TestCase):
    """Tests for GateRunner orchestration."""

    def _make_context(self, **overrides) -> PRContext:
        defaults = {
            "owner": "rupret007",
            "repo": "webjam",
            "number": 123,
            "title": "Test PR",
            "author": "testuser",
            "head_sha": "abc123def456",
            "base_branch": "main",
            "is_draft": False,
            "diff": "diff content",
            "test_files": [],
            "ci_status": {"build": "success"},
            "reviews": [{"state": "APPROVED", "user": {"login": "reviewer"},
                         "commit": {"oid": "abc123def456"}}],
        }
        defaults.update(overrides)
        return PRContext(**defaults)

    def test_deterministic_checks_run_first(self):
        """Deterministic checks run before LLM."""
        mock_llm = MagicMock()
        runner = GateRunner(claude_client=mock_llm)

        context = self._make_context(is_draft=True)
        result = runner.run(context)

        self.assertEqual(result.verdict, GateVerdict.FAIL)
        self.assertIn("draft", result.reason.lower())
        mock_llm.complete.assert_not_called()

    def test_llm_called_when_deterministic_pass(self):
        """LLM is called when deterministic checks pass."""
        mock_llm = MagicMock()
        mock_llm.complete.return_value = "PASS: looks good"
        runner = GateRunner(claude_client=mock_llm)

        context = self._make_context()
        result = runner.run(context)

        mock_llm.complete.assert_called_once()
        self.assertEqual(result.verdict, GateVerdict.PASS)

    def test_no_llm_returns_needs_review(self):
        """Returns NEEDS_REVIEW when no LLM client is configured."""
        runner = GateRunner(claude_client=None)

        context = self._make_context()
        result = runner.run(context)

        self.assertEqual(result.verdict, GateVerdict.NEEDS_REVIEW)
        self.assertIn("no claude client", result.reason.lower())


class TestValidateMergeGate(unittest.TestCase):
    """Tests for validate_merge_gate function."""

    def test_passes_valid_pr(self):
        """Passes when all conditions are met."""
        ok, reason = validate_merge_gate(
            pr_number=123,
            gate_sha="abc123",
            current_sha="abc123",
            is_draft=False,
            ci_status={"build": "success"},
            reviews=[{"state": "APPROVED", "user": {"login": "reviewer"},
                      "commit": {"oid": "abc123"}}],
            gate_result=GateResult(GateVerdict.PASS, "Reviewed complete diff",
                                   "abc123", 123, "claude"),
            author="testuser",
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_fails_draft(self):
        """Fails when PR is a draft."""
        ok, reason = validate_merge_gate(
            pr_number=123,
            gate_sha="abc123",
            current_sha="abc123",
            is_draft=True,
            ci_status={},
            reviews=[],
        )
        self.assertFalse(ok)
        self.assertIn("draft", reason.lower())

    def test_fails_sha_mismatch(self):
        """Fails when head SHA changed after gate."""
        ok, reason = validate_merge_gate(
            pr_number=123,
            gate_sha="abc123",
            current_sha="def456",
            is_draft=False,
            ci_status={},
            reviews=[],
        )
        self.assertFalse(ok)
        self.assertIn("sha changed", reason.lower())

    def test_fails_ci_incomplete(self):
        """Fails when CI is incomplete."""
        ok, reason = validate_merge_gate(
            pr_number=123,
            gate_sha="abc123",
            current_sha="abc123",
            is_draft=False,
            ci_status={"build": "pending"},
            reviews=[{"state": "APPROVED"}],
        )
        self.assertFalse(ok)
        self.assertIn("incomplete", reason.lower())

    def test_fails_ci_failed(self):
        """Fails when CI fails."""
        ok, reason = validate_merge_gate(
            pr_number=123,
            gate_sha="abc123",
            current_sha="abc123",
            is_draft=False,
            ci_status={"build": "failure"},
            reviews=[{"state": "APPROVED"}],
        )
        self.assertFalse(ok)
        self.assertIn("failed", reason.lower())

    def test_fails_no_approval(self):
        """Fails when no approval exists."""
        ok, reason = validate_merge_gate(
            pr_number=123,
            gate_sha="abc123",
            current_sha="abc123",
            is_draft=False,
            ci_status={"build": "success"},
            reviews=[],
        )
        self.assertFalse(ok)
        self.assertIn("approved", reason.lower())


class TestLLMRouter(unittest.TestCase):
    """Tests for LLMRouter."""

    def test_routes_to_preferred_provider(self):
        """Routes to the first available provider for the role."""
        mock_gemini = MagicMock()
        mock_gemini.provider = "gemini"
        mock_gemini.complete_with_usage.return_value = LLMResponse(
            text="response",
            provider="gemini",
            model="gemini-2.0-flash",
            tokens_in=10,
            tokens_out=5,
            cost_usd=0.001,
        )

        tmpdir = tempfile.mkdtemp()
        tracker = BudgetTracker(data_dir=Path(tmpdir))
        router = LLMRouter(
            budget_tracker=tracker,
            gemini_client=mock_gemini,
        )

        client = router.get_client(LLMRole.CONDUCTOR)
        self.assertEqual(client.provider, "gemini")

    def test_falls_back_when_over_budget(self):
        """Falls back to next provider when preferred is over budget."""
        mock_gemini = MagicMock()
        mock_gemini.provider = "gemini"
        mock_claude = MagicMock()
        mock_claude.provider = "claude"

        tmpdir = tempfile.mkdtemp()
        tracker = BudgetTracker(data_dir=Path(tmpdir))
        tracker.record("gemini", cost_usd=200.0)

        router = LLMRouter(
            budget_tracker=tracker,
            gemini_client=mock_gemini,
            claude_client=mock_claude,
        )

        client = router.get_client(LLMRole.CONDUCTOR)
        self.assertEqual(client.provider, "claude")

    def test_records_usage_after_call(self):
        """Records usage after a successful completion."""
        mock_gemini = MagicMock()
        mock_gemini.provider = "gemini"
        mock_gemini.complete_with_usage.return_value = LLMResponse(
            text="response",
            provider="gemini",
            model="gemini-2.0-flash",
            tokens_in=100,
            tokens_out=50,
            cost_usd=0.01,
        )

        tmpdir = tempfile.mkdtemp()
        tracker = BudgetTracker(data_dir=Path(tmpdir))
        router = LLMRouter(
            budget_tracker=tracker,
            gemini_client=mock_gemini,
        )

        router.complete(LLMRole.CONDUCTOR, "test prompt")

        usage = tracker.get_usage("gemini")
        self.assertEqual(usage.tokens_in, 100)
        self.assertEqual(usage.cost_usd, 0.01)


if __name__ == "__main__":
    unittest.main()
