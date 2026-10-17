"""Tests for self-update logic."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from cluster.control_plane.self_update import (
    BRANCH,
    get_current_head,
    is_working_tree_dirty,
    fetch_branch,
    can_fast_forward,
    fast_forward,
    run_tests,
    reinstall,
    self_update,
)


class TestBranchConstant(unittest.TestCase):
    """Test branch constant."""

    def test_branch_is_all_in_one(self):
        """Branch should be the storypool-all-in-one branch."""
        self.assertIn("storypool-all-in-one", BRANCH)
        self.assertIn("47c7", BRANCH)


class TestGetCurrentHead(unittest.TestCase):
    """Test HEAD sha retrieval."""

    def test_gets_head_in_valid_repo(self):
        """Should get HEAD in a valid git repo."""
        repo_root = Path(__file__).parent.parent.parent
        head = get_current_head(repo_root)
        self.assertIsNotNone(head)
        self.assertEqual(len(head), 40)

    def test_returns_none_for_nonexistent(self):
        """Should return None for nonexistent directory."""
        head = get_current_head(Path("/nonexistent/path"))
        self.assertIsNone(head)


class TestIsWorkingTreeDirty(unittest.TestCase):
    """Test dirty working tree detection."""

    def test_clean_repo_not_dirty(self):
        """Clean repo should not be detected as dirty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=tmppath, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmppath, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmppath, capture_output=True)
            (tmppath / "file.txt").write_text("content")
            subprocess.run(["git", "add", "."], cwd=tmppath, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmppath, capture_output=True)
            
            self.assertFalse(is_working_tree_dirty(tmppath))

    def test_dirty_repo_detected(self):
        """Dirty repo should be detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=tmppath, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmppath, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmppath, capture_output=True)
            (tmppath / "file.txt").write_text("content")
            subprocess.run(["git", "add", "."], cwd=tmppath, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmppath, capture_output=True)
            
            (tmppath / "file.txt").write_text("modified")
            
            self.assertTrue(is_working_tree_dirty(tmppath))


class TestSelfUpdate(unittest.TestCase):
    """Test self-update function."""

    def test_nonexistent_dir_fails(self):
        """Should fail gracefully for nonexistent directory."""
        result = self_update(install_dir=Path("/nonexistent/path"))
        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "repo_dir does not exist")

    @patch("cluster.control_plane.self_update.get_current_head")
    def test_no_head_fails(self, mock_head):
        """Should fail if HEAD cannot be retrieved."""
        mock_head.return_value = None
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self_update(install_dir=Path(tmpdir))
        
        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "could not get current HEAD")

    @patch("cluster.control_plane.self_update.is_working_tree_dirty")
    @patch("cluster.control_plane.self_update.get_current_head")
    def test_dirty_tree_fails(self, mock_head, mock_dirty):
        """Should fail if working tree is dirty."""
        mock_head.return_value = "a" * 40
        mock_dirty.return_value = True
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self_update(install_dir=Path(tmpdir))
        
        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "working tree is dirty")

    @patch("cluster.control_plane.self_update.fetch_branch")
    @patch("cluster.control_plane.self_update.is_working_tree_dirty")
    @patch("cluster.control_plane.self_update.get_current_head")
    def test_fetch_failure_fails(self, mock_head, mock_dirty, mock_fetch):
        """Should fail if fetch fails."""
        mock_head.return_value = "a" * 40
        mock_dirty.return_value = False
        mock_fetch.return_value = False
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self_update(install_dir=Path(tmpdir))
        
        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "fetch failed")

    @patch("cluster.control_plane.self_update.can_fast_forward")
    @patch("cluster.control_plane.self_update.fetch_branch")
    @patch("cluster.control_plane.self_update.is_working_tree_dirty")
    @patch("cluster.control_plane.self_update.get_current_head")
    def test_already_up_to_date(self, mock_head, mock_dirty, mock_fetch, mock_ff):
        """Should report already up-to-date."""
        sha = "a" * 40
        mock_head.return_value = sha
        mock_dirty.return_value = False
        mock_fetch.return_value = True
        mock_ff.return_value = (True, sha)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self_update(install_dir=Path(tmpdir))
        
        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "already up-to-date")

    @patch("cluster.control_plane.self_update.run_tests")
    @patch("cluster.control_plane.self_update.fast_forward")
    @patch("cluster.control_plane.self_update.can_fast_forward")
    @patch("cluster.control_plane.self_update.fetch_branch")
    @patch("cluster.control_plane.self_update.is_working_tree_dirty")
    @patch("cluster.control_plane.self_update.get_current_head")
    def test_tests_fail_aborts(self, mock_head, mock_dirty, mock_fetch, mock_ff, mock_forward, mock_tests):
        """Should abort if tests fail after update."""
        old_sha = "a" * 40
        new_sha = "b" * 40
        mock_head.side_effect = [old_sha, new_sha]
        mock_dirty.return_value = False
        mock_fetch.return_value = True
        mock_ff.return_value = (True, new_sha)
        mock_forward.return_value = True
        mock_tests.return_value = False
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self_update(install_dir=Path(tmpdir))
        
        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "tests failed after update")


class TestCanFastForward(unittest.TestCase):
    """Test fast-forward detection."""

    def test_detects_can_ff(self):
        """Should detect when fast-forward is possible."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=tmppath, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmppath, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tmppath, capture_output=True)
            (tmppath / "file.txt").write_text("content")
            subprocess.run(["git", "add", "."], cwd=tmppath, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmppath, capture_output=True)
            subprocess.run(["git", "checkout", "-b", BRANCH], cwd=tmppath, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", str(tmppath)], cwd=tmppath, capture_output=True)
            subprocess.run(["git", "fetch", "origin"], cwd=tmppath, capture_output=True)
            
            can_ff, _ = can_fast_forward(tmppath)


class TestReinstall(unittest.TestCase):
    """Test reinstall function."""

    def test_missing_script_fails(self):
        """Should fail if install script is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = reinstall(Path(tmpdir), "mini")
            self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
