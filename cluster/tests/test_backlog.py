"""Tests for backlog discovery and reporting."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, Mock

from cluster.control_plane.backlog import (
    EXCLUDED_REPOS,
    MAX_UNDRAFTS_PER_TICK,
    discover_repos,
    get_open_prs,
    get_pr_state,
    is_ci_all_green,
    is_never_touch_repo,
    collect_backlog,
    format_backlog_md,
    write_backlog_report,
    label_queued_prs,
    undraft_green_prs,
    ensure_label_exists,
    BACKLOG_PATH,
)


class TestExcludedRepos(unittest.TestCase):
    """Test excluded repos list."""

    def test_rad_dad_excluded(self):
        """rad-dad-show-night is in excluded repos."""
        self.assertIn("rupret007/rad-dad-show-night", EXCLUDED_REPOS)

    def test_hang_out_excluded(self):
        """Hang Out is in excluded repos (both cases)."""
        lower_match = any("hang-out" in r.lower() for r in EXCLUDED_REPOS)
        self.assertTrue(lower_match, "hang-out should be in EXCLUDED_REPOS")

    def test_excluded_is_frozen(self):
        """EXCLUDED_REPOS should be immutable."""
        self.assertIsInstance(EXCLUDED_REPOS, frozenset)


class TestIsCiAllGreen(unittest.TestCase):
    """Test CI status detection."""

    def test_all_success(self):
        """All passing checks should return True."""
        pr = {"statusCheckRollup": [
            {"conclusion": "success"},
            {"conclusion": "success"},
        ]}
        self.assertTrue(is_ci_all_green(pr))

    def test_bucket_pass(self):
        """bucket=pass should count as green."""
        pr = {"statusCheckRollup": [{"bucket": "pass"}]}
        self.assertTrue(is_ci_all_green(pr))

    def test_neutral_is_green(self):
        """neutral state should count as green."""
        pr = {"statusCheckRollup": [{"state": "neutral"}]}
        self.assertTrue(is_ci_all_green(pr))

    def test_any_failure(self):
        """Any failure should return False."""
        pr = {"statusCheckRollup": [
            {"conclusion": "success"},
            {"conclusion": "failure"},
        ]}
        self.assertFalse(is_ci_all_green(pr))

    def test_any_pending(self):
        """Any pending should return False."""
        pr = {"statusCheckRollup": [
            {"conclusion": "success"},
            {"conclusion": "pending"},
        ]}
        self.assertFalse(is_ci_all_green(pr))

    def test_no_checks(self):
        """No checks should return False."""
        pr = {"statusCheckRollup": []}
        self.assertFalse(is_ci_all_green(pr))

    def test_none_checks(self):
        """None checks should return False."""
        pr = {"statusCheckRollup": None}
        self.assertFalse(is_ci_all_green(pr))


class TestIsNeverTouchRepo(unittest.TestCase):
    """Test never-touch repo detection."""

    def test_rad_dad(self):
        """rad-dad-show-night is never-touch."""
        self.assertTrue(is_never_touch_repo("rupret007/rad-dad-show-night"))

    def test_hang_out(self):
        """Hang Out is never-touch (case insensitive)."""
        self.assertTrue(is_never_touch_repo("rupret007/Hang-Out"))
        self.assertTrue(is_never_touch_repo("rupret007/hang-out"))

    def test_normal_repo(self):
        """Normal repos are not never-touch."""
        self.assertFalse(is_never_touch_repo("rupret007/webjam"))
        self.assertFalse(is_never_touch_repo("rupret007/bob-ops-dashboard"))


class TestGetPrState(unittest.TestCase):
    """Test PR state detection."""

    def test_draft_pr_no_ci(self):
        """Draft PRs with no CI should be marked as draft."""
        pr = {"isDraft": True, "labels": [], "statusCheckRollup": []}
        self.assertEqual(get_pr_state(pr), "draft")

    def test_draft_pr_green_ci(self):
        """Draft PRs with green CI should be marked as draft_green."""
        pr = {"isDraft": True, "labels": [], "statusCheckRollup": [{"conclusion": "success"}]}
        self.assertEqual(get_pr_state(pr), "draft_green")

    def test_draft_pr_failing_ci(self):
        """Draft PRs with failing CI should be marked as draft."""
        pr = {"isDraft": True, "labels": [], "statusCheckRollup": [{"conclusion": "failure"}]}
        self.assertEqual(get_pr_state(pr), "draft")

    def test_karen_pass_label(self):
        """PRs with karen-pass label should be marked as passed."""
        pr = {"isDraft": False, "labels": [{"name": "karen-pass"}], "statusCheckRollup": []}
        self.assertEqual(get_pr_state(pr), "passed")

    def test_karen_fail_label(self):
        """PRs with karen-fail label should be marked as failed."""
        pr = {"isDraft": False, "labels": [{"name": "karen-fail"}], "statusCheckRollup": []}
        self.assertEqual(get_pr_state(pr), "failed")

    def test_needs_karen_label(self):
        """PRs with needs-karen label should be under_review."""
        pr = {"isDraft": False, "labels": [{"name": "needs-karen"}], "statusCheckRollup": []}
        self.assertEqual(get_pr_state(pr), "under_review")

    def test_ci_failing(self):
        """PRs with failing CI should be marked as ci_failing."""
        pr = {
            "isDraft": False,
            "labels": [],
            "statusCheckRollup": [{"conclusion": "failure"}],
        }
        self.assertEqual(get_pr_state(pr), "ci_failing")

    def test_ci_pending(self):
        """PRs with pending CI should be marked as ci_pending."""
        pr = {
            "isDraft": False,
            "labels": [],
            "statusCheckRollup": [{"conclusion": "pending"}],
        }
        self.assertEqual(get_pr_state(pr), "ci_pending")

    def test_queued_pr(self):
        """PRs ready for review should be marked as queued."""
        pr = {
            "isDraft": False,
            "labels": [],
            "statusCheckRollup": [{"conclusion": "success"}],
        }
        self.assertEqual(get_pr_state(pr), "queued")

    def test_empty_checks_is_queued(self):
        """PRs with no CI checks are queued."""
        pr = {"isDraft": False, "labels": [], "statusCheckRollup": []}
        self.assertEqual(get_pr_state(pr), "queued")


class TestFormatBacklogMd(unittest.TestCase):
    """Test markdown formatting."""

    def test_basic_format(self):
        """Backlog should format as valid markdown."""
        backlog = {
            "timestamp": "2026-10-17T00:00:00Z",
            "repos": [],
            "summary": {
                "total_repos": 0,
                "total_prs": 0,
                "queued": 0,
            },
        }
        md = format_backlog_md(backlog)
        self.assertIn("# StoryPool Backlog Report", md)
        self.assertIn("2026-10-17T00:00:00Z", md)
        self.assertIn("| Total PRs | 0 |", md)

    def test_format_with_prs(self):
        """Backlog with PRs should include them."""
        backlog = {
            "timestamp": "2026-10-17T00:00:00Z",
            "repos": [{
                "repo": "rupret007/webjam",
                "prs": [{
                    "number": 42,
                    "title": "Fix bug",
                    "author": "jeff",
                    "sha": "abc123",
                    "state": "queued",
                    "url": "https://github.com/rupret007/webjam/pull/42",
                }],
            }],
            "summary": {
                "total_repos": 1,
                "total_prs": 1,
                "queued": 1,
            },
        }
        md = format_backlog_md(backlog)
        self.assertIn("rupret007/webjam", md)
        self.assertIn("#42", md)
        self.assertIn("Fix bug", md)


class TestWriteBacklogReport(unittest.TestCase):
    """Test backlog file writing."""

    def test_writes_to_local_path(self):
        """Backlog should be written to local path."""
        backlog = {
            "timestamp": "2026-10-17T00:00:00Z",
            "repos": [],
            "summary": {"total_repos": 0, "total_prs": 0},
        }
        
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / "backlog.md"
            with patch("cluster.control_plane.backlog.BACKLOG_PATH", local_path):
                write_backlog_report(backlog)
            
            self.assertTrue(local_path.exists())
            content = local_path.read_text()
            self.assertIn("StoryPool Backlog Report", content)

    def test_writes_to_repo_docs(self):
        """Backlog should also be written to repo docs if repo_root provided."""
        backlog = {
            "timestamp": "2026-10-17T00:00:00Z",
            "repos": [],
            "summary": {"total_repos": 0, "total_prs": 0},
        }
        
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            local_path = repo_root / "local_backlog.md"
            
            with patch("cluster.control_plane.backlog.BACKLOG_PATH", local_path):
                write_backlog_report(backlog, repo_root=repo_root)
            
            docs_path = repo_root / "docs" / "storypool-backlog.md"
            self.assertTrue(docs_path.exists())


class TestDiscoverRepos(unittest.TestCase):
    """Test repo discovery."""

    @patch("cluster.control_plane.backlog._run_gh")
    def test_excludes_blocked_repos(self, mock_gh):
        """discover_repos should exclude blocked repos."""
        mock_gh.return_value = [
            {"nameWithOwner": "rupret007/webjam"},
            {"nameWithOwner": "rupret007/rad-dad-show-night"},
            {"nameWithOwner": "rupret007/bob-ops-dashboard"},
            {"nameWithOwner": "rupret007/Hang-Out"},
        ]
        
        repos = discover_repos("rupret007")
        
        self.assertIn("rupret007/webjam", repos)
        self.assertIn("rupret007/bob-ops-dashboard", repos)
        self.assertNotIn("rupret007/rad-dad-show-night", repos)
        self.assertNotIn("rupret007/Hang-Out", repos)

    @patch("cluster.control_plane.backlog._run_gh")
    def test_handles_empty_response(self, mock_gh):
        """discover_repos should handle empty response."""
        mock_gh.return_value = []
        repos = discover_repos("rupret007")
        self.assertEqual(repos, [])

    @patch("cluster.control_plane.backlog._run_gh")
    def test_handles_none_response(self, mock_gh):
        """discover_repos should handle None response."""
        mock_gh.return_value = None
        repos = discover_repos("rupret007")
        self.assertEqual(repos, [])


class TestLabelQueuedPrs(unittest.TestCase):
    """Test PR labeling."""

    @patch("cluster.control_plane.backlog.get_open_prs")
    @patch("cluster.control_plane.backlog.discover_repos")
    def test_dry_run_does_not_label(self, mock_discover, mock_prs):
        """Dry run should not actually label PRs."""
        mock_discover.return_value = ["rupret007/webjam"]
        mock_prs.return_value = [{
            "number": 42,
            "title": "Test PR",
            "headRefOid": "abc123",
            "isDraft": False,
            "labels": [],
            "statusCheckRollup": [{"conclusion": "success"}],
        }]
        
        with patch("subprocess.run") as mock_run:
            labeled = label_queued_prs(dry_run=True)
        
        mock_run.assert_not_called()
        self.assertEqual(len(labeled), 1)
        self.assertEqual(labeled[0]["pr"], 42)

    @patch("cluster.control_plane.backlog.get_open_prs")
    @patch("cluster.control_plane.backlog.discover_repos")
    def test_skips_non_queued_prs(self, mock_discover, mock_prs):
        """Should skip PRs that are not queued."""
        mock_discover.return_value = ["rupret007/webjam"]
        mock_prs.return_value = [
            {
                "number": 1,
                "title": "Draft PR",
                "headRefOid": "abc123",
                "isDraft": True,
                "labels": [],
                "statusCheckRollup": [],
            },
            {
                "number": 2,
                "title": "Already labeled",
                "headRefOid": "def456",
                "isDraft": False,
                "labels": [{"name": "needs-karen"}],
                "statusCheckRollup": [],
            },
        ]
        
        labeled = label_queued_prs(dry_run=True)
        self.assertEqual(len(labeled), 0)


class TestCollectBacklog(unittest.TestCase):
    """Test backlog collection."""

    @patch("cluster.control_plane.backlog.get_open_prs")
    @patch("cluster.control_plane.backlog.discover_repos")
    def test_collects_summary(self, mock_discover, mock_prs):
        """collect_backlog should compute summary correctly."""
        mock_discover.return_value = ["rupret007/webjam"]
        mock_prs.return_value = [
            {
                "number": 1,
                "title": "Queued PR",
                "headRefOid": "abc",
                "isDraft": False,
                "labels": [],
                "statusCheckRollup": [{"conclusion": "success"}],
                "author": {"login": "jeff"},
                "url": "https://github.com/rupret007/webjam/pull/1",
            },
            {
                "number": 2,
                "title": "Draft PR",
                "headRefOid": "def",
                "isDraft": True,
                "labels": [],
                "statusCheckRollup": [],
                "author": {"login": "jeff"},
                "url": "https://github.com/rupret007/webjam/pull/2",
            },
        ]
        
        backlog = collect_backlog()
        
        self.assertEqual(backlog["summary"]["total_repos"], 1)
        self.assertEqual(backlog["summary"]["total_prs"], 2)
        self.assertEqual(backlog["summary"]["queued"], 1)
        self.assertEqual(backlog["summary"]["draft"], 1)


class TestEnsureLabelExists(unittest.TestCase):
    """Test label creation."""

    @patch("subprocess.run")
    def test_creates_label_if_missing(self, mock_run):
        """Should create label if it doesn't exist."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="[]"),
            MagicMock(returncode=0, stdout=""),
        ]
        
        result = ensure_label_exists("rupret007/webjam", "needs-karen")
        
        self.assertTrue(result)
        self.assertEqual(mock_run.call_count, 2)

    @patch("subprocess.run")
    def test_skips_if_label_exists(self, mock_run):
        """Should skip creation if label already exists."""
        mock_run.return_value = MagicMock(
            returncode=0, 
            stdout='[{"name": "needs-karen"}]'
        )
        
        result = ensure_label_exists("rupret007/webjam", "needs-karen")
        
        self.assertFalse(result)
        self.assertEqual(mock_run.call_count, 1)


class TestUndraftGreenPrs(unittest.TestCase):
    """Test auto-undrafting of green draft PRs."""

    def test_max_undrafts_constant(self):
        """MAX_UNDRAFTS_PER_TICK should be 5."""
        self.assertEqual(MAX_UNDRAFTS_PER_TICK, 5)

    @patch("cluster.control_plane.backlog.get_open_prs")
    @patch("cluster.control_plane.backlog.discover_repos")
    def test_dry_run_does_not_undraft(self, mock_discover, mock_prs):
        """Dry run should not actually undraft PRs."""
        mock_discover.return_value = ["rupret007/webjam"]
        mock_prs.return_value = [{
            "number": 42,
            "title": "Green Draft",
            "headRefOid": "abc123",
            "isDraft": True,
            "labels": [],
            "statusCheckRollup": [{"conclusion": "success"}],
            "author": {"login": "jeff"},
            "url": "https://github.com/rupret007/webjam/pull/42",
        }]
        
        with patch("subprocess.run") as mock_run:
            undrafted = undraft_green_prs(dry_run=True)
        
        mock_run.assert_not_called()
        self.assertEqual(len(undrafted), 1)
        self.assertEqual(undrafted[0]["pr"], 42)

    @patch("cluster.control_plane.backlog.get_open_prs")
    @patch("cluster.control_plane.backlog.discover_repos")
    def test_skips_never_touch_repos(self, mock_discover, mock_prs):
        """Should skip never-touch repos."""
        mock_discover.return_value = ["rupret007/rad-dad-show-night"]
        mock_prs.return_value = [{
            "number": 1,
            "title": "Green Draft",
            "headRefOid": "abc123",
            "isDraft": True,
            "labels": [],
            "statusCheckRollup": [{"conclusion": "success"}],
        }]
        
        undrafted = undraft_green_prs(dry_run=True)
        
        self.assertEqual(len(undrafted), 0)

    @patch("cluster.control_plane.backlog.get_open_prs")
    @patch("cluster.control_plane.backlog.discover_repos")
    def test_skips_non_draft_prs(self, mock_discover, mock_prs):
        """Should skip non-draft PRs."""
        mock_discover.return_value = ["rupret007/webjam"]
        mock_prs.return_value = [{
            "number": 1,
            "title": "Not a draft",
            "headRefOid": "abc123",
            "isDraft": False,
            "labels": [],
            "statusCheckRollup": [{"conclusion": "success"}],
        }]
        
        undrafted = undraft_green_prs(dry_run=True)
        
        self.assertEqual(len(undrafted), 0)

    @patch("cluster.control_plane.backlog.get_open_prs")
    @patch("cluster.control_plane.backlog.discover_repos")
    def test_skips_non_green_drafts(self, mock_discover, mock_prs):
        """Should skip draft PRs with failing/pending CI."""
        mock_discover.return_value = ["rupret007/webjam"]
        mock_prs.return_value = [
            {
                "number": 1,
                "title": "Failing CI",
                "headRefOid": "abc123",
                "isDraft": True,
                "labels": [],
                "statusCheckRollup": [{"conclusion": "failure"}],
            },
            {
                "number": 2,
                "title": "Pending CI",
                "headRefOid": "def456",
                "isDraft": True,
                "labels": [],
                "statusCheckRollup": [{"conclusion": "pending"}],
            },
            {
                "number": 3,
                "title": "No CI",
                "headRefOid": "ghi789",
                "isDraft": True,
                "labels": [],
                "statusCheckRollup": [],
            },
        ]
        
        undrafted = undraft_green_prs(dry_run=True)
        
        self.assertEqual(len(undrafted), 0)

    @patch("cluster.control_plane.backlog.get_open_prs")
    @patch("cluster.control_plane.backlog.discover_repos")
    def test_respects_max_undrafts(self, mock_discover, mock_prs):
        """Should stop at max_undrafts."""
        mock_discover.return_value = ["rupret007/webjam"]
        mock_prs.return_value = [
            {
                "number": i,
                "title": "Green Draft {}".format(i),
                "headRefOid": "sha{}".format(i),
                "isDraft": True,
                "labels": [],
                "statusCheckRollup": [{"conclusion": "success"}],
                "author": {"login": "jeff"},
                "url": "https://github.com/rupret007/webjam/pull/{}".format(i),
            }
            for i in range(10)
        ]
        
        undrafted = undraft_green_prs(dry_run=True, max_undrafts=3)
        
        self.assertEqual(len(undrafted), 3)

    @patch("cluster.control_plane.backlog.ensure_label_exists")
    @patch("subprocess.run")
    @patch("cluster.control_plane.backlog.get_open_prs")
    @patch("cluster.control_plane.backlog.discover_repos")
    def test_live_undraft_and_label(self, mock_discover, mock_prs, mock_run, mock_ensure):
        """Live mode should undraft and add needs-karen label."""
        mock_discover.return_value = ["rupret007/webjam"]
        mock_prs.return_value = [{
            "number": 42,
            "title": "Green Draft",
            "headRefOid": "abc123",
            "isDraft": True,
            "labels": [],
            "statusCheckRollup": [{"conclusion": "success"}],
            "author": {"login": "jeff"},
            "url": "https://github.com/rupret007/webjam/pull/42",
        }]
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_ensure.return_value = False
        
        undrafted = undraft_green_prs(dry_run=False)
        
        self.assertEqual(len(undrafted), 1)
        self.assertEqual(mock_run.call_count, 2)
        
        ready_call = mock_run.call_args_list[0]
        self.assertIn("ready", ready_call[0][0])
        
        label_call = mock_run.call_args_list[1]
        self.assertIn("--add-label", label_call[0][0])


class TestFormatBacklogMdWithUndrafted(unittest.TestCase):
    """Test markdown formatting with undrafted PRs."""

    def test_includes_undrafted_section(self):
        """Backlog with undrafted PRs should include section."""
        backlog = {
            "timestamp": "2026-10-17T00:00:00Z",
            "repos": [],
            "undrafted_this_tick": [{
                "repo": "rupret007/webjam",
                "pr": 42,
                "title": "Green Draft",
                "author": "jeff",
                "sha": "abc123",
                "url": "https://github.com/rupret007/webjam/pull/42",
            }],
            "summary": {
                "total_repos": 0,
                "total_prs": 0,
                "draft_green": 1,
            },
        }
        md = format_backlog_md(backlog)
        self.assertIn("Undrafted This Tick", md)
        self.assertIn("#42", md)
        self.assertIn("Green Draft", md)

    def test_includes_draft_green_count(self):
        """Summary should include draft_green count."""
        backlog = {
            "timestamp": "2026-10-17T00:00:00Z",
            "repos": [],
            "undrafted_this_tick": [],
            "summary": {
                "total_repos": 0,
                "total_prs": 0,
                "draft_green": 3,
                "draft": 2,
            },
        }
        md = format_backlog_md(backlog)
        self.assertIn("Draft (CI green) | 3", md)
        self.assertIn("Draft (other) | 2", md)


if __name__ == "__main__":
    unittest.main()
