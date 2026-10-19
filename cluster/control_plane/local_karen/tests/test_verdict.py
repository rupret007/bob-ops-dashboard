"""Tests for verdict file handling."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

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
        first_path = save_verdict(v1, self.config)

        v2 = Verdict(
            owner="rupret007", repo="webjam", pr_number=123, head_sha="sha222222222",
            passed=True, reason="Second run passed", timestamp="2024-01-02T00:00:00Z",
            reviewer="codex", tier="low", confidence=1.0,
            check_results=[], review_results=[],
        )
        second_path = save_verdict(v2, self.config)
        # WSL/shared filesystems can give consecutive writes identical mtimes.
        os.utime(first_path, (100, 100))
        os.utime(second_path, (100, 100))

        latest = load_latest_verdict("rupret007", "webjam", 123, self.config)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.head_sha, "sha222222222")
        self.assertTrue(latest.passed)

    def _stored(self, sha="a" * 40, **overrides):
        value = Verdict("rupret007", "webjam", 123, sha, False, "Needs repair",
                        "2024-01-01T00:00:00Z", "codex", "low", 1.0, [], [])
        value = replace(value, **overrides)
        return value, save_verdict(value, self.config)

    def test_latest_compares_actual_time_instead_of_lexical_order_or_mtime(self):
        _, old_path = self._stored(timestamp="2024-01-01T01:00:00+02:00")
        newer, new_path = self._stored("b" * 40, timestamp="2023-12-31T23:30:00Z")
        os.utime(old_path, (200, 200))
        os.utime(new_path, (100, 100))
        self.assertEqual(load_latest_verdict("rupret007", "webjam", 123, self.config), newer)
        # An any-head reporting lookup never changes exact-head lookup identity.
        self.assertIsNone(load_verdict("rupret007", "webjam", 123, "c" * 40, self.config))

    def test_unpublished_pass_cannot_override_retained_fail_marker(self):
        old, _ = self._stored()
        new = replace(old, passed=True, timestamp="2024-01-02T00:00:00Z")
        with patch.object(Path, "symlink_to", side_effect=OSError("injected failure")):
            with self.assertRaises(OSError):
                save_verdict(new, self.config)
        self.assertEqual(load_latest_verdict("rupret007", "webjam", 123, self.config), old)

    def test_newer_fail_blocks_when_marker_update_fails(self):
        old, _ = self._stored(passed=True)
        new = replace(old, passed=False, timestamp="2024-01-02T00:00:00Z")
        with patch.object(Path, "symlink_to", side_effect=OSError("injected failure")):
            with self.assertRaises(OSError):
                save_verdict(new, self.config)
        self.assertEqual(load_latest_verdict("rupret007", "webjam", 123, self.config), new)

    def test_equal_observation_times_choose_blocking_verdict(self):
        failed, _ = self._stored()
        self._stored("b" * 40, passed=True)
        self.assertEqual(load_latest_verdict("rupret007", "webjam", 123, self.config), failed)

    def test_latest_ignores_wrong_identity_incomplete_and_malformed_records(self):
        value, _ = self._stored()
        marker = next(Path(self.tmpdir).glob("*_LATEST.json"))
        marker.unlink()
        for bad in (replace(value, owner="another"), replace(value, repo="another"),
                    replace(value, pr_number=124), replace(value, head_sha="b" * 40),
                    replace(value, status="ERROR"), replace(value, timestamp="invalid"),
                    replace(value, timestamp="2024-01-01T00:00:00"),
                    replace(value, passed="true")):
            with self.subTest(bad=bad.to_dict()):
                marker.write_text(bad.to_json())
                self.assertIsNone(load_latest_verdict("rupret007", "webjam", 123, self.config))
        marker.write_text("{incomplete")
        self.assertIsNone(load_latest_verdict("rupret007", "webjam", 123, self.config))

    def test_latest_accepts_authenticated_copy_marker_without_symlink(self):
        value, path = self._stored()
        marker = next(Path(self.tmpdir).glob("*_LATEST.json"))
        marker.unlink()
        marker.write_text(path.read_text())
        self.assertEqual(load_latest_verdict("rupret007", "webjam", 123, self.config), value)

    def test_unpublished_artifact_without_marker_is_not_latest(self):
        self._stored(passed=True)
        next(Path(self.tmpdir).glob("*_LATEST.json")).unlink()
        self.assertIsNone(load_latest_verdict("rupret007", "webjam", 123, self.config))

    def test_marker_cannot_read_record_outside_verdict_directory(self):
        self._stored()
        marker = next(Path(self.tmpdir).glob("*_LATEST.json"))
        marker.unlink()
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "outside.json"
            target.write_text("Do not read this unrelated file")
            marker.symlink_to(target)
            with patch.object(Path, "read_text", side_effect=AssertionError("No outside reads")):
                self.assertIsNone(load_latest_verdict("rupret007", "webjam", 123, self.config))


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
