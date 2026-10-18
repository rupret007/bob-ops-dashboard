"""Tests for the StoryPool LLM-in-the-loop conductor."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cluster.control_plane.budget_tracker import BudgetTracker
from cluster.control_plane.metrics import MetricsTracker
from cluster.control_plane.conductor import (
    CODEX_WEEKLY_CAP_PCT,
    ActionType,
    CodexClient,
    Conductor,
    GeminiClient,
    ProposedAction,
    StateSnapshot,
    ValidatedAction,
    _ci_status_from_pr,
    _get_verdict_file,
    _has_label,
    _is_approved,
    _now_iso,
    _read_verdict,
)


class TestProposedAction(unittest.TestCase):
    """Tests for ProposedAction dataclass."""

    def test_to_dict_excludes_empty(self):
        """to_dict excludes empty and None values."""
        action = ProposedAction(
            action_type="merge_pr",
            repo="owner/repo",
            pr_number=123,
            head_sha=None,
            reason="",
        )
        d = action.to_dict()
        self.assertIn("repo", d)
        self.assertIn("pr_number", d)
        self.assertNotIn("head_sha", d)
        self.assertNotIn("reason", d)

    def test_to_dict_includes_extra(self):
        """to_dict includes extra fields."""
        action = ProposedAction(
            action_type="flag_anomaly",
            reason="stuck lane detected",
            extra={"lane_id": "cursor-1"},
        )
        d = action.to_dict()
        self.assertIn("extra", d)
        self.assertEqual(d["extra"]["lane_id"], "cursor-1")


class TestValidatedAction(unittest.TestCase):
    """Tests for ValidatedAction dataclass."""

    def test_to_dict_complete(self):
        """to_dict returns complete structure."""
        proposal = ProposedAction(action_type="label_needs_karen", repo="test/repo", pr_number=1)
        va = ValidatedAction(
            proposal=proposal,
            validated=True,
            validation_reason="Validated",
            executed=True,
            execution_result="labeled",
        )
        d = va.to_dict()
        self.assertIn("proposal", d)
        self.assertTrue(d["validated"])
        self.assertTrue(d["executed"])
        self.assertEqual(d["execution_result"], "labeled")


class TestStateSnapshot(unittest.TestCase):
    """Tests for StateSnapshot."""

    def test_to_prompt_context(self):
        """to_prompt_context generates readable context."""
        snapshot = StateSnapshot(
            timestamp="2026-10-17T12:00:00Z",
            repos=["owner/repo1", "owner/repo2"],
            open_prs=[
                {"repo": "owner/repo1", "number": 1, "isDraft": False, "headRefOid": "abc123"}
            ],
            ci_statuses={"owner/repo1/1": {"build": "success"}},
            karen_verdicts=[],
            lane_usage={"codex": 1, "cursor": 2},
            machine_guards={
                "mini": {"load": 5.0, "swap": 30.0, "disk": 100.0, "passed": True},
                "nucbox": {"load": 8.0, "lanes": 3, "passed": True},
            },
            budget_summary={
                "codex": {"cost_usd": 25.0, "weekly_pct": 50.0},
                "cursor": {"cost_usd": 10.0, "weekly_pct": 20.0},
            },
            recent_errors=[],
            never_touch_repos=["owner/dashboard"],
        )
        
        context = snapshot.to_prompt_context()
        
        self.assertIn("StoryPool State Snapshot", context)
        self.assertIn("owner/repo1", context)
        self.assertIn("Never-Touch", context)
        self.assertIn("owner/dashboard", context)
        self.assertIn("Machine Guards", context)
        self.assertIn("Budget Summary", context)


class TestHelperFunctions(unittest.TestCase):
    """Tests for helper functions."""

    def test_ci_status_from_pr_success(self):
        """Success conclusion maps to success."""
        pr = {"statusCheckRollup": [{"name": "build", "conclusion": "SUCCESS"}]}
        status = _ci_status_from_pr(pr)
        self.assertEqual(status["build"], "success")

    def test_ci_status_from_pr_failure(self):
        """Failure conclusion maps to failure."""
        pr = {"statusCheckRollup": [{"name": "test", "conclusion": "FAILURE"}]}
        status = _ci_status_from_pr(pr)
        self.assertEqual(status["test"], "failure")

    def test_ci_status_from_pr_pending(self):
        """Pending state maps to pending."""
        pr = {"statusCheckRollup": [{"name": "lint", "state": "PENDING"}]}
        status = _ci_status_from_pr(pr)
        self.assertEqual(status["lint"], "pending")

    def test_has_label_true(self):
        """has_label returns True when label exists."""
        pr = {"labels": [{"name": "needs-karen"}, {"name": "bug"}]}
        self.assertTrue(_has_label(pr, "needs-karen"))

    def test_has_label_false(self):
        """has_label returns False when label missing."""
        pr = {"labels": [{"name": "bug"}]}
        self.assertFalse(_has_label(pr, "needs-karen"))

    def test_is_approved_true(self):
        """is_approved returns True when APPROVED review exists."""
        pr = {"reviews": [{"state": "APPROVED", "author": {"login": "user"}}]}
        self.assertTrue(_is_approved(pr))

    def test_is_approved_false(self):
        """is_approved returns False when no APPROVED review."""
        pr = {"reviews": [{"state": "CHANGES_REQUESTED"}]}
        self.assertFalse(_is_approved(pr))


class TestVerdictFile(unittest.TestCase):
    """Tests for verdict file handling."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tmpdir = scratch.name
        self.verdict_dir = Path(self.tmpdir)

    def test_get_verdict_file_path(self):
        """Verdict file path is constructed correctly."""
        with patch("cluster.control_plane.conductor.VERDICT_DIR", self.verdict_dir):
            path = _get_verdict_file("owner/repo", 123, "abc123def456")
            self.assertEqual(path.parent.name, "owner_repo")
            self.assertEqual(path.name, "pr-123-abc123def456.json")

    def test_read_verdict_not_exists(self):
        """read_verdict returns None when file doesn't exist."""
        with patch("cluster.control_plane.conductor.VERDICT_DIR", self.verdict_dir):
            verdict = _read_verdict("owner/repo", 123, "abc123")
            self.assertIsNone(verdict)

    def test_read_verdict_karenpass(self):
        """read_verdict returns verdict dict for KARENPASS."""
        with patch("cluster.control_plane.conductor.VERDICT_DIR", self.verdict_dir):
            verdict_file = _get_verdict_file("owner/repo", 123, "abc123def456")
            verdict_file.parent.mkdir(parents=True, exist_ok=True)
            verdict_file.write_text(json.dumps({
                "verdict": "KARENPASS",
                "sha": "abc123def456",
                "reason": "looks good",
            }))

            result = _read_verdict("owner/repo", 123, "abc123def456")
            self.assertIsNotNone(result)
            self.assertEqual(result["verdict"], "KARENPASS")

    def test_read_verdict_sha_mismatch(self):
        """read_verdict returns None when SHA doesn't match."""
        with patch("cluster.control_plane.conductor.VERDICT_DIR", self.verdict_dir):
            verdict_file = _get_verdict_file("owner/repo", 123, "abc123def456")
            verdict_file.parent.mkdir(parents=True, exist_ok=True)
            verdict_file.write_text(json.dumps({
                "verdict": "KARENPASS",
                "sha": "different_sha",
                "reason": "looks good",
            }))

            result = _read_verdict("owner/repo", 123, "abc123def456")
            self.assertIsNone(result)


class TestCodexClient(unittest.TestCase):
    """Tests for CodexClient."""

    @patch("cluster.control_plane.conductor.CodexClient._find_codex")
    @patch("subprocess.run")
    def test_complete_success(self, mock_run, mock_find):
        """complete returns response on success."""
        mock_find.return_value = Path("/usr/bin/codex")
        mock_run.return_value = MagicMock(returncode=0, stdout='{"response": "test"}')
        
        client = CodexClient(host="nuc", effort="low")
        result = client.complete("test prompt")
        
        self.assertEqual(result, '{"response": "test"}')
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        self.assertIn("-c", call_args[0][0])
        self.assertIn("model_reasoning_effort=low", call_args[0][0])
        self.assertEqual(call_args[1]["env"]["CODEX_HOST"], "nuc")

    @patch("cluster.control_plane.conductor.CodexClient._find_codex")
    @patch("subprocess.run")
    def test_complete_failure(self, mock_run, mock_find):
        """complete returns None on failure."""
        mock_find.return_value = Path("/usr/bin/codex")
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        
        client = CodexClient()
        result = client.complete("test prompt")
        
        self.assertIsNone(result)

    @patch("cluster.control_plane.conductor.CodexClient._find_codex")
    @patch("subprocess.run")
    def test_complete_high_effort(self, mock_run, mock_find):
        """complete uses high effort when specified."""
        mock_find.return_value = Path("/usr/bin/codex")
        mock_run.return_value = MagicMock(returncode=0, stdout="response")
        
        client = CodexClient(effort="low")
        client.complete("test prompt", high_effort=True)
        
        call_args = mock_run.call_args[0][0]
        self.assertIn("-c", call_args)
        c_idx = call_args.index("-c")
        self.assertIn("model_reasoning_effort=high", call_args[c_idx + 1])


class TestConductor(unittest.TestCase):
    """Tests for Conductor class."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tmpdir = scratch.name
        self.log_dir = Path(self.tmpdir) / "logs"
        self.verdict_dir = Path(self.tmpdir) / "verdicts"
        self.conductor = Conductor(
            log_dir=self.log_dir,
            verdict_dir=self.verdict_dir,
            budget_tracker=BudgetTracker(Path(self.tmpdir) / "budget"),
            metrics_tracker=MetricsTracker(Path(self.tmpdir) / "metrics"),
        )

    def test_writes_log_entries(self):
        """Log entries are written to JSONL file."""
        self.conductor._write_log({"action": "test", "data": "value"})

        log_file = self.conductor._log_file()
        self.assertTrue(log_file.exists())

        with open(log_file) as f:
            line = f.readline()
            parsed = json.loads(line)
            self.assertEqual(parsed["action"], "test")
            self.assertIn("timestamp", parsed)

    def test_writes_alert_file(self):
        """Alert file is written correctly."""
        self.conductor._write_alert("Test alert", {"reason": "testing"})

        alert_file = self.conductor._alert_file()
        self.assertTrue(alert_file.exists())

        with open(alert_file) as f:
            alert = json.load(f)
            self.assertEqual(alert["message"], "Test alert")
            self.assertEqual(alert["status"], "PAUSED")

    def test_writes_status_file(self):
        """Status file is written correctly."""
        self.conductor._write_status({"validated_count": 5})

        status_file = self.conductor._status_file()
        self.assertTrue(status_file.exists())

        with open(status_file) as f:
            status = json.load(f)
            self.assertEqual(status["summary"]["validated_count"], 5)

    def test_can_use_codex_under_cap(self):
        """Codex can be used when under cap."""
        # Mock budget to return 0% usage
        with patch.object(self.conductor.budget, "weekly_pct", return_value=0.0):
            allowed, reason = self.conductor._can_use_codex()
            self.assertTrue(allowed)
            self.assertEqual(reason, "ok")

    def test_can_use_codex_over_cap(self):
        """Codex cannot be used when over cap."""
        self.conductor.budget.record("codex", cost_usd=50.0)
        allowed, reason = self.conductor._can_use_codex()
        self.assertFalse(allowed)
        self.assertIn("cap", reason.lower())


class TestConductorValidation(unittest.TestCase):
    """Tests for conductor action validation (deterministic rails)."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tmpdir = scratch.name
        self.log_dir = Path(self.tmpdir) / "logs"
        self.verdict_dir = Path(self.tmpdir) / "verdicts"
        self.conductor = Conductor(
            log_dir=self.log_dir,
            verdict_dir=self.verdict_dir,
            budget_tracker=BudgetTracker(Path(self.tmpdir) / "budget"),
            metrics_tracker=MetricsTracker(Path(self.tmpdir) / "metrics"),
        )
        self.snapshot = StateSnapshot(
            timestamp=_now_iso(),
            repos=["owner/repo"],
            open_prs=[],
            ci_statuses={},
            karen_verdicts=[],
            lane_usage={},
            machine_guards={},
            budget_summary={},
            recent_errors=[],
            never_touch_repos=["rupret007/bob-ops-dashboard"],
        )

    def test_validate_merge_rejects_never_touch(self):
        """Merge is rejected for never-touch repos."""
        action = ProposedAction(
            action_type=ActionType.MERGE_PR.value,
            repo="rupret007/bob-ops-dashboard",
            pr_number=123,
            head_sha="abc123",
        )
        
        result = self.conductor.validate_action(action, self.snapshot)
        
        self.assertFalse(result.validated)
        self.assertIn("RAIL", result.validation_reason)
        self.assertIn("NEVER_TOUCH", result.validation_reason)

    @patch("cluster.control_plane.conductor._read_verdict")
    def test_validate_merge_rejects_no_verdict(self, mock_read_verdict):
        """Merge is rejected without KARENPASS verdict."""
        mock_read_verdict.return_value = None
        
        action = ProposedAction(
            action_type=ActionType.MERGE_PR.value,
            repo="owner/repo",
            pr_number=123,
            head_sha="abc123",
        )
        
        result = self.conductor.validate_action(action, self.snapshot)
        
        self.assertFalse(result.validated)
        self.assertIn("RAIL", result.validation_reason)
        self.assertIn("KARENPASS", result.validation_reason)

    @patch("cluster.control_plane.conductor._get_pr_details")
    @patch("cluster.control_plane.conductor._read_verdict")
    def test_validate_merge_rejects_sha_mismatch(self, mock_read_verdict, mock_get_details):
        """Merge is rejected when SHA has changed."""
        mock_read_verdict.return_value = {"verdict": "KARENPASS", "sha": "abc123"}
        mock_get_details.return_value = {
            "headRefOid": "different_sha",
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            "reviews": [{"state": "APPROVED"}],
        }
        
        action = ProposedAction(
            action_type=ActionType.MERGE_PR.value,
            repo="owner/repo",
            pr_number=123,
            head_sha="abc123",
        )
        
        result = self.conductor.validate_action(action, self.snapshot)
        
        self.assertFalse(result.validated)
        self.assertIn("RAIL", result.validation_reason)
        self.assertIn("SHA mismatch", result.validation_reason)

    @patch("cluster.control_plane.conductor._get_pr_details")
    @patch("cluster.control_plane.conductor._read_verdict")
    def test_validate_merge_rejects_ci_failing(self, mock_read_verdict, mock_get_details):
        """Merge is rejected when CI is failing."""
        mock_read_verdict.return_value = {"verdict": "KARENPASS", "sha": "abc123"}
        mock_get_details.return_value = {
            "headRefOid": "abc123",
            "statusCheckRollup": [{"name": "build", "conclusion": "FAILURE"}],
            "reviews": [{"state": "APPROVED"}],
        }
        
        action = ProposedAction(
            action_type=ActionType.MERGE_PR.value,
            repo="owner/repo",
            pr_number=123,
            head_sha="abc123",
        )
        
        result = self.conductor.validate_action(action, self.snapshot)
        
        self.assertFalse(result.validated)
        self.assertIn("RAIL", result.validation_reason)
        self.assertIn("CI not passing", result.validation_reason)

    @patch("cluster.control_plane.conductor._get_pr_details")
    @patch("cluster.control_plane.conductor._read_verdict")
    def test_validate_merge_accepts_valid(self, mock_read_verdict, mock_get_details):
        """Merge is accepted when all conditions met."""
        mock_read_verdict.return_value = {"verdict": "KARENPASS", "sha": "abc123"}
        mock_get_details.return_value = {
            "headRefOid": "abc123",
            "statusCheckRollup": [{"name": "build", "conclusion": "SUCCESS"}],
            "reviews": [{"state": "APPROVED"}],
        }
        
        action = ProposedAction(
            action_type=ActionType.MERGE_PR.value,
            repo="owner/repo",
            pr_number=123,
            head_sha="abc123",
        )
        
        result = self.conductor.validate_action(action, self.snapshot)
        
        self.assertTrue(result.validated)
        self.assertEqual(result.validation_reason, "Validated")

    @patch("cluster.control_plane.conductor._get_pr_details")
    def test_validate_label_rejects_draft(self, mock_get_details):
        """Label is rejected for draft PRs."""
        mock_get_details.return_value = {"isDraft": True}
        
        action = ProposedAction(
            action_type=ActionType.LABEL_NEEDS_KAREN.value,
            repo="owner/repo",
            pr_number=123,
        )
        
        result = self.conductor.validate_action(action, self.snapshot)
        
        self.assertFalse(result.validated)
        self.assertIn("draft", result.validation_reason.lower())

    @patch("cluster.control_plane.conductor._get_pr_details")
    def test_validate_label_rejects_ci_failing(self, mock_get_details):
        """Label is rejected when CI is failing."""
        mock_get_details.return_value = {
            "isDraft": False,
            "statusCheckRollup": [{"name": "build", "conclusion": "FAILURE"}],
            "reviews": [{"state": "APPROVED"}],
            "labels": [],
        }
        
        action = ProposedAction(
            action_type=ActionType.LABEL_NEEDS_KAREN.value,
            repo="owner/repo",
            pr_number=123,
        )
        
        result = self.conductor.validate_action(action, self.snapshot)
        
        self.assertFalse(result.validated)
        self.assertIn("CI not passing", result.validation_reason)

    @patch("cluster.control_plane.conductor._get_pr_details")
    def test_validate_label_rejects_already_labeled(self, mock_get_details):
        """Label is rejected when already has needs-karen."""
        mock_get_details.return_value = {
            "isDraft": False,
            "statusCheckRollup": [{"name": "build", "conclusion": "SUCCESS"}],
            "reviews": [{"state": "APPROVED"}],
            "labels": [{"name": "needs-karen"}],
        }
        
        action = ProposedAction(
            action_type=ActionType.LABEL_NEEDS_KAREN.value,
            repo="owner/repo",
            pr_number=123,
        )
        
        result = self.conductor.validate_action(action, self.snapshot)
        
        self.assertFalse(result.validated)
        self.assertIn("already has", result.validation_reason.lower())

    @patch("cluster.control_plane.conductor._get_pr_details")
    def test_validate_label_accepts_valid(self, mock_get_details):
        """Label is accepted when all conditions met."""
        mock_get_details.return_value = {
            "isDraft": False,
            "statusCheckRollup": [{"name": "build", "conclusion": "SUCCESS"}],
            "reviews": [{"state": "APPROVED"}],
            "labels": [],
        }
        
        action = ProposedAction(
            action_type=ActionType.LABEL_NEEDS_KAREN.value,
            repo="owner/repo",
            pr_number=123,
        )
        
        result = self.conductor.validate_action(action, self.snapshot)
        
        self.assertTrue(result.validated)

    def test_validate_flag_anomaly_always_valid(self):
        """Flag anomaly is always validated (no restrictions)."""
        action = ProposedAction(
            action_type=ActionType.FLAG_ANOMALY.value,
            reason="Suspicious pattern detected",
        )
        
        result = self.conductor.validate_action(action, self.snapshot)
        
        self.assertTrue(result.validated)

    def test_validate_unknown_action_rejected(self):
        """Unknown action types are rejected."""
        action = ProposedAction(
            action_type="unknown_action",
            reason="test",
        )
        
        result = self.conductor.validate_action(action, self.snapshot)
        
        self.assertFalse(result.validated)
        self.assertIn("Unknown action type", result.validation_reason)


class TestConductorExecution(unittest.TestCase):
    """Tests for conductor action execution."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tmpdir = scratch.name
        self.conductor = Conductor(
            log_dir=Path(self.tmpdir) / "logs",
            verdict_dir=Path(self.tmpdir) / "verdicts",
            budget_tracker=BudgetTracker(Path(self.tmpdir) / "budget"),
            metrics_tracker=MetricsTracker(Path(self.tmpdir) / "metrics"),
        )

    def test_execute_shadow_mode(self):
        """Actions are not executed in shadow mode."""
        proposal = ProposedAction(action_type="label_needs_karen", repo="test/repo", pr_number=1)
        validated = ValidatedAction(proposal, True, "Validated")
        
        result = self.conductor.execute_action(validated, live=False)
        
        self.assertFalse(result.executed)
        self.assertEqual(result.execution_result, "shadow_mode")

    @patch("subprocess.run")
    def test_execute_live_label(self, mock_run):
        """Label action is executed in live mode."""
        mock_run.return_value = MagicMock(returncode=0)
        
        proposal = ProposedAction(action_type="label_needs_karen", repo="test/repo", pr_number=1)
        validated = ValidatedAction(proposal, True, "Validated")
        
        result = self.conductor.execute_action(validated, live=True)
        
        self.assertTrue(result.executed)
        self.assertEqual(result.execution_result, "labeled")
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_execute_live_merge(self, mock_run):
        """Merge action is executed in live mode."""
        def mock_run_side_effect(cmd, **kwargs):
            result = MagicMock(returncode=0)
            if "gh" in cmd and "api" in cmd:
                result.stdout = '{"state": "success"}'
            return result
        mock_run.side_effect = mock_run_side_effect
        
        proposal = ProposedAction(
            action_type="merge_pr", 
            repo="test/repo", 
            pr_number=1,
            head_sha="abc123",
        )
        validated = ValidatedAction(proposal, True, "Validated")
        
        result = self.conductor.execute_action(validated, live=True)
        
        self.assertTrue(result.executed)
        self.assertIn("merged", result.execution_result)
        merge_call = [c for c in mock_run.call_args_list if "merge" in str(c)]
        self.assertTrue(len(merge_call) > 0)
        call_args = merge_call[0][0][0]
        self.assertIn("--match-head-commit", call_args)
        self.assertIn("abc123", call_args)

    @patch("subprocess.run")
    def test_execute_handles_error(self, mock_run):
        """Execution errors are handled gracefully."""
        mock_run.side_effect = subprocess.SubprocessError("command failed")
        
        proposal = ProposedAction(action_type="label_needs_karen", repo="test/repo", pr_number=1)
        validated = ValidatedAction(proposal, True, "Validated")
        
        result = self.conductor.execute_action(validated, live=True)
        
        self.assertFalse(result.executed)
        self.assertIn("error", result.execution_result)


class TestConductorDigest(unittest.TestCase):
    """Tests for conductor digest generation."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tmpdir = scratch.name
        self.conductor = Conductor(
            log_dir=Path(self.tmpdir) / "logs",
            verdict_dir=Path(self.tmpdir) / "verdicts",
            budget_tracker=BudgetTracker(Path(self.tmpdir) / "budget"),
            metrics_tracker=MetricsTracker(Path(self.tmpdir) / "metrics"),
        )

    def test_writes_digest_file(self):
        """Digest file is written with correct content."""
        snapshot = StateSnapshot(
            timestamp=_now_iso(),
            repos=["owner/repo"],
            open_prs=[{"repo": "owner/repo", "number": 1}],
            ci_statuses={},
            karen_verdicts=[],
            lane_usage={"codex": 1},
            machine_guards={
                "mini": {"load": 5.0, "passed": True},
                "nucbox": {"load": 8.0, "passed": True},
            },
            budget_summary={"codex": {"cost_usd": 10.0, "weekly_pct": 20.0}},
            recent_errors=[],
            never_touch_repos=[],
        )
        
        proposals = {
            "analysis": "Test analysis",
            "summary": "Test summary",
            "proposed_actions": [],
            "anomalies": ["Test anomaly"],
        }
        
        self.conductor.write_digest(snapshot, proposals, [])
        
        digest_file = self.conductor._digest_file()
        self.assertTrue(digest_file.exists())
        
        content = digest_file.read_text()
        self.assertIn("StoryPool Morning Digest", content)
        self.assertIn("Merged", content)
        self.assertIn("Fixed by Fix Loop", content)
        self.assertIn("Still Failing", content)
        self.assertIn("codex", content)


class TestConductorRun(unittest.TestCase):
    """Tests for conductor run cycle."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.tmpdir = scratch.name
        self.conductor = Conductor(
            log_dir=Path(self.tmpdir) / "logs",
            verdict_dir=Path(self.tmpdir) / "verdicts",
            budget_tracker=BudgetTracker(Path(self.tmpdir) / "budget"),
            metrics_tracker=MetricsTracker(Path(self.tmpdir) / "metrics"),
        )

    @patch.object(Conductor, "get_llm_proposals")
    @patch.object(Conductor, "build_state_snapshot")
    def test_run_fail_safe_no_llm(self, mock_snapshot, mock_proposals):
        """Run enters fail-safe when LLM unavailable."""
        mock_snapshot.return_value = StateSnapshot(
            timestamp=_now_iso(),
            repos=[],
            open_prs=[],
            ci_statuses={},
            karen_verdicts=[],
            lane_usage={},
            machine_guards={},
            budget_summary={},
            recent_errors=[],
            never_touch_repos=[],
        )
        mock_proposals.return_value = None
        
        summary = self.conductor.run(live=False)
        
        self.assertFalse(summary["llm_available"])
        self.assertEqual(summary["proposals_count"], 0)
        
        alert_file = self.conductor._alert_file()
        self.assertTrue(alert_file.exists())

    @patch.object(Conductor, "get_llm_proposals")
    @patch.object(Conductor, "build_state_snapshot")
    def test_run_validates_proposals(self, mock_snapshot, mock_proposals):
        """Run validates all proposals against rails."""
        mock_snapshot.return_value = StateSnapshot(
            timestamp=_now_iso(),
            repos=[],
            open_prs=[],
            ci_statuses={},
            karen_verdicts=[],
            lane_usage={},
            machine_guards={},
            budget_summary={},
            recent_errors=[],
            never_touch_repos=["rupret007/bob-ops-dashboard"],
        )
        mock_proposals.return_value = {
            "analysis": "Test",
            "summary": "Test",
            "anomalies": [],
            "proposed_actions": [
                {
                    "action_type": "merge_pr",
                    "repo": "rupret007/bob-ops-dashboard",
                    "pr_number": 1,
                    "head_sha": "abc123",
                    "reason": "Should be rejected",
                },
                {
                    "action_type": "flag_anomaly",
                    "reason": "Should be validated",
                },
            ],
        }
        
        summary = self.conductor.run(live=False)
        
        self.assertTrue(summary["llm_available"])
        self.assertEqual(summary["proposals_count"], 2)
        self.assertEqual(summary["rejected_count"], 1)
        self.assertEqual(summary["validated_count"], 1)


class TestNeverTouchIntegration(unittest.TestCase):
    """Tests for never_touch integration."""

    def test_never_touch_repos_checked(self):
        """check_merge_allowed is imported and used."""
        from cluster.never_touch import check_merge_allowed

        allowed, reason = check_merge_allowed("rupret007/bob-ops-dashboard")
        self.assertFalse(allowed)
        self.assertIn("NEVER_TOUCH", reason)

        allowed, reason = check_merge_allowed("rupret007/webjam")
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
