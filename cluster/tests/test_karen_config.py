"""Tests for Karen config auto-discovery migration."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cluster.control_plane.local_karen.config import (
    KarenConfig,
    AUTO_DISCOVER_SENTINEL,
)


class TestAutoDiscoverSentinel(unittest.TestCase):
    """Test auto-discover sentinel value."""

    def test_sentinel_is_auto(self):
        """Sentinel should be 'auto'."""
        self.assertEqual(AUTO_DISCOVER_SENTINEL, "auto")


class TestShouldAutoDiscover(unittest.TestCase):
    """Test auto-discovery detection."""

    def test_default_is_auto(self):
        """Default config should auto-discover."""
        config = KarenConfig()
        self.assertTrue(config.should_auto_discover())

    def test_auto_string_discovers(self):
        """repos='auto' should trigger auto-discovery."""
        config = KarenConfig(repos="auto")
        self.assertTrue(config.should_auto_discover())

    def test_empty_string_discovers(self):
        """repos='' should trigger auto-discovery."""
        config = KarenConfig(repos="")
        self.assertTrue(config.should_auto_discover())

    def test_single_repo_no_discover(self):
        """repos='owner/repo' should not auto-discover."""
        config = KarenConfig(repos="rupret007/webjam")
        self.assertFalse(config.should_auto_discover())

    def test_multiple_repos_no_discover(self):
        """repos='a,b' should not auto-discover."""
        config = KarenConfig(repos="rupret007/a,rupret007/b")
        self.assertFalse(config.should_auto_discover())


class TestGetReposList(unittest.TestCase):
    """Test repos list extraction."""

    def test_auto_returns_empty(self):
        """repos='auto' should return empty list."""
        config = KarenConfig(repos="auto")
        self.assertEqual(config.get_repos_list(), [])

    def test_single_repo_returns_list(self):
        """repos='owner/repo' should return single-item list."""
        config = KarenConfig(repos="rupret007/webjam")
        self.assertEqual(config.get_repos_list(), ["rupret007/webjam"])

    def test_multiple_repos_returns_list(self):
        """repos='a,b' should return list."""
        config = KarenConfig(repos="rupret007/a,rupret007/b")
        self.assertEqual(config.get_repos_list(), ["rupret007/a", "rupret007/b"])

    def test_strips_whitespace(self):
        """Should strip whitespace from repo names."""
        config = KarenConfig(repos=" rupret007/a , rupret007/b ")
        self.assertEqual(config.get_repos_list(), ["rupret007/a", "rupret007/b"])


class TestPostInit(unittest.TestCase):
    """Test __post_init__ conversion."""

    def test_empty_list_becomes_auto(self):
        """repos=[] should become 'auto'."""
        config = KarenConfig(repos=[])
        self.assertEqual(config.repos, "auto")

    def test_list_becomes_string(self):
        """repos=['a', 'b'] should become 'a,b'."""
        config = KarenConfig(repos=["rupret007/a", "rupret007/b"])
        self.assertEqual(config.repos, "rupret007/a,rupret007/b")


class TestLoadConfig(unittest.TestCase):
    """Test config loading with migration."""

    def test_loads_repos_auto(self):
        """Should load repos='auto' as auto-discover."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"repos": "auto"}, f)
            f.flush()
            
            config = KarenConfig.load(Path(f.name))
            self.assertTrue(config.should_auto_discover())
            
            Path(f.name).unlink()

    def test_loads_repos_empty_list(self):
        """Should load repos=[] as auto-discover."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"repos": []}, f)
            f.flush()
            
            config = KarenConfig.load(Path(f.name))
            self.assertTrue(config.should_auto_discover())
            
            Path(f.name).unlink()

    def test_loads_repos_null(self):
        """Should load repos=null as auto-discover."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"repos": None}, f)
            f.flush()
            
            config = KarenConfig.load(Path(f.name))
            self.assertTrue(config.should_auto_discover())
            
            Path(f.name).unlink()

    def test_loads_repos_list(self):
        """Should load repos=['a'] as list."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"repos": ["rupret007/webjam"]}, f)
            f.flush()
            
            config = KarenConfig.load(Path(f.name))
            self.assertEqual(config.get_repos_list(), ["rupret007/webjam"])
            
            Path(f.name).unlink()


class TestMigrateConfig(unittest.TestCase):
    """Test config migration."""

    def test_migrates_missing_repos(self):
        """Should add repos='auto' if missing."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"codex_host": "nuc"}, f)
            f.flush()
            path = Path(f.name)
            
            result = KarenConfig.migrate_config(path)
            
            self.assertTrue(result)
            data = json.loads(path.read_text())
            self.assertEqual(data["repos"], "auto")
            
            path.unlink()

    def test_migrates_empty_list(self):
        """Should migrate repos=[] to 'auto'."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"repos": []}, f)
            f.flush()
            path = Path(f.name)
            
            result = KarenConfig.migrate_config(path)
            
            self.assertTrue(result)
            data = json.loads(path.read_text())
            self.assertEqual(data["repos"], "auto")
            
            path.unlink()

    def test_migrates_single_repo(self):
        """Should migrate repos=['single'] to 'auto'."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"repos": ["rupret007/webjam"]}, f)
            f.flush()
            path = Path(f.name)
            
            result = KarenConfig.migrate_config(path)
            
            self.assertTrue(result)
            data = json.loads(path.read_text())
            self.assertEqual(data["repos"], "auto")
            
            path.unlink()

    def test_no_migrate_needed(self):
        """Should not migrate if repos already 'auto'."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"repos": "auto"}, f)
            f.flush()
            path = Path(f.name)
            
            result = KarenConfig.migrate_config(path)
            
            self.assertFalse(result)
            
            path.unlink()

    def test_no_file_returns_false(self):
        """Should return False if config doesn't exist."""
        result = KarenConfig.migrate_config(Path("/nonexistent/config.json"))
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
