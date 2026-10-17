"""Tests for LLM reviewers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cluster.control_plane.local_karen.checks import PRInfo
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.reviewers import (
    BaseReviewer,
    ClaudeReviewer,
    CodexReviewer,
    GeminiReviewer,
    MiniMaxReviewer,
    ReviewResult,
    ReviewTier,
    ReviewVerdict,
    classify_risk_tier,
    get_available_reviewers,
    run_review_chain,
)


class TestRiskTierClassification(unittest.TestCase):
    """Tests for risk tier classification."""

    def _make_pr(self, **overrides) -> PRInfo:
        defaults = {
            "owner": "rupret007",
            "repo": "webjam",
            "number": 123,
            "head_sha": "abc123def456789",
            "title": "Test PR",
            "author": "testuser",
            "is_draft": False,
            "diff": "diff --git a/file.py b/file.py\n+print('hello')",
            "ci_status": {"build": "success"},
            "changed_files": ["file.py"],
            "deleted_files": [],
        }
        defaults.update(overrides)
        return PRInfo(**defaults)

    def test_low_tier_simple_change(self):
        pr = self._make_pr(
            title="Fix typo in readme",
            diff="+Fixed spelling error",
            changed_files=["README.md"],
        )
        tier = classify_risk_tier(pr)
        self.assertEqual(tier, ReviewTier.LOW)

    def test_medium_tier_api_change(self):
        pr = self._make_pr(
            title="Add new feature",
            diff="+def calculate(x):\n+    return x * 2",
            changed_files=["utils.py"],
        )
        tier = classify_risk_tier(pr)
        self.assertEqual(tier, ReviewTier.LOW)

    def test_high_tier_auth_change(self):
        pr = self._make_pr(
            title="Update authentication flow",
            diff="+def authenticate_user(password):\n+def verify_token(token):",
            changed_files=["auth/login.py", "auth/tokens.py"],
        )
        tier = classify_risk_tier(pr)
        self.assertEqual(tier, ReviewTier.HIGH)

    def test_security_tier_vulnerability_fix(self):
        pr = self._make_pr(
            title="Fix SQL injection vulnerability",
            diff="+sanitize_input(query)",
            changed_files=["db/queries.py"],
        )
        tier = classify_risk_tier(pr)
        self.assertEqual(tier, ReviewTier.SECURITY)

    def test_high_tier_large_change(self):
        pr = self._make_pr(
            title="Major refactor",
            diff="x" * 60000,
            changed_files=[f"file{i}.py" for i in range(25)],
        )
        tier = classify_risk_tier(pr)
        self.assertEqual(tier, ReviewTier.HIGH)


class TestReviewResultParsing(unittest.TestCase):
    """Tests for parsing LLM responses."""

    def test_parse_pass_verdict(self):
        class TestReviewer(BaseReviewer):
            name = "test"
            def is_available(self, config): return True, "ok"
            def review(self, pr, tier, config): pass

        reviewer = TestReviewer()
        pr = PRInfo(
            owner="test", repo="test", number=1, head_sha="abc",
            title="Test", author="user", is_draft=False, diff="",
            ci_status={}, changed_files=[], deleted_files=[],
        )

        result = reviewer._parse_verdict("PASS: Looks good to merge", pr, ReviewTier.LOW)
        self.assertEqual(result.verdict, ReviewVerdict.PASS)
        self.assertEqual(result.reason, "Looks good to merge")

    def test_parse_fail_verdict(self):
        class TestReviewer(BaseReviewer):
            name = "test"
            def is_available(self, config): return True, "ok"
            def review(self, pr, tier, config): pass

        reviewer = TestReviewer()
        pr = PRInfo(
            owner="test", repo="test", number=1, head_sha="abc",
            title="Test", author="user", is_draft=False, diff="",
            ci_status={}, changed_files=[], deleted_files=[],
        )

        result = reviewer._parse_verdict("FAIL: Missing tests for new endpoint", pr, ReviewTier.MEDIUM)
        self.assertEqual(result.verdict, ReviewVerdict.FAIL)
        self.assertIn("missing tests", result.reason.lower())

    def test_parse_borderline_verdict(self):
        class TestReviewer(BaseReviewer):
            name = "test"
            def is_available(self, config): return True, "ok"
            def review(self, pr, tier, config): pass

        reviewer = TestReviewer()
        pr = PRInfo(
            owner="test", repo="test", number=1, head_sha="abc",
            title="Test", author="user", is_draft=False, diff="",
            ci_status={}, changed_files=[], deleted_files=[],
        )

        result = reviewer._parse_verdict("BORDERLINE: Needs human review of auth changes", pr, ReviewTier.HIGH)
        self.assertEqual(result.verdict, ReviewVerdict.BORDERLINE)
        self.assertEqual(result.confidence, 0.5)

    def test_parse_unparseable_response(self):
        class TestReviewer(BaseReviewer):
            name = "test"
            def is_available(self, config): return True, "ok"
            def review(self, pr, tier, config): pass

        reviewer = TestReviewer()
        pr = PRInfo(
            owner="test", repo="test", number=1, head_sha="abc",
            title="Test", author="user", is_draft=False, diff="",
            ci_status={}, changed_files=[], deleted_files=[],
        )

        result = reviewer._parse_verdict("This is a weird response without a verdict", pr, ReviewTier.LOW)
        self.assertEqual(result.verdict, ReviewVerdict.BORDERLINE)
        self.assertIn("could not parse", result.reason.lower())
        self.assertEqual(result.confidence, 0.3)


class TestCodexReviewer(unittest.TestCase):
    """Tests for Codex reviewer."""

    def test_not_available_when_cli_missing(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            reviewer = CodexReviewer()
            config = KarenConfig()

            ok, reason = reviewer.is_available(config)
            self.assertFalse(ok)
            self.assertIn("not found", reason)

    def test_not_available_over_budget(self):
        tmpdir = tempfile.mkdtemp()
        config = KarenConfig(state_dir=tmpdir, codex_weekly_cap_pct=75.0)

        state_file = Path(tmpdir) / "codex_weekly_usage.json"
        state_file.write_text(json.dumps({
            "week_start": 9999999999.0,
            "usage_pct": 80.0,
        }))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            reviewer = CodexReviewer()

            ok, reason = reviewer.is_available(config)
            self.assertFalse(ok)
            self.assertIn("80.0%", reason)


class TestClaudeReviewer(unittest.TestCase):
    """Tests for Claude reviewer."""

    def test_not_available_when_disabled(self):
        config = KarenConfig(claude_enabled=False)
        reviewer = ClaudeReviewer()

        ok, reason = reviewer.is_available(config)
        self.assertFalse(ok)
        self.assertIn("disabled", reason)

    def test_not_available_over_daily_cap(self):
        tmpdir = tempfile.mkdtemp()
        config = KarenConfig(
            claude_enabled=True,
            claude_daily_cap=3,
            state_dir=tmpdir,
        )

        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state_file = Path(tmpdir) / "claude_daily_usage.json"
        state_file.write_text(json.dumps({"date": today, "count": 3}))

        reviewer = ClaudeReviewer()

        ok, reason = reviewer.is_available(config)
        self.assertFalse(ok)
        self.assertIn("3/3", reason)


class TestReviewerOrder(unittest.TestCase):
    """Tests for reviewer ordering."""

    def test_default_order(self):
        config = KarenConfig()
        self.assertEqual(config.reviewer_order, ["codex", "gemini", "minimax"])

    def test_custom_order(self):
        config = KarenConfig(reviewer_order=["gemini", "codex"])
        self.assertEqual(config.reviewer_order, ["gemini", "codex"])

    def test_get_available_filters_unavailable(self):
        config = KarenConfig(reviewer_order=["codex", "gemini", "minimax"])

        with patch.object(CodexReviewer, "is_available", return_value=(False, "not found")):
            with patch.object(GeminiReviewer, "is_available", return_value=(True, "ok")):
                with patch.object(MiniMaxReviewer, "is_available", return_value=(True, "ok")):
                    available = get_available_reviewers(config)

                    names = [name for name, _ in available]
                    self.assertNotIn("codex", names)
                    self.assertIn("gemini", names)


class TestReviewChain(unittest.TestCase):
    """Tests for review chain execution."""

    def _make_pr(self) -> PRInfo:
        return PRInfo(
            owner="rupret007",
            repo="webjam",
            number=123,
            head_sha="abc123def456789",
            title="Test PR",
            author="testuser",
            is_draft=False,
            diff="+print('hello')",
            ci_status={"build": "success"},
            changed_files=["file.py"],
            deleted_files=[],
        )

    def test_chain_stops_on_first_success(self):
        pr = self._make_pr()
        config = KarenConfig(reviewer_order=["codex", "gemini"])

        mock_codex_result = ReviewResult(
            reviewer="codex",
            verdict=ReviewVerdict.PASS,
            reason="Looks good",
            tier=ReviewTier.LOW,
        )

        with patch.object(CodexReviewer, "is_available", return_value=(True, "ok")):
            with patch.object(CodexReviewer, "review", return_value=mock_codex_result):
                with patch.object(GeminiReviewer, "is_available", return_value=(True, "ok")):
                    with patch.object(GeminiReviewer, "review") as mock_gemini:
                        result, all_results = run_review_chain(pr, config)

                        self.assertEqual(result.verdict, ReviewVerdict.PASS)
                        self.assertEqual(result.reviewer, "codex")
                        mock_gemini.assert_not_called()

    def test_chain_falls_back_on_error(self):
        pr = self._make_pr()
        config = KarenConfig(reviewer_order=["codex", "gemini"])

        mock_codex_error = ReviewResult(
            reviewer="codex",
            verdict=ReviewVerdict.ERROR,
            reason="CLI failed",
            tier=ReviewTier.LOW,
        )
        mock_gemini_pass = ReviewResult(
            reviewer="gemini",
            verdict=ReviewVerdict.PASS,
            reason="Approved",
            tier=ReviewTier.LOW,
        )

        with patch.object(CodexReviewer, "is_available", return_value=(True, "ok")):
            with patch.object(CodexReviewer, "review", return_value=mock_codex_error):
                with patch.object(GeminiReviewer, "is_available", return_value=(True, "ok")):
                    with patch.object(GeminiReviewer, "review", return_value=mock_gemini_pass):
                        result, all_results = run_review_chain(pr, config)

                        self.assertEqual(result.verdict, ReviewVerdict.PASS)
                        self.assertEqual(result.reviewer, "gemini")
                        self.assertEqual(len(all_results), 2)

    def test_second_judge_on_borderline(self):
        pr = self._make_pr()
        config = KarenConfig(reviewer_order=["codex", "gemini", "minimax"])

        mock_codex_borderline = ReviewResult(
            reviewer="codex",
            verdict=ReviewVerdict.BORDERLINE,
            reason="Needs review",
            tier=ReviewTier.HIGH,
        )
        mock_minimax_fail = ReviewResult(
            reviewer="minimax",
            verdict=ReviewVerdict.FAIL,
            reason="Ken agreed with Karen",
            tier=ReviewTier.HIGH,
        )

        with patch.object(CodexReviewer, "is_available", return_value=(True, "ok")):
            with patch.object(CodexReviewer, "review", return_value=mock_codex_borderline):
                with patch.object(MiniMaxReviewer, "is_available", return_value=(True, "ok")):
                    with patch.object(MiniMaxReviewer, "review_as_second_judge", return_value=mock_minimax_fail):
                        result, all_results = run_review_chain(pr, config)

                        self.assertEqual(result.verdict, ReviewVerdict.FAIL)
                        self.assertEqual(result.reviewer, "minimax")


if __name__ == "__main__":
    unittest.main()
