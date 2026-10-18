"""Tests for deterministic checks."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from cluster.control_plane.local_karen.checks import (
    CheckVerdict,
    PRInfo,
    check_blocked_repo,
    check_ci_green,
    check_no_dangerous_commands,
    check_no_secrets_in_diff,
    check_not_draft,
    check_tests_not_weakened,
    run_all_checks,
    all_checks_pass,
    get_failed_checks,
    _HAS_NEVER_TOUCH,
)
from cluster.control_plane.local_karen.config import KarenConfig


class TestCheckHelpers(unittest.TestCase):
    """Tests for check helper functions."""

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
            "ci_status": {"build": "success", "test": "success"},
            "changed_files": ["file.py"],
            "deleted_files": [],
        }
        defaults.update(overrides)
        return PRInfo(**defaults)

    def _make_config(self, **overrides) -> KarenConfig:
        defaults = {
            "never_touch_repos": frozenset(["rupret007/rad-dad-show-night"]),
        }
        defaults.update(overrides)
        return KarenConfig(**defaults)


class TestBlockedRepoCheck(TestCheckHelpers):
    """Tests for blocked repo check."""

    def test_passes_allowed_repo(self):
        pr = self._make_pr(owner="rupret007", repo="webjam")
        config = self._make_config()
        result = check_blocked_repo(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.PASS)

    def test_fails_blocked_repo_from_config(self):
        """Fails when repo is in local config never_touch_repos."""
        pr = self._make_pr(owner="rupret007", repo="rad-dad-show-night")
        config = self._make_config()
        result = check_blocked_repo(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)
        self.assertIn("never-touch", result.reason.lower())

    @unittest.skipUnless(_HAS_NEVER_TOUCH, "cluster.never_touch not available")
    def test_fails_blocked_repo_from_authoritative_list(self):
        """Fails when repo is on authoritative never_touch list."""
        pr = self._make_pr(owner="rupret007", repo="rad-dad-show-night")
        config = self._make_config(never_touch_repos=frozenset())
        result = check_blocked_repo(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)
        self.assertIn("authoritative", result.reason.lower())

    def test_fails_blocked_repo_case_insensitive(self):
        """Never-touch check should handle case variations."""
        pr = self._make_pr(owner="Rupret007", repo="Rad-Dad-Show-Night")
        config = self._make_config()
        result = check_blocked_repo(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)


class TestDraftCheck(TestCheckHelpers):
    """Tests for draft check."""

    def test_passes_non_draft(self):
        pr = self._make_pr(is_draft=False)
        config = self._make_config()
        result = check_not_draft(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.PASS)

    def test_fails_draft(self):
        pr = self._make_pr(is_draft=True)
        config = self._make_config()
        result = check_not_draft(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)
        self.assertIn("draft", result.reason.lower())


class TestCICheck(TestCheckHelpers):
    """Tests for CI status check."""

    def test_passes_all_success(self):
        pr = self._make_pr(ci_status={
            "build": "success",
            "test": "success",
            "lint": "skipped",
        })
        config = self._make_config()
        result = check_ci_green(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.PASS)

    def test_fails_pending(self):
        pr = self._make_pr(ci_status={"build": "pending"})
        config = self._make_config()
        result = check_ci_green(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)
        self.assertIn("incomplete", result.reason.lower())

    def test_fails_failure(self):
        pr = self._make_pr(ci_status={"build": "failure"})
        config = self._make_config()
        result = check_ci_green(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)
        self.assertIn("failed", result.reason.lower())

    def test_passes_neutral(self):
        pr = self._make_pr(ci_status={"optional": "neutral"})
        config = self._make_config()
        result = check_ci_green(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.PASS)


class TestSecretsCheck(TestCheckHelpers):
    """Tests for secrets detection."""

    def test_passes_clean_diff(self):
        pr = self._make_pr(diff="""
diff --git a/file.py b/file.py
+print('hello world')
+x = 42
""")
        config = self._make_config()
        result = check_no_secrets_in_diff(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.PASS)

    def test_fails_api_key(self):
        # Build synthetic inputs at runtime so the gate can scan its own tests.
        pr = self._make_pr(diff=(
            'diff --git a/config.py b/config.py\n+API_KEY = "{}"\n'
        ).format("sk-" + "1234567890abcdefghijklmnopqrstuvwxyz123456789012"))
        config = self._make_config()
        result = check_no_secrets_in_diff(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)
        self.assertIn("secret", result.reason.lower())

    def test_fails_github_pat(self):
        pr = self._make_pr(diff=(
            "diff --git a/.env b/.env\n+GITHUB_TOKEN={}\n"
        ).format("ghp_" + "x" * 36))
        config = self._make_config()
        result = check_no_secrets_in_diff(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)

    def test_fails_private_key(self):
        pr = self._make_pr(diff=(
            "diff --git a/key.pem b/key.pem\n+-----BEGIN {}-----\n"
            "+MIIEpQIBAAKCAQEA0Z...\n"
        ).format("RSA PRIVATE KEY"))
        config = self._make_config()
        result = check_no_secrets_in_diff(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)

    def test_ignores_deletions(self):
        pr = self._make_pr(diff=(
            'diff --git a/config.py b/config.py\n-API_KEY = "{}"\n'
            '+API_KEY = os.environ.get("API_KEY")\n'
        ).format("sk-" + "1234567890abcdefghijklmnopqrstuvwxyz123456789012"))
        config = self._make_config()
        result = check_no_secrets_in_diff(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.PASS)

    def test_diagnostics_redact_source_including_test_files(self):
        secret = "ghp_" + "x" * 36
        pr = self._make_pr(diff=(
            "diff --git a/tests/test_config.py b/tests/test_config.py\n"
            "@@ -0,0 +1 @@\n+PRIVATE_CONTEXT={}\n"
        ).format(secret))
        result = check_no_secrets_in_diff(pr, self._make_config())

        self.assertEqual(result.verdict, CheckVerdict.FAIL)
        self.assertIn("GitHub PAT", result.details)
        self.assertIn("tests/test_config.py at diff line 3", result.details)
        self.assertIn("[REDACTED]", result.details)
        self.assertNotIn(secret, str(result))
        self.assertNotIn(secret[:12], str(result))
        self.assertNotIn("PRIVATE_CONTEXT", str(result))


class TestTestWeakeningCheck(TestCheckHelpers):
    """Tests for test weakening detection."""

    def test_passes_no_test_changes(self):
        pr = self._make_pr(
            diff="""
diff --git a/app.py b/app.py
+def new_feature():
+    return 42
""",
            deleted_files=[],
        )
        config = self._make_config()
        result = check_tests_not_weakened(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.PASS)

    def test_fails_deleted_test_file(self):
        pr = self._make_pr(
            diff="",
            deleted_files=["tests/test_feature.py"],
        )
        config = self._make_config()
        result = check_tests_not_weakened(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)
        self.assertIn("weakened", result.reason.lower())
        self.assertIn("deleted test file", result.details.lower())

    def test_fails_removed_test_function(self):
        pr = self._make_pr(diff="""
diff --git a/tests/test_app.py b/tests/test_app.py
-def test_important_feature():
-    assert feature() == expected
""")
        config = self._make_config()
        result = check_tests_not_weakened(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)
        self.assertIn("removed test function", result.details.lower())

    def test_fails_removed_assertion(self):
        pr = self._make_pr(diff="""
diff --git a/tests/test_app.py b/tests/test_app.py
 def test_feature():
-    assert result == expected
+    pass
""")
        config = self._make_config()
        result = check_tests_not_weakened(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)
        self.assertIn("assertion", result.details.lower())


class TestDangerousCommandsCheck(TestCheckHelpers):
    """Tests for dangerous command detection."""

    def test_passes_safe_code(self):
        pr = self._make_pr(diff="""
diff --git a/app.py b/app.py
+def safe_function():
+    return 42
""")
        config = self._make_config()
        result = check_no_dangerous_commands(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.PASS)

    def test_fails_sudo(self):
        pr = self._make_pr(diff=(
            "diff --git a/install.sh b/install.sh\n+{}\n"
        ).format(" ".join(("sudo", "apt", "install", "package"))))
        config = self._make_config()
        result = check_no_dangerous_commands(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)
        self.assertIn("dangerous", result.reason.lower())
        self.assertIn("sudo", result.details.lower())

    def test_fails_force_push(self):
        pr = self._make_pr(diff=(
            "diff --git a/deploy.sh b/deploy.sh\n+{}\n"
        ).format(" ".join(("git", "push", "--force", "origin", "main"))))
        config = self._make_config()
        result = check_no_dangerous_commands(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)
        self.assertIn("dangerous", result.reason.lower())
        self.assertIn("force push", result.details.lower())

    def test_fails_rm_rf_root(self):
        pr = self._make_pr(diff=(
            "diff --git a/cleanup.sh b/cleanup.sh\n+{}\n"
        ).format(" ".join(("rm", "-rf", "/var/log/*"))))
        config = self._make_config()
        result = check_no_dangerous_commands(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)

    def test_fails_tag_creation(self):
        pr = self._make_pr(diff=(
            "diff --git a/release.sh b/release.sh\n+{}\n"
        ).format(" ".join(("git", "tag", "v1.0.0"))))
        config = self._make_config()
        result = check_no_dangerous_commands(pr, config)
        self.assertEqual(result.verdict, CheckVerdict.FAIL)
        self.assertIn("dangerous", result.reason.lower())
        self.assertIn("tag", result.details.lower())

    def test_rejects_every_command_category_including_test_files(self):
        commands = [
            ["sudo", "apt", "install", "package"],
            ["rm", "-rf", "/var/log/*"],
            ["rm", "-rf", "~/fixture"],
            ["git", "push", "--force", "origin", "main"],
            ["git", "push", "-f", "origin", "main"],
            ["git", "tag", "v1.0.0"],
            ["gh", "release", "create", "v1.0.0"],
            ["git", "push", "origin", "--tags"],
            ["chmod", "777", "fixture"],
        ]
        source_lines = [" ".join(argv) for argv in commands]
        source_lines += ["{}(payload)".format(name) for name in ("eval", "exec")]
        for source in source_lines:
            with self.subTest(source=source):
                pr = self._make_pr(diff=(
                    "diff --git a/tests/test_commands.py b/tests/test_commands.py\n+{}\n"
                ).format(source))
                result = check_no_dangerous_commands(pr, self._make_config())
                self.assertEqual(result.verdict, CheckVerdict.FAIL)

    def test_diagnostics_do_not_copy_command_arguments(self):
        secret = "ghp_" + "x" * 36
        command = " ".join(("sudo", "PRIVATE_CONTEXT", secret))
        pr = self._make_pr(diff=(
            "diff --git a/install.sh b/install.sh\n+{}\n"
        ).format(command))
        result = check_no_dangerous_commands(pr, self._make_config())

        self.assertEqual(result.verdict, CheckVerdict.FAIL)
        self.assertIn("sudo", result.details)
        self.assertIn("install.sh at diff line 2", result.details)
        self.assertNotIn(secret, str(result))
        self.assertNotIn("PRIVATE_CONTEXT", str(result))


class TestRunAllChecks(TestCheckHelpers):
    """Tests for running all checks together."""

    def test_all_pass(self):
        pr = self._make_pr()
        config = self._make_config()
        results = run_all_checks(pr, config)

        self.assertTrue(all_checks_pass(results))
        self.assertEqual(len(get_failed_checks(results)), 0)

    def test_one_fails(self):
        pr = self._make_pr(is_draft=True)
        config = self._make_config()
        results = run_all_checks(pr, config)

        self.assertFalse(all_checks_pass(results))
        failed = get_failed_checks(results)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].check_name, "not_draft")

    def test_blocked_repo_fails_first(self):
        pr = self._make_pr(owner="rupret007", repo="rad-dad-show-night")
        config = self._make_config()
        results = run_all_checks(pr, config)

        self.assertFalse(all_checks_pass(results))
        failed = get_failed_checks(results)
        self.assertTrue(any(f.check_name == "blocked_repo" for f in failed))


if __name__ == "__main__":
    unittest.main()
