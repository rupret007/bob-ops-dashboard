"""Tests for fail-closed never-touch repository guards.

These guards must block all write operations to never-touch repos
unless explicitly approved. The tests verify fail-closed behavior.
"""

import time
import unittest

from cluster.never_touch import (
    GatePassReceipt,
    NEVER_TOUCH_REPOS,
    NeverTouchViolation,
    assert_not_never_touch,
    check_gate_pass_at_sha,
    guard_pr_create,
    guard_pr_merge,
    guard_push,
    is_never_touch,
    normalize_repo,
)


class NormalizeRepoTests(unittest.TestCase):
    def test_basic_format(self):
        self.assertEqual(normalize_repo("owner/repo"), "owner/repo")
        self.assertEqual(normalize_repo("Owner/Repo"), "owner/repo")
        self.assertEqual(normalize_repo("  owner/repo  "), "owner/repo")

    def test_github_url_stripped(self):
        self.assertEqual(
            normalize_repo("https://github.com/owner/repo"),
            "owner/repo",
        )
        self.assertEqual(
            normalize_repo("https://github.com/owner/repo/"),
            "owner/repo",
        )

    def test_invalid_formats_raise(self):
        invalid = [
            "",
            "repo",
            "owner/",
            "/repo",
            "owner/repo/extra",
            "owner repo",
            "https://gitlab.com/owner/repo",
        ]
        for bad in invalid:
            with self.assertRaises(ValueError, msg=f"should reject {bad!r}"):
                normalize_repo(bad)


class IsNeverTouchTests(unittest.TestCase):
    def test_known_never_touch_repo(self):
        self.assertTrue(is_never_touch("rupret007/rad-dad-show-night"))
        self.assertTrue(is_never_touch("RUPRET007/RAD-DAD-SHOW-NIGHT"))
        self.assertTrue(
            is_never_touch("https://github.com/rupret007/rad-dad-show-night")
        )

    def test_other_repos_allowed(self):
        self.assertFalse(is_never_touch("rupret007/bob-ops-dashboard"))
        self.assertFalse(is_never_touch("rupret007/webjam"))
        self.assertFalse(is_never_touch("other-owner/rad-dad-show-night"))

    def test_invalid_repo_returns_false(self):
        self.assertFalse(is_never_touch(""))
        self.assertFalse(is_never_touch("not-a-repo"))


class AssertNotNeverTouchTests(unittest.TestCase):
    def test_never_touch_raises(self):
        with self.assertRaises(NeverTouchViolation) as ctx:
            assert_not_never_touch("rupret007/rad-dad-show-night", "test op")
        self.assertIn("rad-dad-show-night", str(ctx.exception))
        self.assertIn("test op", str(ctx.exception))
        self.assertIn("human approval", str(ctx.exception))

    def test_allowed_repo_passes(self):
        assert_not_never_touch("rupret007/bob-ops-dashboard", "test op")
        assert_not_never_touch("rupret007/webjam", "test op")

    def test_invalid_repo_raises(self):
        with self.assertRaises(NeverTouchViolation) as ctx:
            assert_not_never_touch("", "test op")
        self.assertIn("invalid repo", str(ctx.exception))


class GuardPushTests(unittest.TestCase):
    def test_push_to_never_touch_blocked(self):
        with self.assertRaises(NeverTouchViolation) as ctx:
            guard_push(
                "rupret007/rad-dad-show-night",
                "proj-max/R10-fix",
                "a" * 40,
            )
        self.assertIn("push", str(ctx.exception))
        self.assertIn("proj-max/R10-fix", str(ctx.exception))

    def test_push_to_normal_repo_allowed(self):
        guard_push("rupret007/bob-ops-dashboard", "cursor/fix", "a" * 40)


class GuardPrCreateTests(unittest.TestCase):
    def test_pr_create_on_never_touch_blocked(self):
        with self.assertRaises(NeverTouchViolation) as ctx:
            guard_pr_create(
                "rupret007/rad-dad-show-night",
                "proj-max/R10-fix",
                "main",
            )
        self.assertIn("create PR", str(ctx.exception))

    def test_pr_create_on_normal_repo_allowed(self):
        guard_pr_create("rupret007/webjam", "cursor/fix", "main")


class GuardPrMergeTests(unittest.TestCase):
    def test_merge_on_never_touch_always_blocked(self):
        """Never-touch repos are ALWAYS blocked for automation, regardless of gate_pass."""
        with self.assertRaises(NeverTouchViolation) as ctx:
            guard_pr_merge(
                "rupret007/rad-dad-show-night",
                67,
                "a" * 40,
                gate_pass=None,
            )
        self.assertIn("merge PR #67", str(ctx.exception))
        self.assertIn("human approval", str(ctx.exception))

    def test_merge_on_never_touch_blocked_even_with_pass(self):
        """Even with a valid gate pass, never-touch repos block automation."""
        sha = "a" * 40
        gate_pass = GatePassReceipt(
            repo="rupret007/rad-dad-show-night",
            sha=sha,
            reviewer="karen",
            expires_at=time.time() + 3600,
        )
        with self.assertRaises(NeverTouchViolation):
            guard_pr_merge("rupret007/rad-dad-show-night", 67, sha, gate_pass)

    def test_merge_on_normal_repo_allowed(self):
        """Normal repos allow automated merge (gate_pass optional for now)."""
        guard_pr_merge("rupret007/webjam", 159, "a" * 40, gate_pass=None)

    def test_merge_on_normal_repo_with_pass_allowed(self):
        """Normal repos with valid gate_pass also allowed."""
        sha = "a" * 40
        gate_pass = GatePassReceipt(
            repo="rupret007/webjam",
            sha=sha,
            reviewer="karen",
            expires_at=time.time() + 3600,
        )
        guard_pr_merge("rupret007/webjam", 159, sha, gate_pass)


class CheckGatePassAtShaTests(unittest.TestCase):
    """Tests for gate pass verification (for future opt-in repos)."""

    def test_non_never_touch_repo_skips_check(self):
        """Normal repos don't require gate pass verification."""
        check_gate_pass_at_sha("rupret007/webjam", "a" * 40, None, "test")

    def test_never_touch_requires_pass(self):
        """Never-touch repos require a valid gate pass."""
        with self.assertRaises(NeverTouchViolation) as ctx:
            check_gate_pass_at_sha(
                "rupret007/rad-dad-show-night",
                "a" * 40,
                None,
                "test merge",
            )
        self.assertIn("no gate PASS", str(ctx.exception))

    def test_never_touch_with_valid_pass(self):
        """Never-touch repos with valid pass don't raise."""
        sha = "a" * 40
        gate_pass = GatePassReceipt(
            repo="rupret007/rad-dad-show-night",
            sha=sha,
            reviewer="karen",
            expires_at=time.time() + 3600,
        )
        check_gate_pass_at_sha(
            "rupret007/rad-dad-show-night",
            sha,
            gate_pass,
            "test",
        )

    def test_stale_sha_rejected(self):
        """Gate pass at wrong SHA is rejected."""
        gate_pass = GatePassReceipt(
            repo="rupret007/rad-dad-show-night",
            sha="a" * 40,
            reviewer="karen",
            expires_at=time.time() + 3600,
        )
        with self.assertRaises(NeverTouchViolation) as ctx:
            check_gate_pass_at_sha(
                "rupret007/rad-dad-show-night",
                "b" * 40,
                gate_pass,
                "test",
            )
        self.assertIn("HEAD is bbbb", str(ctx.exception))

    def test_expired_pass_rejected(self):
        """Expired gate pass is rejected."""
        sha = "a" * 40
        gate_pass = GatePassReceipt(
            repo="rupret007/rad-dad-show-night",
            sha=sha,
            reviewer="karen",
            expires_at=time.time() - 1,
        )
        with self.assertRaises(NeverTouchViolation) as ctx:
            check_gate_pass_at_sha(
                "rupret007/rad-dad-show-night",
                sha,
                gate_pass,
                "test",
            )
        self.assertIn("expired", str(ctx.exception))

    def test_wrong_repo_pass_rejected(self):
        """Gate pass for different repo is rejected."""
        sha = "a" * 40
        gate_pass = GatePassReceipt(
            repo="rupret007/webjam",
            sha=sha,
            reviewer="karen",
            expires_at=time.time() + 3600,
        )
        with self.assertRaises(NeverTouchViolation) as ctx:
            check_gate_pass_at_sha(
                "rupret007/rad-dad-show-night",
                sha,
                gate_pass,
                "test",
            )
        self.assertIn("different repo", str(ctx.exception))


class NeverTouchReposContentTests(unittest.TestCase):
    def test_show_night_in_list(self):
        self.assertIn("rupret007/rad-dad-show-night", NEVER_TOUCH_REPOS)

    def test_list_is_frozen(self):
        with self.assertRaises(AttributeError):
            NEVER_TOUCH_REPOS.add("another/repo")


class NeverTouchViolationTests(unittest.TestCase):
    def test_exception_attributes(self):
        exc = NeverTouchViolation("owner/repo", "merge", "detail")
        self.assertEqual(exc.repo, "owner/repo")
        self.assertEqual(exc.operation, "merge")
        self.assertIn("BLOCKED", str(exc))
        self.assertIn("owner/repo", str(exc))
        self.assertIn("merge", str(exc))
        self.assertIn("detail", str(exc))

    def test_is_permission_error(self):
        exc = NeverTouchViolation("owner/repo", "push")
        self.assertIsInstance(exc, PermissionError)


if __name__ == "__main__":
    unittest.main()
