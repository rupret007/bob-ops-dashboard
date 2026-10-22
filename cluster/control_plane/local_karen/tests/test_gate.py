"""Tests for gate runner."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cluster.control_plane.local_karen.checks import CheckResult, CheckVerdict, PRInfo
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.gate import GateRunner, run_gate
from cluster.control_plane.local_karen.reviewers import ReviewResult, ReviewTier, ReviewVerdict


class TestGateRunner(unittest.TestCase):
    """Tests for GateRunner."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = KarenConfig(
            verdict_dir=self.tmpdir,
            state_dir=self.tmpdir,
        )
        # Deterministic rejections still receive a real review in production.
        # Tests isolate that provider boundary; individual tests override it below.
        review = ReviewResult("codex", ReviewVerdict.PASS, "Reviewed complete diff", tier=ReviewTier.LOW)
        patcher = patch("cluster.control_plane.local_karen.gate.run_review_chain", return_value=(review, [review]))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _mock_pr_info(self, **overrides) -> PRInfo:
        defaults = {
            "owner": "rupret007",
            "repo": "webjam",
            "number": 123,
            "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "title": "Test PR",
            "author": "testuser",
            "is_draft": False,
            "diff": "+print('hello')",
            "ci_status": {"build": "success"},
            "changed_files": ["file.py"],
            "deleted_files": [],
        }
        defaults.update(overrides)
        return PRInfo(**defaults)

    def test_fails_closed_on_fetch_error(self):
        with patch("cluster.control_plane.local_karen.gate.fetch_pr_info") as mock_fetch:
            mock_fetch.side_effect = Exception("Network error")

            runner = GateRunner(self.config)
            result = runner.run("rupret007", "webjam", 123)

            self.assertFalse(result.verdict.passed)
            self.assertIn("fail closed", result.verdict.reason.lower())
            self.assertIn("fetch", result.verdict.reason.lower())

    def test_fails_on_blocked_repo(self):
        pr = self._mock_pr_info(owner="rupret007", repo="rad-dad-show-night")

        with patch("cluster.control_plane.local_karen.gate.fetch_pr_info", return_value=pr):
            with patch("cluster.control_plane.local_karen.gate.verify_sha_current", return_value=True):
                runner = GateRunner(self.config)
                result = runner.run("rupret007", "rad-dad-show-night", 123)

                self.assertFalse(result.verdict.passed)
                self.assertIn("never-touch", result.verdict.reason.lower())

    def test_fails_on_draft(self):
        pr = self._mock_pr_info(is_draft=True)

        with patch("cluster.control_plane.local_karen.gate.fetch_pr_info", return_value=pr):
            with patch("cluster.control_plane.local_karen.gate.verify_sha_current", return_value=True):
                runner = GateRunner(self.config)
                result = runner.run("rupret007", "webjam", 123)

                self.assertFalse(result.verdict.passed)
                self.assertIn("draft", result.verdict.reason.lower())

    def test_fails_on_ci_failure(self):
        pr = self._mock_pr_info(ci_status={"build": "failure"})

        with patch("cluster.control_plane.local_karen.gate.fetch_pr_info", return_value=pr):
            with patch("cluster.control_plane.local_karen.gate.verify_sha_current", return_value=True):
                runner = GateRunner(self.config)
                result = runner.run("rupret007", "webjam", 123)

                self.assertFalse(result.verdict.passed)
                self.assertIn("ci failed", result.verdict.reason.lower())

    def test_fails_on_secrets_in_diff(self):
        pr = self._mock_pr_info(diff='+API_KEY = "sk-1234567890abcdefghijklmnopqrstuvwxyz123456789012"')

        with patch("cluster.control_plane.local_karen.gate.fetch_pr_info", return_value=pr):
            with patch("cluster.control_plane.local_karen.gate.verify_sha_current", return_value=True):
                runner = GateRunner(self.config)
                result = runner.run("rupret007", "webjam", 123)

                self.assertFalse(result.verdict.passed)
                self.assertIn("secret", result.verdict.reason.lower())

    def test_passes_with_successful_review(self):
        pr = self._mock_pr_info()

        mock_review_result = ReviewResult(
            reviewer="codex",
            verdict=ReviewVerdict.PASS,
            reason="Looks good",
            tier=ReviewTier.LOW,
        )

        with patch("cluster.control_plane.local_karen.gate.fetch_pr_info", return_value=pr):
            with patch("cluster.control_plane.local_karen.gate.verify_sha_current", return_value=True):
                with patch("cluster.control_plane.local_karen.gate.run_review_chain", return_value=(mock_review_result, [mock_review_result])):
                    runner = GateRunner(self.config)
                    result = runner.run("rupret007", "webjam", 123)

                    self.assertTrue(result.verdict.passed)
                    self.assertEqual(result.verdict.reviewer, "codex")

    def test_fails_on_review_fail(self):
        pr = self._mock_pr_info()

        mock_review_result = ReviewResult(
            reviewer="codex",
            verdict=ReviewVerdict.FAIL,
            reason="Missing tests",
            tier=ReviewTier.MEDIUM,
        )

        with patch("cluster.control_plane.local_karen.gate.fetch_pr_info", return_value=pr):
            with patch("cluster.control_plane.local_karen.gate.verify_sha_current", return_value=True):
                with patch("cluster.control_plane.local_karen.gate.run_review_chain", return_value=(mock_review_result, [mock_review_result])):
                    runner = GateRunner(self.config)
                    result = runner.run("rupret007", "webjam", 123)

                    self.assertFalse(result.verdict.passed)
                    self.assertIn("missing tests", result.verdict.reason.lower())

    def test_fails_on_review_error(self):
        pr = self._mock_pr_info()

        mock_review_result = ReviewResult(
            reviewer="codex",
            verdict=ReviewVerdict.ERROR,
            reason="CLI crashed",
            tier=ReviewTier.LOW,
        )

        with patch("cluster.control_plane.local_karen.gate.fetch_pr_info", return_value=pr):
            with patch("cluster.control_plane.local_karen.gate.verify_sha_current", return_value=True):
                with patch("cluster.control_plane.local_karen.gate.run_review_chain", return_value=(mock_review_result, [mock_review_result])):
                    runner = GateRunner(self.config)
                    result = runner.run("rupret007", "webjam", 123)

                    self.assertFalse(result.verdict.passed)
                    self.assertIn("fail closed", result.verdict.reason.lower())

    def test_invalidates_on_sha_change(self):
        pr = self._mock_pr_info()

        mock_review_result = ReviewResult(
            reviewer="codex",
            verdict=ReviewVerdict.PASS,
            reason="Looks good",
            tier=ReviewTier.LOW,
        )

        with patch("cluster.control_plane.local_karen.gate.fetch_pr_info", return_value=pr):
            with patch("cluster.control_plane.local_karen.gate.verify_sha_current", return_value=False):
                with patch("cluster.control_plane.local_karen.gate.run_review_chain", return_value=(mock_review_result, [mock_review_result])):
                    runner = GateRunner(self.config)
                    result = runner.run("rupret007", "webjam", 123)

                    self.assertFalse(result.verdict.passed)
                    self.assertFalse(result.sha_verified)
                    self.assertIn("sha changed", result.verdict.reason.lower())

    def test_saves_verdict_file(self):
        pr = self._mock_pr_info()

        mock_review_result = ReviewResult(
            reviewer="codex",
            verdict=ReviewVerdict.PASS,
            reason="Approved",
            tier=ReviewTier.LOW,
        )

        with patch("cluster.control_plane.local_karen.gate.fetch_pr_info", return_value=pr):
            with patch("cluster.control_plane.local_karen.gate.verify_sha_current", return_value=True):
                with patch("cluster.control_plane.local_karen.gate.run_review_chain", return_value=(mock_review_result, [mock_review_result])):
                    runner = GateRunner(self.config)
                    result = runner.run("rupret007", "webjam", 123)

                    self.assertIsNotNone(result.verdict_path)
                    self.assertTrue(Path(result.verdict_path).exists())
                    self.assertIn("KARENPASS", result.verdict_path)


class TestBorderlineHandling(unittest.TestCase):
    """Tests for borderline verdict handling."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = KarenConfig(
            verdict_dir=self.tmpdir,
            state_dir=self.tmpdir,
        )
        # Deterministic rejections still receive a real review in production.
        # Tests isolate that provider boundary; individual tests override it below.
        review = ReviewResult("codex", ReviewVerdict.PASS, "Reviewed complete diff", tier=ReviewTier.LOW)
        patcher = patch("cluster.control_plane.local_karen.gate.run_review_chain", return_value=(review, [review]))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _mock_pr_info(self) -> PRInfo:
        return PRInfo(
            owner="rupret007",
            repo="webjam",
            number=123,
            head_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            title="Test PR",
            author="testuser",
            is_draft=False,
            diff="+print('hello')",
            ci_status={"build": "success"},
            changed_files=["file.py"],
            deleted_files=[],
        )

    def test_borderline_becomes_fail(self):
        pr = self._mock_pr_info()

        mock_review_result = ReviewResult(
            reviewer="codex",
            verdict=ReviewVerdict.BORDERLINE,
            reason="Needs human review",
            tier=ReviewTier.HIGH,
        )

        with patch("cluster.control_plane.local_karen.gate.fetch_pr_info", return_value=pr):
            with patch("cluster.control_plane.local_karen.gate.verify_sha_current", return_value=True):
                with patch("cluster.control_plane.local_karen.gate.run_review_chain", return_value=(mock_review_result, [mock_review_result])):
                    runner = GateRunner(self.config)
                    result = runner.run("rupret007", "webjam", 123)

                    self.assertFalse(result.verdict.passed)


class TestConvenienceFunction(unittest.TestCase):
    """Tests for run_gate convenience function."""

    def test_run_gate_function(self):
        tmpdir = tempfile.mkdtemp()
        config = KarenConfig(verdict_dir=tmpdir, state_dir=tmpdir)

        pr = PRInfo(
            owner="rupret007",
            repo="webjam",
            number=123,
            head_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            title="Test PR",
            author="testuser",
            is_draft=False,
            diff="+print('hello')",
            ci_status={"build": "success"},
            changed_files=["file.py"],
            deleted_files=[],
        )

        mock_review_result = ReviewResult(
            reviewer="codex",
            verdict=ReviewVerdict.PASS,
            reason="OK",
            tier=ReviewTier.LOW,
        )

        with patch("cluster.control_plane.local_karen.gate.fetch_pr_info", return_value=pr):
            with patch("cluster.control_plane.local_karen.gate.verify_sha_current", return_value=True):
                with patch("cluster.control_plane.local_karen.gate.run_review_chain", return_value=(mock_review_result, [mock_review_result])):
                    result = run_gate("rupret007", "webjam", 123, config)

                    self.assertTrue(result.verdict.passed)


if __name__ == "__main__":
    unittest.main()
