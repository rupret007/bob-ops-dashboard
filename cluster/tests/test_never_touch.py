"""Tests for fail-closed never-touch repository guards.

These guards must block all write operations to never-touch repos
unless explicitly approved. The tests verify fail-closed behavior.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_clone_url_forms_are_canonical(self):
        for repo in (
            "Owner/Repo.git",
            "https://github.com/Owner/Repo.git",
            "HTTPS://GITHUB.COM/Owner/Repo.GIT/",
            "git@github.com:Owner/Repo.git",
            "ssh://git@github.com/Owner/Repo.git",
        ):
            with self.subTest(repo=repo):
                self.assertEqual(normalize_repo(repo), "owner/repo")

    def test_non_string_repositories_fail_closed(self):
        for repo in (None, 0, [], {}):
            with self.subTest(repo=repo):
                with self.assertRaises(ValueError):
                    normalize_repo(repo)
                with self.assertRaises(NeverTouchViolation):
                    assert_not_never_touch(repo, "write")


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

    def test_matching_malformed_shas_cannot_authorize_a_pass(self):
        for sha in (None, False, 1, "", "a" * 39, "a" * 41, "g" * 40, "a" * 40 + "\n"):
            with self.subTest(sha=sha):
                receipt = GatePassReceipt(
                    repo="rupret007/rad-dad-show-night", sha=sha,
                    reviewer="karen", expires_at=time.time() + 3600,
                )
                with self.assertRaises(NeverTouchViolation):
                    check_gate_pass_at_sha(receipt.repo, sha, receipt, "merge")

    def test_malformed_receipt_sha_is_rejected(self):
        for sha in (None, False, 1, [], {}):
            with self.subTest(sha=sha):
                receipt = GatePassReceipt(
                    repo="rupret007/rad-dad-show-night", sha=sha,
                    reviewer="karen", expires_at=time.time() + 3600,
                )
                with self.assertRaises(NeverTouchViolation):
                    check_gate_pass_at_sha(receipt.repo, "a" * 40, receipt, "merge")

    def test_invalid_receipt_objects_are_rejected(self):
        for receipt in ({}, False, "PASS", object()):
            with self.subTest(receipt=receipt):
                with self.assertRaises(NeverTouchViolation):
                    check_gate_pass_at_sha(
                        "rupret007/rad-dad-show-night", "a" * 40, receipt, "merge",
                    )

    def test_invalid_repositories_raise_policy_violations(self):
        for repo in (None, "", "not-a-repo", [], {}):
            with self.subTest(repo=repo):
                with self.assertRaises(NeverTouchViolation):
                    check_gate_pass_at_sha(repo, "a" * 40, None, "merge")
                receipt = GatePassReceipt(
                    repo=repo, sha="a" * 40,
                    reviewer="karen", expires_at=time.time() + 3600,
                )
                with self.assertRaises(NeverTouchViolation):
                    check_gate_pass_at_sha(
                        "rupret007/rad-dad-show-night", receipt.sha, receipt, "merge",
                    )

    def test_invalid_expiry_cannot_authorize_a_pass(self):
        for expiry in (float("nan"), float("inf"), -float("inf"),
                       None, "later", True, False, 10 ** 1000):
            with self.subTest(expiry=expiry):
                receipt = GatePassReceipt(
                    repo="rupret007/rad-dad-show-night", sha="a" * 40,
                    reviewer="karen", expires_at=expiry,
                )
                with self.assertRaises(NeverTouchViolation):
                    check_gate_pass_at_sha(receipt.repo, receipt.sha, receipt, "merge")

    def test_missing_reviewer_cannot_authorize_a_pass(self):
        for reviewer in (None, "", " \t\n", False, 1):
            with self.subTest(reviewer=reviewer):
                receipt = GatePassReceipt(
                    repo="rupret007/rad-dad-show-night", sha="a" * 40,
                    reviewer=reviewer, expires_at=time.time() + 3600,
                )
                with self.assertRaises(NeverTouchViolation):
                    check_gate_pass_at_sha(receipt.repo, receipt.sha, receipt, "merge")

    def test_expiry_boundary_and_normalized_repositories(self):
        now = 1_000_000.0
        for expiry, expired in ((now - 1, True), (now, True), (now + 1, False)):
            with self.subTest(expiry=expiry), patch("time.time", return_value=now):
                receipt = GatePassReceipt(
                    repo="git@github.com:RUPRET007/RAD-DAD-SHOW-NIGHT.git",
                    sha="a" * 40, reviewer="karen", expires_at=expiry,
                )
                if expired:
                    with self.assertRaisesRegex(NeverTouchViolation, "expired"):
                        check_gate_pass_at_sha(
                            "rupret007/rad-dad-show-night", receipt.sha, receipt, "merge",
                        )
                else:
                    check_gate_pass_at_sha(
                        "rupret007/rad-dad-show-night", receipt.sha, receipt, "merge",
                    )


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


ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "cluster" / "never_touch" / "__init__.py"
PROTECTED_REPO = "rupret007/rad-dad-show-night"


class CloneUrlGuardTests(unittest.TestCase):
    def test_clone_urls_cannot_bypass_any_write_guard(self):
        for repo in (
            PROTECTED_REPO + ".git",
            f"https://github.com/{PROTECTED_REPO}.git",
            f"https://github.com/{PROTECTED_REPO.upper()}.GIT/",
            f"git@github.com:{PROTECTED_REPO}.git",
            f"ssh://git@github.com/{PROTECTED_REPO}.git",
        ):
            with self.subTest(repo=repo):
                self.assertTrue(is_never_touch(repo))
                for guard, args in (
                    (guard_push, (repo, "fixture", "a" * 40)),
                    (guard_pr_create, (repo, "fixture", "main")),
                    (guard_pr_merge, (repo, 1, "a" * 40)),
                    (check_gate_pass_at_sha, (repo, "a" * 40, None, "merge")),
                ):
                    with self.assertRaises(NeverTouchViolation):
                        guard(*args)


class RemotePushGuardTests(unittest.TestCase):
    """Exercise real Git configuration resolution, with no network or commits."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cwd = Path(tmp.name)
        self.env = {"PATH": os.defpath, "HOME": tmp.name,
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        self.git("init", "--quiet")
        self.git("remote", "add", "origin", "https://github.com/rupret007/bob-ops-dashboard.git")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.cwd, env=self.env,
                              capture_output=True, text=True, check=True)

    def check_guard(self):
        return subprocess.run([sys.executable, str(GUARD), "push", "--remote", "origin"],
                              cwd=self.cwd, env=self.env, capture_output=True, text=True)

    def test_allowed_remote_passes(self):
        result = self.check_guard()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_protected_pushurl_blocks_even_with_allowed_fetch_url(self):
        self.git("remote", "set-url", "--push", "origin", f"https://github.com/{PROTECTED_REPO}.git")
        result = self.check_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BLOCKED", result.stderr)

    def test_all_pushurls_are_checked(self):
        self.git("remote", "set-url", "--add", "--push", "origin", "https://github.com/owner/allowed.git")
        self.git("remote", "set-url", "--add", "--push", "origin", f"git@github.com:{PROTECTED_REPO}.git")
        self.assertNotEqual(self.check_guard().returncode, 0)

    def test_push_instead_of_rewrite_is_checked(self):
        self.git("config", f"url.https://github.com/{PROTECTED_REPO}.git.pushInsteadOf",
                 "https://github.com/rupret007/bob-ops-dashboard.git")
        self.assertNotEqual(self.check_guard().returncode, 0)

    def test_missing_and_unrecognized_destinations_fail_closed(self):
        self.git("remote", "remove", "origin")
        self.assertNotEqual(self.check_guard().returncode, 0)
        self.git("remote", "add", "origin", "/unrecognized/local/path")
        self.assertNotEqual(self.check_guard().returncode, 0)


class ProductionWritePathTests(unittest.TestCase):
    def test_decision_issue_guard_runs_before_github_write(self):
        from cluster import never_touch
        from cluster.never_touch._dashboard import close_decision_issue

        refresh = (ROOT / "refresh.sh").read_text()
        start = refresh.index("    if apply_decisions:\n")
        end = refresh.index("\nstanding =", start)
        self.assertIn("from cluster.never_touch._dashboard import close_decision_issue", refresh[start:end])
        self.assertIn('close_decision_issue(iss["number"], decision)', refresh[start:end])
        with patch("cluster.never_touch._dashboard.subprocess.check_call") as close:
            with patch.object(never_touch, "NEVER_TOUCH_REPOS", frozenset({"rupret007/bob-ops-dashboard"})):
                with self.assertRaises(NeverTouchViolation):
                    close_decision_issue(1, "hold")
            close.assert_not_called()
            close_decision_issue(1, "hold")
            close.assert_called_once_with(
                ["gh", "issue", "close", "1", "-R", "rupret007/bob-ops-dashboard",
                 "--comment", "Recorded as hold for Bob."],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

    def test_mesh_jobs_are_blocked_before_checkout_or_runner_launch(self):
        from cluster.mesh_worker import Executor

        for repo in (PROTECTED_REPO, f"https://github.com/{PROTECTED_REPO}.git", None):
            for dry_run in (False, True):
                with self.subTest(repo=repo, dry_run=dry_run), \
                        patch("cluster.job_exec.prepare_worktree") as checkout, \
                        patch.object(Executor, "_proc") as run:
                    with self.assertRaises(NeverTouchViolation):
                        Executor("member", dry_run).run({
                            "v": 2, "id": "fixture", "repo": repo, "ref": "main",
                            "cap": "llm.codex", "prompt": "Run the fixture checks",
                            "timeout_s": 60, "load_ceiling": 4,
                        })
                    checkout.assert_not_called()
                    run.assert_not_called()

    def test_job_execution_guard_remains_effective_if_allowlist_expands(self):
        from cluster import job_exec
        from cluster.tests.test_security import valid_spec

        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run), \
                    patch("cluster.spec.ALLOWED_REPOS", {PROTECTED_REPO}), \
                    patch.object(job_exec, "job_workdir") as workdir, \
                    patch.object(job_exec.subprocess, "run") as run:
                spec = valid_spec(repo=PROTECTED_REPO)
                with self.assertRaises(NeverTouchViolation):
                    job_exec.prepare_worktree(spec, dry_run)
                with self.assertRaises(NeverTouchViolation):
                    job_exec.run_cmd(spec, Path("/unused"), dry_run)
                workdir.assert_not_called()
                run.assert_not_called()

    def test_mesh_guard_covers_all_capabilities_if_allowlist_expands(self):
        from cluster.capabilities import CAPABILITIES, GUEST_CAPS
        from cluster.mesh_worker import Executor
        from cluster.routing import validate_cap_spec

        for cap in CAPABILITIES:
            for dry_run in (False, True):
                with self.subTest(cap=cap, dry_run=dry_run), \
                        patch("cluster.routing.ALLOWED_REPOS", {PROTECTED_REPO}), \
                        patch("cluster.job_exec.prepare_worktree") as checkout, \
                        patch.object(Executor, "_proc") as run:
                    spec = {
                        "v": 2, "id": "fixture", "repo": PROTECTED_REPO, "ref": "main",
                        "cap": cap, "timeout_s": 60, "load_ceiling": 4,
                    }
                    if cap.startswith("llm."):
                        spec["prompt"] = "Run the fixture checks"
                    else:
                        spec["cmd"] = ["npm", "run", "build"] if cap == "shell.build" else ["npm", "test"]
                    guest = cap in GUEST_CAPS
                    spec = validate_cap_spec(spec, guest=guest)
                    with self.assertRaises(NeverTouchViolation):
                        Executor("guest" if guest else "member", dry_run).run(spec)
                    checkout.assert_not_called()
                    run.assert_not_called()

    def test_dispatch_blocks_before_calling_github(self):
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            calls = scratch / "calls"
            for cli in ("gh", "jq"):
                stub = scratch / cli
                stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\nprintf "{}\\n"\n')
                stub.chmod(0o755)
            env = {"PATH": str(scratch) + os.pathsep + os.defpath,
                   "HOME": tmp, "CALL_LOG": str(calls),
                   "TARGET_OWNER": "rupret007", "TARGET_REPO": "rad-dad-show-night"}
            script = ["bash", str(ROOT / "scripts" / "dispatch-board-refresh.sh")]
            blocked = subprocess.run(script, env=env, cwd=scratch, capture_output=True, text=True)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("BLOCKED", blocked.stderr)
            self.assertFalse(calls.exists())
            env["TARGET_REPO"] = "bob-ops-dashboard"
            allowed = subprocess.run(script, env=env, cwd=scratch, capture_output=True, text=True)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertIn("/repos/rupret007/bob-ops-dashboard/dispatches", calls.read_text())

    def test_both_production_push_loops_stop_before_protected_push(self):
        workflow = (ROOT / ".github/workflows/refresh-dashboard.yml").read_text()
        workflow_body = textwrap.dedent(workflow.split(
            "      - name: Commit and push if changed\n        run: |\n", 1)[1])
        refresh = (ROOT / "refresh.sh").read_text()
        start = refresh.index("    pushed=0\n")
        end = refresh.index("    # K2 post-push dual-SoT", start)
        refresh_body = textwrap.dedent(refresh[start:end])
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            (scratch / "cluster").symlink_to(ROOT / "cluster", target_is_directory=True)
            fake_git = scratch / "git"
            fake_git.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n'
                'case "$1 $2" in\n'
                '  "remote get-url") printf "%s\\n" "$PUSH_URL";;\n'
                '  "diff --cached") exit 1;;\n'
                'esac\n')
            fake_git.chmod(0o755)
            env = {"PATH": str(scratch) + os.pathsep + os.defpath,
                   "HOME": tmp, "ROOT": str(ROOT)}
            for name, body in (("workflow", workflow_body), ("refresh", refresh_body)):
                for destination in (f"https://github.com/{PROTECTED_REPO}.git", "malformed"):
                    with self.subTest(path=name, destination=destination):
                        calls = scratch / "calls"
                        calls.unlink(missing_ok=True)
                        env.update(CALL_LOG=str(calls), PUSH_URL=destination)
                        result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + body],
                                                cwd=scratch, env=env, capture_output=True, text=True)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("BLOCKED", result.stderr)
                        self.assertFalse(any(line.startswith("push ") for line in calls.read_text().splitlines()))

    def test_refresh_push_carries_guard_in_pages_excluded_files(self):
        body = (ROOT / "refresh.sh").read_text().split('if [[ $PUSH -eq 1 ]]; then\n', 1)[1]
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            source = scratch / "source"
            (source / "cluster/never_touch").mkdir(parents=True)
            for name in ("index.html", "status.json", "README.md", "refresh.sh"):
                (source / name).write_text("synthetic fixture\n")
            for name in ("__init__.py", "__main__.py", "_dashboard.py"):
                (source / "cluster/never_touch" / name).write_text((GUARD.parent / name).read_text())
            scripts = {
                "gh": 'mkdir -p "$4"\n',
                "python3": 'if [ "$1" = "-c" ]; then exit 0; fi\nexec "$PYTHON" "$@"\n',
                "git": (
                    'printf "%s\\n" "$*" >> "$CALL_LOG"\n'
                    'case "$1 $2" in\n'
                    '  "diff --cached") exit 1;;\n'
                    '  "remote get-url") printf "https://github.com/owner/allowed.git\\n";;\n'
                    '  "push origin")\n'
                    '    for name in __init__.py __main__.py _dashboard.py; do\n'
                    '      cmp "cluster/never_touch/$name" "$ROOT/cluster/never_touch/$name" || exit 90\n'
                    '    done\n'
                    '    test ! -e _config.yml || exit 91\n'
                    '    : > "$RECEIPT";;\n'
                    'esac\n'
                ),
            }
            for name, script in scripts.items():
                path = scratch / name
                path.write_text("#!/bin/sh\n" + script)
                path.chmod(0o755)
            calls, receipt = scratch / "calls", scratch / "receipt"
            env = {"PATH": str(scratch) + os.pathsep + os.defpath, "HOME": tmp,
                   "ROOT": str(source), "OWNER": "fixture-owner", "TMPDIR": tmp,
                   "CALL_LOG": str(calls), "RECEIPT": str(receipt), "PYTHON": sys.executable}
            result = subprocess.run(["bash", "-c", 'set -euo pipefail\nif true; then\n' + body],
                                    cwd=scratch, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(receipt.exists(), "the complete Pages-excluded guard package must reach the publisher")
            self.assertIn("add cluster/never_touch/__init__.py cluster/never_touch/__main__.py cluster/never_touch/_dashboard.py", calls.read_text())


if __name__ == "__main__":
    unittest.main()
