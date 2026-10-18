"""Tests for self-update logic."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from cluster.control_plane.self_update import (
    BRANCH,
    KAREN_MODULE_DIR,
    get_current_head,
    is_working_tree_dirty,
    fetch_branch,
    can_fast_forward,
    fast_forward,
    run_tests,
    reinstall,
    self_update,
    clear_pycache,
    copy_karen_module,
    migrate_config,
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
    @patch("cluster.control_plane.self_update.rollback_to_sha")
    @patch("cluster.control_plane.self_update.fast_forward")
    @patch("cluster.control_plane.self_update.can_fast_forward")
    @patch("cluster.control_plane.self_update.fetch_branch")
    @patch("cluster.control_plane.self_update.is_working_tree_dirty")
    @patch("cluster.control_plane.self_update.get_current_head")
    def test_tests_fail_aborts(self, mock_head, mock_dirty, mock_fetch, mock_ff, mock_forward, mock_rollback, mock_tests):
        """Should abort and rollback if tests fail after update."""
        old_sha = "a" * 40
        new_sha = "b" * 40
        mock_head.side_effect = [old_sha, new_sha]
        mock_dirty.return_value = False
        mock_fetch.return_value = True
        mock_ff.return_value = (True, new_sha)
        mock_forward.return_value = (True, old_sha)
        mock_tests.return_value = False
        mock_rollback.return_value = True
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self_update(install_dir=Path(tmpdir))
        
        self.assertFalse(result["updated"])
        self.assertIn("tests failed", result["reason"])
        self.assertTrue(result.get("rolled_back", False))


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


class TestClearPycache(unittest.TestCase):
    """Test pycache clearing."""

    def test_clears_pycache_dirs(self):
        """Should remove __pycache__ directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            pycache = tmppath / "subdir" / "__pycache__"
            pycache.mkdir(parents=True)
            (pycache / "module.cpython-39.pyc").touch()
            
            count = clear_pycache(tmppath)
            
            self.assertGreater(count, 0)
            self.assertFalse(pycache.exists())

    def test_clears_pyc_files(self):
        """Should remove .pyc files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            pyc = tmppath / "module.pyc"
            pyc.touch()
            
            count = clear_pycache(tmppath)
            
            self.assertEqual(count, 1)
            self.assertFalse(pyc.exists())

    def test_handles_nonexistent_dir(self):
        """Should handle nonexistent directory."""
        count = clear_pycache(Path("/nonexistent/path"))
        self.assertEqual(count, 0)


class TestCopyKarenModule(unittest.TestCase):
    """Test karen module copying."""

    def test_copies_module_files(self):
        """Should copy karen module to destination."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            
            src = tmppath / "repo"
            src_karen = src / "cluster" / "control_plane" / "local_karen"
            src_karen.mkdir(parents=True)
            (src_karen / "__init__.py").write_text("# karen init")
            (src_karen / "config.py").write_text("# config")
            (src / "cluster" / "control_plane" / "backlog.py").write_text("# backlog")
            (src / "cluster" / "control_plane" / "self_update.py").write_text("# self_update")
            (src / "cluster" / "never_touch.py").write_text("# never_touch")
            
            dst = tmppath / "module"
            
            result = copy_karen_module(src, dst)
            
            self.assertTrue(result)
            self.assertTrue((dst / "cluster" / "control_plane" / "local_karen" / "config.py").exists())
            self.assertTrue((dst / "cluster" / "control_plane" / "backlog.py").exists())
            self.assertTrue((dst / "cluster" / "control_plane" / "self_update.py").exists())
            self.assertTrue((dst / "cluster" / "never_touch.py").exists())
            self.assertTrue((dst / "cluster" / "__init__.py").exists())

    def test_handles_missing_source(self):
        """Should fail if source doesn't exist."""
        result = copy_karen_module(Path("/nonexistent/repo"))
        self.assertFalse(result)


class TestMigrateConfig(unittest.TestCase):
    """Test config migration."""

    def test_migrates_empty_repos_list(self):
        """Should migrate repos: [] to repos: 'auto'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps({"repos": []}))
            
            with patch("cluster.control_plane.self_update.Path") as mock_path:
                mock_path.return_value.expanduser.return_value = config_path
                mock_path.return_value.is_file.return_value = True
                
                with patch.dict(os.environ, {"KAREN_CONFIG": str(config_path)}):
                    result = migrate_config()
            
            data = json.loads(config_path.read_text())
            self.assertEqual(data["repos"], "auto")

    def test_migrates_single_repo_list(self):
        """Should migrate repos: ['one/repo'] to repos: 'auto'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps({"repos": ["rupret007/webjam"]}))
            
            with patch.dict(os.environ, {"KAREN_CONFIG": str(config_path)}):
                result = migrate_config()
            
            data = json.loads(config_path.read_text())
            self.assertEqual(data["repos"], "auto")

    def test_preserves_multi_repo_list(self):
        """Should convert repos: ['a', 'b'] to comma-separated string."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps({"repos": ["rupret007/a", "rupret007/b"]}))
            
            with patch.dict(os.environ, {"KAREN_CONFIG": str(config_path)}):
                result = migrate_config()
            
            data = json.loads(config_path.read_text())
            self.assertEqual(data["repos"], "rupret007/a,rupret007/b")

    def test_adds_repos_if_missing(self):
        """Should add repos: 'auto' if not present."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(json.dumps({"codex_host": "nuc"}))
            
            with patch.dict(os.environ, {"KAREN_CONFIG": str(config_path)}):
                result = migrate_config()
            
            data = json.loads(config_path.read_text())
            self.assertEqual(data["repos"], "auto")
            self.assertEqual(data["codex_host"], "nuc")


if __name__ == "__main__":
    unittest.main()
