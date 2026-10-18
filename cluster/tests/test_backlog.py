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
    discover_repos,
    get_open_prs,
    get_pr_state,
    collect_backlog,
    format_backlog_md,
    write_backlog_report,
    label_queued_prs,
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


class TestGetPrState(unittest.TestCase):
    """Test PR state detection."""

    def test_draft_pr(self):
        """Draft PRs should be marked as draft."""
        pr = {"isDraft": True, "labels": [], "statusCheckRollup": []}
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


if __name__ == "__main__":
    unittest.main()
