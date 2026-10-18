"""Tests for verdict file handling."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cluster.control_plane.local_karen.checks import CheckResult, CheckVerdict, PRInfo
from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.reviewers import ReviewResult, ReviewTier, ReviewVerdict
from cluster.control_plane.local_karen.verdict import (
    Verdict,
    create_verdict,
    format_verdict_comment,
    is_verdict_stale,
    load_latest_verdict,
    load_verdict,
    save_verdict,
)


class TestVerdict(unittest.TestCase):
    """Tests for Verdict dataclass."""

    def test_verdict_type_pass(self):
        v = Verdict(
            owner="test", repo="test", pr_number=1, head_sha="abc123",
            passed=True, reason="ok", timestamp="", reviewer="test",
            tier="low", confidence=1.0, check_results=[], review_results=[],
        )
        self.assertEqual(v.verdict_type, "KARENPASS")

    def test_verdict_type_fail(self):
        v = Verdict(
            owner="test", repo="test", pr_number=1, head_sha="abc123",
            passed=False, reason="not ok", timestamp="", reviewer="test",
            tier="low", confidence=1.0, check_results=[], review_results=[],
        )
        self.assertEqual(v.verdict_type, "KARENFAIL")

    def test_verdict_key(self):
        v = Verdict(
            owner="rupret007", repo="webjam", pr_number=123, head_sha="abc123def456",
            passed=True, reason="ok", timestamp="", reviewer="test",
            tier="low", confidence=1.0, check_results=[], review_results=[],
        )
        self.assertEqual(v.key, "rupret007/webjam#123@abc123def456")

    def test_roundtrip_json(self):
        v = Verdict(
            owner="rupret007", repo="webjam", pr_number=123, head_sha="abc123def456",
            passed=True, reason="Approved", timestamp="2024-01-01T00:00:00Z",
            reviewer="codex", tier="medium", confidence=0.9,
            check_results=[{"check": "ci", "verdict": "PASS", "reason": "ok", "details": None}],
            review_results=[{"reviewer": "codex", "verdict": "PASS", "reason": "ok", "confidence": 0.9, "cost_usd": 0.01, "duration_sec": 5.0}],
            budget_used_usd=0.01, duration_sec=5.0,
        )

        json_str = v.to_json()
        v2 = Verdict.from_json(json_str)

        self.assertEqual(v.owner, v2.owner)
        self.assertEqual(v.repo, v2.repo)
        self.assertEqual(v.pr_number, v2.pr_number)
        self.assertEqual(v.head_sha, v2.head_sha)
        self.assertEqual(v.passed, v2.passed)
        self.assertEqual(v.reason, v2.reason)


class TestVerdictStorage(unittest.TestCase):
    """Tests for verdict storage."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = KarenConfig(verdict_dir=self.tmpdir)

    def test_save_and_load_pass(self):
        v = Verdict(
            owner="rupret007", repo="webjam", pr_number=123, head_sha="abc123def456789",
            passed=True, reason="Approved", timestamp="2024-01-01T00:00:00Z",
            reviewer="codex", tier="medium", confidence=0.9,
            check_results=[], review_results=[],
        )

        path = save_verdict(v, self.config)
        self.assertTrue(path.exists())
        self.assertIn("KARENPASS", path.name)

        loaded = load_verdict("rupret007", "webjam", 123, "abc123def456789", self.config)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.passed, True)
        self.assertEqual(loaded.reason, "Approved")

    def test_save_and_load_fail(self):
        v = Verdict(
            owner="rupret007", repo="webjam", pr_number=123, head_sha="abc123def456789",
            passed=False, reason="Tests missing", timestamp="2024-01-01T00:00:00Z",
            reviewer="codex", tier="medium", confidence=0.9,
            check_results=[], review_results=[],
        )

        path = save_verdict(v, self.config)
        self.assertTrue(path.exists())
        self.assertIn("KARENFAIL", path.name)

        loaded = load_verdict("rupret007", "webjam", 123, "abc123def456789", self.config)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.passed, False)

    def test_load_nonexistent_returns_none(self):
        loaded = load_verdict("rupret007", "webjam", 999, "nonexistent", self.config)
        self.assertIsNone(loaded)

    def test_load_latest_verdict(self):
        v1 = Verdict(
            owner="rupret007", repo="webjam", pr_number=123, head_sha="sha111111111",
            passed=False, reason="First run failed", timestamp="2024-01-01T00:00:00Z",
            reviewer="codex", tier="low", confidence=1.0,
            check_results=[], review_results=[],
        )
        save_verdict(v1, self.config)

        v2 = Verdict(
            owner="rupret007", repo="webjam", pr_number=123, head_sha="sha222222222",
            passed=True, reason="Second run passed", timestamp="2024-01-02T00:00:00Z",
            reviewer="codex", tier="low", confidence=1.0,
            check_results=[], review_results=[],
        )
        save_verdict(v2, self.config)

        latest = load_latest_verdict("rupret007", "webjam", 123, self.config)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.head_sha, "sha222222222")
        self.assertTrue(latest.passed)


class TestVerdictStaleness(unittest.TestCase):
    """Tests for verdict staleness checking."""

    def test_not_stale_when_sha_matches(self):
        v = Verdict(
            owner="test", repo="test", pr_number=1, head_sha="abc123",
            passed=True, reason="ok", timestamp="", reviewer="test",
            tier="low", confidence=1.0, check_results=[], review_results=[],
        )
        self.assertFalse(is_verdict_stale(v, "abc123"))

    def test_stale_when_sha_differs(self):
        v = Verdict(
            owner="test", repo="test", pr_number=1, head_sha="abc123",
            passed=True, reason="ok", timestamp="", reviewer="test",
            tier="low", confidence=1.0, check_results=[], review_results=[],
        )
        self.assertTrue(is_verdict_stale(v, "def456"))


class TestCreateVerdict(unittest.TestCase):
    """Tests for creating verdicts from results."""

    def test_create_verdict_from_results(self):
        pr = PRInfo(
            owner="rupret007", repo="webjam", number=123, head_sha="abc123def456",
            title="Test PR", author="user", is_draft=False, diff="",
            ci_status={}, changed_files=[], deleted_files=[],
        )

        check_results = [
            CheckResult(check_name="ci", verdict=CheckVerdict.PASS, reason="ok"),
            CheckResult(check_name="draft", verdict=CheckVerdict.PASS, reason="ok"),
        ]

        review_results = [
            ReviewResult(
                reviewer="codex", verdict=ReviewVerdict.PASS, reason="Approved",
                tier=ReviewTier.MEDIUM, confidence=0.9, cost_usd=0.05, duration_sec=10.0,
            ),
        ]

        verdict = create_verdict(
            pr=pr,
            check_results=check_results,
            review_results=review_results,
            final_passed=True,
            final_reason="Approved",
            final_reviewer="codex",
        )

        self.assertEqual(verdict.owner, "rupret007")
        self.assertEqual(verdict.repo, "webjam")
        self.assertEqual(verdict.pr_number, 123)
        self.assertEqual(verdict.head_sha, "abc123def456")
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.reason, "Approved")
        self.assertEqual(verdict.reviewer, "codex")
        self.assertEqual(verdict.tier, "medium")
        self.assertEqual(verdict.confidence, 0.9)
        self.assertEqual(len(verdict.check_results), 2)
        self.assertEqual(len(verdict.review_results), 1)
        self.assertEqual(verdict.budget_used_usd, 0.05)


class TestFormatVerdictComment(unittest.TestCase):
    """Tests for formatting verdict as PR comment."""

    def test_format_pass_comment(self):
        v = Verdict(
            owner="rupret007", repo="webjam", pr_number=123, head_sha="abc123def456",
            passed=True, reason="All checks passed", timestamp="2024-01-01T00:00:00Z",
            reviewer="codex", tier="medium", confidence=0.9,
            check_results=[
                {"check": "ci", "verdict": "PASS", "reason": "CI green", "details": None},
            ],
            review_results=[
                {"reviewer": "codex", "verdict": "PASS", "reason": "Approved", "confidence": 0.9, "cost_usd": 0.05, "duration_sec": 10.0},
            ],
            budget_used_usd=0.05, duration_sec=10.0,
        )

        comment = format_verdict_comment(v)

        self.assertIn("KARENPASS", comment)
        self.assertIn("✅", comment)
        self.assertIn("abc123def456", comment)
        self.assertIn("codex", comment)
        self.assertIn("CI green", comment)
        self.assertIn("$0.05", comment)

    def test_format_fail_comment(self):
        v = Verdict(
            owner="rupret007", repo="webjam", pr_number=123, head_sha="abc123def456",
            passed=False, reason="Tests missing", timestamp="2024-01-01T00:00:00Z",
            reviewer="codex", tier="high", confidence=0.8,
            check_results=[
                {"check": "ci", "verdict": "PASS", "reason": "CI green", "details": None},
            ],
            review_results=[
                {"reviewer": "codex", "verdict": "FAIL", "reason": "Tests missing", "confidence": 0.8, "cost_usd": 0.05, "duration_sec": 10.0},
            ],
            budget_used_usd=0.05, duration_sec=10.0,
        )

        comment = format_verdict_comment(v)

        self.assertIn("KARENFAIL", comment)
        self.assertIn("❌", comment)
        self.assertIn("Tests missing", comment)


if __name__ == "__main__":
    unittest.main()
