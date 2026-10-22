"""Tests for GitHub client, especially CI status parsing."""

from __future__ import annotations

import pytest

import json
import unittest

from cluster.control_plane.local_karen.github_client import _parse_ci_status


class TestParseCIStatus(unittest.TestCase):
    """Tests for _parse_ci_status with real gh CLI output shapes."""

    def test_bucket_pass(self):
        """bucket=pass maps to success."""
        ci_data = [
            {"name": "build", "state": "SUCCESS", "bucket": "pass"},
            {"name": "test", "state": "SUCCESS", "bucket": "pass"},
        ]
        result = _parse_ci_status(ci_data)
        self.assertEqual(result["build"], "success")
        self.assertEqual(result["test"], "success")

    def test_bucket_fail(self):
        """bucket=fail maps to failure."""
        ci_data = [
            {"name": "build", "state": "FAILURE", "bucket": "fail"},
        ]
        result = _parse_ci_status(ci_data)
        self.assertEqual(result["build"], "failure")

    def test_bucket_pending(self):
        """bucket=pending maps to pending."""
        ci_data = [
            {"name": "build", "state": "PENDING", "bucket": "pending"},
        ]
        result = _parse_ci_status(ci_data)
        self.assertEqual(result["build"], "pending")

    def test_bucket_skipping(self):
        """bucket=skipping maps to skipped."""
        ci_data = [
            {"name": "optional-check", "state": "SKIPPED", "bucket": "skipping"},
        ]
        result = _parse_ci_status(ci_data)
        self.assertEqual(result["optional-check"], "skipped")

    def test_bucket_cancel(self):
        """bucket=cancel maps to cancelled."""
        ci_data = [
            {"name": "build", "state": "CANCELLED", "bucket": "cancel"},
        ]
        result = _parse_ci_status(ci_data)
        self.assertEqual(result["build"], "cancelled")

    def test_real_gh_output_shape(self):
        """Test with realistic gh pr checks --json output.

        This is the actual shape from: gh pr checks 85 --repo rupret007/bob-ops-dashboard --json name,state,bucket
        """
        ci_data = [
            {"bucket": "pass", "name": "Build and Test", "state": "SUCCESS"},
            {"bucket": "pass", "name": "Lint", "state": "SUCCESS"},
            {"bucket": "pending", "name": "Deploy Preview", "state": "PENDING"},
        ]
        result = _parse_ci_status(ci_data)

        self.assertEqual(result["Build and Test"], "success")
        self.assertEqual(result["Lint"], "success")
        self.assertEqual(result["Deploy Preview"], "pending")

    def test_fallback_to_state_when_no_bucket(self):
        """Falls back to state field when bucket is missing."""
        ci_data = [
            {"name": "build", "state": "success"},
            {"name": "test", "state": "failure"},
            {"name": "lint", "state": "neutral"},
        ]
        result = _parse_ci_status(ci_data)

        self.assertEqual(result["build"], "success")
        self.assertEqual(result["test"], "failure")
        self.assertEqual(result["lint"], "success")

    def test_state_pending_variants(self):
        """Various pending state values map to pending."""
        ci_data = [
            {"name": "check1", "state": "pending"},
            {"name": "check2", "state": "queued"},
            {"name": "check3", "state": "in_progress"},
            {"name": "check4", "state": "waiting"},
        ]
        result = _parse_ci_status(ci_data)

        for name in ["check1", "check2", "check3", "check4"]:
            self.assertEqual(result[name], "pending", f"{name} should be pending")

    def test_state_failure_variants(self):
        """Various failure state values map to failure."""
        ci_data = [
            {"name": "check1", "state": "failure"},
            {"name": "check2", "state": "error"},
            {"name": "check3", "state": "timed_out"},
            {"name": "check4", "state": "action_required"},
        ]
        result = _parse_ci_status(ci_data)

        for name in ["check1", "check2", "check3", "check4"]:
            self.assertEqual(result[name], "failure", f"{name} should be failure")

    def test_empty_list(self):
        """Empty CI data returns empty dict."""
        result = _parse_ci_status([])
        self.assertEqual(result, {})

    def test_mixed_results(self):
        """Mixed pass/fail/pending results."""
        ci_data = [
            {"name": "build", "bucket": "pass", "state": "SUCCESS"},
            {"name": "test", "bucket": "fail", "state": "FAILURE"},
            {"name": "deploy", "bucket": "pending", "state": "PENDING"},
            {"name": "lint", "bucket": "skipping", "state": "SKIPPED"},
        ]
        result = _parse_ci_status(ci_data)

        self.assertEqual(result["build"], "success")
        self.assertEqual(result["test"], "failure")
        self.assertEqual(result["deploy"], "pending")
        self.assertEqual(result["lint"], "skipped")

    def test_unknown_bucket(self):
        """Unknown bucket value falls back to state."""
        ci_data = [
            {"name": "check", "bucket": "weird_value", "state": "success"},
        ]
        result = _parse_ci_status(ci_data)
        self.assertEqual(result["check"], "success")

    def test_no_bucket_or_state(self):
        """Missing both bucket and state returns unknown."""
        ci_data = [
            {"name": "mystery-check"},
        ]
        result = _parse_ci_status(ci_data)
        self.assertEqual(result["mystery-check"], "unknown")


class TestCICheckIntegration(unittest.TestCase):
    """Integration tests for CI check behavior in gate."""

    def test_all_pass_allows_gate(self):
        """All passing checks should allow gate to proceed."""
        ci_data = [
            {"name": "build", "bucket": "pass", "state": "SUCCESS"},
            {"name": "test", "bucket": "pass", "state": "SUCCESS"},
            {"name": "lint", "bucket": "skipping", "state": "SKIPPED"},
        ]
        result = _parse_ci_status(ci_data)

        passing = ("success", "skipped", "neutral")
        all_pass = all(status in passing for status in result.values())
        self.assertTrue(all_pass)

    def test_any_pending_blocks_gate(self):
        """Any pending check should block gate."""
        ci_data = [
            {"name": "build", "bucket": "pass", "state": "SUCCESS"},
            {"name": "test", "bucket": "pending", "state": "PENDING"},
        ]
        result = _parse_ci_status(ci_data)

        has_pending = any(status == "pending" for status in result.values())
        self.assertTrue(has_pending)

    def test_any_failure_blocks_gate(self):
        """Any failing check should block gate."""
        ci_data = [
            {"name": "build", "bucket": "pass", "state": "SUCCESS"},
            {"name": "test", "bucket": "fail", "state": "FAILURE"},
        ]
        result = _parse_ci_status(ci_data)

        has_failure = any(status == "failure" for status in result.values())
        self.assertTrue(has_failure)


if __name__ == "__main__":
    unittest.main()


def test_workflow_identity_keeps_each_check():
    result = _parse_ci_status([
        {'name':'tests', 'workflow':'Linux', 'bucket':'fail'},
        {'name':'tests', 'workflow':'Mac', 'bucket':'pass'},
    ])
    assert result == {'Linux / tests':'failure', 'Mac / tests':'success'}


def test_duplicate_check_never_hides_failure_or_pending():
    for workflow in (None, 'CI'):
        for bucket, expected in [('fail','failure'), ('pending','pending')]:
            blocked = {'name':'tests', 'bucket':bucket}
            green = {'name':'tests', 'bucket':'pass'}
            if workflow:
                blocked['workflow'] = green['workflow'] = workflow
            for checks in ([blocked,green], [green,blocked]):
                result = _parse_ci_status(checks)
                assert expected in result.values()
                assert not all(value in ('success','skipped') for value in result.values())


def test_different_workflows_cannot_hide_pending():
    result = _parse_ci_status([
        {'name':'tests', 'workflow':'Linux', 'bucket':'pending'},
        {'name':'tests', 'workflow':'Mac', 'bucket':'pass'},
    ])
    assert result == {'Linux / tests':'pending', 'Mac / tests':'success'}


@pytest.fixture(autouse=True)
def isolated_github_admission(tmp_path, monkeypatch):
    """Mock admission separately from CLI/label semantics; budget has race tests."""
    from cluster.control_plane import github_budget
    monkeypatch.setenv('STORYPOOL_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(github_budget, 'reserve', lambda resource='core': None)
    monkeypatch.setattr(github_budget, 'observe', lambda *args, **kwargs: None)
