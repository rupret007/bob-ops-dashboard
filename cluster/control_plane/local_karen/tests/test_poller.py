"""Tests for Karen poller."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cluster.control_plane.local_karen.config import KarenConfig
from cluster.control_plane.local_karen.poller import (
    PollState,
    load_poll_state,
    save_poll_state,
)


class TestPollState(unittest.TestCase):
    """Tests for PollState dataclass."""

    def test_to_dict(self):
        """PollState should serialize to dict."""
        state = PollState(
            last_poll="2026-10-17T00:00:00Z",
            prs_checked=[{"repo": "test/repo", "pr": 1}],
        )
        d = state.to_dict()
        self.assertEqual(d["last_poll"], "2026-10-17T00:00:00Z")
        self.assertEqual(len(d["prs_checked"]), 1)

    def test_from_dict(self):
        """PollState should deserialize from dict."""
        d = {
            "last_poll": "2026-10-17T00:00:00Z",
            "prs_checked": [{"repo": "test/repo", "pr": 1}],
        }
        state = PollState.from_dict(d)
        self.assertEqual(state.last_poll, "2026-10-17T00:00:00Z")
        self.assertEqual(len(state.prs_checked), 1)

    def test_from_dict_defaults(self):
        """PollState should handle missing keys."""
        state = PollState.from_dict({})
        self.assertEqual(state.last_poll, "")
        self.assertEqual(state.prs_checked, [])


class TestPollStateStorage(unittest.TestCase):
    """Tests for poll state persistence."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = KarenConfig(state_dir=self.tmpdir)

    def test_save_and_load(self):
        """State should roundtrip through save/load."""
        state = PollState(
            last_poll="2026-10-17T12:00:00Z",
            prs_checked=[
                {"repo": "test/repo", "pr": 1, "passed": True},
                {"repo": "test/repo", "pr": 2, "passed": False},
            ],
        )
        save_poll_state(state, self.config)
        
        loaded = load_poll_state(self.config)
        self.assertEqual(loaded.last_poll, state.last_poll)
        self.assertEqual(len(loaded.prs_checked), 2)

    def test_load_missing_file(self):
        """Loading missing state should return empty state."""
        state = load_poll_state(self.config)
        self.assertEqual(state.last_poll, "")
        self.assertEqual(state.prs_checked, [])


class TestNeverTouchRespected(unittest.TestCase):
    """Tests that poller respects never-touch repos."""

    def test_config_has_never_touch(self):
        """Config should include rad-dad-show-night in never-touch."""
        config = KarenConfig()
        self.assertIn("rupret007/rad-dad-show-night", config.never_touch_repos)

    def test_blocked_repo_check(self):
        """Config should correctly identify blocked repos."""
        config = KarenConfig()
        self.assertTrue(config.is_blocked_repo("rupret007", "rad-dad-show-night"))
        self.assertFalse(config.is_blocked_repo("rupret007", "webjam"))


if __name__ == "__main__":
    unittest.main()

class TestCLIResultOutput(unittest.TestCase):
    def test_once_prints_pass_and_fail_with_reason(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from cluster.control_plane.local_karen import poller
        results = [dict(repo='owner/repo', pr=1, sha='abc', passed=passed,
                        reason='review reason') for passed in (True, False)]
        output = StringIO()
        with patch('sys.argv', ['karen-poller', '--once']), \
             patch.object(poller.KarenConfig, 'load'), \
             patch.object(poller, 'run_poll_once', return_value=results), \
             redirect_stdout(output):
            self.assertEqual(poller.main(), 0)
        self.assertIn('PASS owner/repo#1@abc: review reason', output.getvalue())
        self.assertIn('FAIL owner/repo#1@abc: review reason', output.getvalue())
