"""Regressions for incomplete reviews, accounting, capacity, and Pages exposure."""

import json
import stat
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml

from cluster.control_plane.budget_tracker import BudgetCaps, BudgetTracker
from cluster.control_plane.gate_runner import (
    ApprovalChecker, CIStatusChecker, ClaudeGateChecker, GateResult, GateRunner,
    GateVerdict, MiniMaxSecondJudge, PRContext, validate_merge_gate,
)
from cluster.control_plane.guards import MachineGuards, get_lane_count, get_swap_pct
from cluster.control_plane.shadow_compare import compare_report, parse_log_file, summarize_day
from cluster.control_plane.shadow_runner import ShadowDecision, ShadowRunner, _ci_status_from_pr, _reviews_from_pr


SHA = "a" * 40


def approval(state="APPROVED", sha=SHA, login="reviewer", **extra):
    return {"state": state, "user": {"login": login}, "commit": {"oid": sha}, **extra}


@pytest.fixture
def context():
    return PRContext("owner", "repo", 84, "Review", "author", SHA, "main", False,
                     "diff --git a/main.py b/main.py\n+safe()", [],
                     {"tests": "success"}, [approval()])


@pytest.mark.parametrize("response", [
    "PASS", "PASS:", "PASS:   ", "PASS: ready\nFAIL: security issue",
    "Review follows\nPASS: ready", "PASS: ready\nMore review pending", "", None,
    "```\nPASS: ready\n```", "PASS: ready; FAIL: auth issue",
])
def test_ambiguous_primary_response_never_passes(context, response):
    client = Mock()
    client.complete.return_value = response
    assert ClaudeGateChecker(client).check(context).verdict == GateVerdict.NEEDS_REVIEW


@pytest.mark.parametrize("overrides", [
    {"diff": ""}, {"diff": " "}, {"diff_complete": False},
    {"diff": "a" * 8000 + "\n+unsafe_tail()"}, {"head_sha": ""},
])
def test_incomplete_diff_is_not_sent_for_approval(context, overrides):
    client = Mock()
    client.complete.return_value = "PASS: ready"
    assert ClaudeGateChecker(client).check(replace(context, **overrides)).verdict == GateVerdict.NEEDS_REVIEW
    client.complete.assert_not_called()


def test_complete_diff_and_full_sha_reach_primary(context):
    client = Mock()
    client.complete.return_value = "PASS: full review complete"
    context.diff = "x" * 7990 + "UNIQUE_END"
    assert ClaudeGateChecker(client).check(context).passed
    prompt = client.complete.call_args.args[0]
    assert context.diff in prompt
    assert SHA in prompt


def test_review_service_failure_requires_review(context):
    client = Mock()
    client.complete.side_effect = RuntimeError("synthetic provider failure")
    result = ClaudeGateChecker(client).check(context)
    assert result.verdict == GateVerdict.NEEDS_REVIEW
    assert "synthetic provider failure" not in result.reason


def test_missing_diff_does_not_call_either_judge(context):
    primary, secondary = Mock(), Mock()
    result = GateRunner(primary, secondary).run(replace(context, diff_complete=False))
    assert result.verdict == GateVerdict.NEEDS_REVIEW
    primary.complete.assert_not_called()
    secondary.complete.assert_not_called()


@pytest.mark.parametrize("response", ["DISAGREE: safe", "AGREE: concern remains", "PASS: safe", "", None,
                                     "DISAGREE: safe\nAGREE: concern remains"])
def test_second_judge_cannot_promote_incomplete_review(context, response):
    primary, secondary = Mock(), Mock()
    primary.complete.return_value = "NEEDS_REVIEW: incomplete security review"
    secondary.complete.return_value = response
    result = GateRunner(primary, secondary).run(context)
    assert not result.passed
    assert result.verdict in (GateVerdict.FAIL, GateVerdict.NEEDS_REVIEW)


def test_second_judge_failure_requires_review(context):
    secondary = Mock()
    secondary.complete.side_effect = RuntimeError("unavailable")
    result = GateResult(GateVerdict.NEEDS_REVIEW, "uncertain", SHA, 84, "claude")
    assert MiniMaxSecondJudge(secondary, result).check(context).verdict == GateVerdict.NEEDS_REVIEW


@pytest.mark.parametrize("reviews", [
    [], [{"state": "APPROVED"}], [approval(sha="old")], [approval(login="author")],
    [approval(), approval("CHANGES_REQUESTED")], [approval(), approval("DISMISSED")],
    [approval(), approval("CHANGES_REQUESTED", login="other")],
    [approval(), approval(sha="old")],
])
def test_stale_missing_or_superseded_reviews_block(context, reviews):
    assert ApprovalChecker().check(replace(context, reviews=reviews)).verdict == GateVerdict.FAIL


def test_latest_approval_supersedes_earlier_changes_and_comments(context):
    context.reviews = [approval("CHANGES_REQUESTED"), approval(), approval("COMMENTED")]
    assert ApprovalChecker().check(context) is None


def test_review_order_uses_submission_time(context):
    context.reviews = [approval("CHANGES_REQUESTED", submittedAt="2026-10-18T12:00:00Z"),
                       approval(submittedAt="2026-10-17T12:00:00Z")]
    assert ApprovalChecker().check(context).verdict == GateVerdict.FAIL


def test_empty_ci_does_not_pass(context):
    assert CIStatusChecker().check(replace(context, ci_status={})).verdict == GateVerdict.FAIL


@pytest.mark.parametrize("result", [
    None, GateResult(GateVerdict.FAIL, "blocked", SHA, 84, "claude"),
    GateResult(GateVerdict.NEEDS_REVIEW, "uncertain", SHA, 84, "claude"),
    GateResult(GateVerdict.PASS, "reviewed", "old", 84, "claude"),
    GateResult(GateVerdict.PASS, "reviewed", SHA, 85, "claude"),
])
def test_merge_requires_explicit_pass_for_same_pr_and_sha(context, result):
    assert not validate_merge_gate(84, SHA, SHA, False, context.ci_status,
                                   context.reviews, result, author=context.author)[0]


def test_merge_rechecks_current_state_after_pass(context):
    result = GateResult(GateVerdict.PASS, "complete review", SHA, 84, "claude")
    assert validate_merge_gate(84, SHA, SHA, False, context.ci_status,
                               context.reviews, result, author="author") == (True, "ok")
    assert not validate_merge_gate(84, SHA, SHA, False, context.ci_status,
                                   [approval(login="author")], result, author="author")[0]
    assert not validate_merge_gate(84, "", "", False, context.ci_status,
                                   context.reviews, result)[0]
    assert not validate_merge_gate(84, SHA, SHA, False, context.ci_status,
                                   context.reviews, result)[0]


@pytest.mark.parametrize("date", [(2026, 3, 1), (2027, 1, 1)])
def test_week_start_crosses_calendar_boundaries(tmp_path, date):
    tracker = BudgetTracker(tmp_path)
    now = datetime(*date, tzinfo=timezone.utc)
    with patch("cluster.control_plane.budget_tracker.datetime", wraps=datetime) as clock:
        clock.now.return_value = now
        start = datetime.fromtimestamp(tracker._week_start(), timezone.utc)
    assert start.weekday() == 0
    assert start.hour == 0
    assert 0 <= (now - start).days <= 6


def test_prepaid_spend_survives_weekly_reset(tmp_path):
    with patch("cluster.control_plane.budget_tracker.datetime", wraps=datetime) as clock:
        clock.now.return_value = datetime(2026, 3, 1, tzinfo=timezone.utc)
        tracker = BudgetTracker(tmp_path)
        tracker.record("gemini", cost_usd=121)
        tracker.record("claude", cost_usd=20)
        clock.now.return_value = datetime(2026, 3, 2, tzinfo=timezone.utc)
        reloaded = BudgetTracker(tmp_path)
        assert not reloaded.can_use("gemini")[0]
        assert reloaded.get_usage("gemini").cost_usd == 121
        assert reloaded.can_use("claude")[0]
        assert reloaded.get_usage("claude").cost_usd == 0


@pytest.mark.parametrize("changes", [
    {"tokens_in": -1}, {"tokens_out": 1.5}, {"tokens_in": True},
    {"cost_usd": -1}, {"cost_usd": float("nan")}, {"cost_usd": float("inf")},
])
def test_invalid_accounting_cannot_reduce_budget(tmp_path, changes):
    tracker = BudgetTracker(tmp_path)
    tracker.record("claude", cost_usd=20)
    with pytest.raises(ValueError):
        tracker.record("claude", **changes)
    assert not tracker.can_use("claude")[0]


@pytest.mark.parametrize("shared", [False, True])
def test_concurrent_trackers_preserve_every_record(tmp_path, shared):
    trackers = [BudgetTracker(tmp_path) for _ in range(12)]
    if shared:
        trackers = [trackers[0]] * 12
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda tracker: tracker.record("claude", tokens_in=10, cost_usd=1), trackers))
    usage = BudgetTracker(tmp_path).get_usage("claude")
    assert (usage.calls, usage.tokens_in, usage.cost_usd) == (12, 120, 12)
    assert stat.S_IMODE((tmp_path / "budget_usage.json").stat().st_mode) == 0o600


def test_corrupt_budget_state_cannot_become_zero_spend(tmp_path):
    (tmp_path / "budget_usage.json").write_text("{broken")
    with pytest.raises(ValueError):
        BudgetTracker(tmp_path)


def test_read_only_budget_creates_no_state(tmp_path):
    data = tmp_path / "absent"
    tracker = BudgetTracker(data, read_only=True)
    allowed, reason = tracker.can_use("claude")
    assert not allowed
    assert "unavailable" in reason
    assert not data.exists()
    with pytest.raises(ValueError):
        tracker.record("claude", cost_usd=1)
    assert not data.exists()


def test_missing_usage_does_not_simulate_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYPOOL_DATA_DIR", str(tmp_path / "unused-budget"))
    runner = ShadowRunner("mini", tmp_path / "logs")
    with patch.object(runner, "check_machine_guards", return_value=(True, "ok", {"lanes": 0})), \
         patch("cluster.control_plane.shadow_runner._get_open_prs", return_value=[{"number": 84}]):
        decisions = runner.evaluate_dispatch()
    assert not any(d.action == "would_dispatch" for d in decisions)
    assert any("Usage data unavailable" in d.reason for d in decisions)


@pytest.mark.parametrize("cap", [-1, float("nan"), float("inf")])
def test_invalid_caps_fail_closed(tmp_path, cap):
    with pytest.raises(ValueError):
        BudgetTracker(tmp_path, caps=BudgetCaps(claude_weekly_usd=cap))


@pytest.mark.parametrize("machine,occupied,expected", [("nucbox", 6, 1), ("nucbox", 7, 0),
                                                        ("mini", 5, 1), ("mini", 6, 0)])
def test_dispatch_is_limited_to_free_slots(tmp_path, machine, occupied, expected):
    runner = ShadowRunner(machine, tmp_path / "logs", BudgetTracker(tmp_path / "budget"))
    candidates = [{"number": number, "isDraft": False} for number in range(5)]
    with patch("cluster.control_plane.guards.get_load1", return_value=1) as load, \
         patch("cluster.control_plane.guards.get_swap_pct", return_value=0), \
         patch("cluster.control_plane.guards.get_free_disk_gb", return_value=100), \
         patch("cluster.control_plane.guards.get_lane_count", return_value=occupied) as lanes, \
         patch("cluster.control_plane.shadow_runner._get_open_prs", return_value=candidates):
        decisions = runner.evaluate_dispatch()
    assert sum(d.action == "would_dispatch" for d in decisions) == expected
    load.assert_called_once()
    lanes.assert_called_once()


def test_exhausted_lane_budget_prevents_dispatch(tmp_path):
    tracker = BudgetTracker(tmp_path / "budget")
    tracker.record("cursor", cost_usd=50)
    runner = ShadowRunner("mini", tmp_path / "logs", tracker)
    with patch.object(runner, "check_machine_guards", return_value=(True, "ok", {"lanes": 0})), \
         patch("cluster.control_plane.shadow_runner._get_open_prs", return_value=[{"number": 84}]):
        decisions = runner.evaluate_dispatch()
    assert not any(d.action == "would_dispatch" for d in decisions)
    assert any("Budget cap" in d.reason for d in decisions)


def test_unavailable_lane_telemetry_blocks_dispatch(tmp_path):
    runner = ShadowRunner("nucbox", tmp_path / "logs", BudgetTracker(tmp_path / "budget"))
    with patch("cluster.control_plane.guards.get_load1", return_value=1), \
         patch("cluster.control_plane.guards.get_lane_count", side_effect=OSError("unavailable")), \
         patch("cluster.control_plane.shadow_runner._get_open_prs") as prs:
        decisions = runner.evaluate_dispatch()
    assert len(decisions) == 1
    assert decisions[0].action == "would_skip"
    prs.assert_not_called()


def test_macos_swap_is_measured():
    with patch("cluster.control_plane.guards.platform.system", return_value="Darwin"), \
         patch("cluster.control_plane.guards.subprocess.run", return_value=Mock(stdout="total = 1.00G used = 900.00M free = 124.00M")):
        assert get_swap_pct() == pytest.approx(87.890625)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_health_telemetry_blocks(value):
    assert not MachineGuards("mini", load_fn=lambda: value).check_all().passed


def test_unknown_machine_is_not_healthy():
    assert not MachineGuards("unknown", load_fn=lambda: 0).check_all().passed


def test_live_lane_count_excludes_service_screens():
    result = Mock(returncode=1, stdout="123.nlane-work (Detached)\n456.svc-hourly (Detached)\n789.lane-other (Detached)")
    with patch("cluster.control_plane.guards.subprocess.run", return_value=result):
        assert get_lane_count() == 2


@pytest.mark.parametrize("checks", [
    [{"name": "tests", "conclusion": "FAILURE"}, {"name": "tests", "conclusion": "SUCCESS"}],
    [{"name": "tests", "conclusion": "SUCCESS"}, {"name": "tests", "conclusion": "FAILURE"}],
    [{"name": "tests", "status": "IN_PROGRESS", "conclusion": "SUCCESS"}],
])
def test_ci_rollup_cannot_hide_blocking_check(checks):
    assert _ci_status_from_pr({"statusCheckRollup": checks})["tests"] != "success"


def test_review_parser_preserves_commit_and_time():
    review = {"state": "APPROVED", "author": {"login": "reviewer"}, "commit": {"oid": SHA},
              "submittedAt": "2026-10-18T12:00:00Z"}
    parsed = _reviews_from_pr({"reviews": [review]})[0]
    assert parsed["commit"] == review["commit"]
    assert parsed["submittedAt"] == review["submittedAt"]


def test_comparison_counts_skips_and_distinct_runs(tmp_path):
    decisions = [{"timestamp": "2026-10-18T12:00:00Z", "machine": "mini", "run_id": run,
                  "decision_type": "gate", "action": "would_skip", "reason": "Budget cap"}
                 for run in ("one", "one", "two")]
    summary = summarize_day(decisions)
    assert summary.total_runs == 2
    assert summary.gate_skip == 3
    assert summary.skip_reasons["Budget cap"] == 3
    path = tmp_path / f"shadow-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"
    path.write_text("\n".join(json.dumps(d) for d in decisions) + "\n[]\nnull\n{broken\n")
    assert len(parse_log_file(path)) == 3
    assert "**Gate skipped:** 3" in compare_report(tmp_path, days=1)


def test_idle_runs_are_logged_and_counted(tmp_path):
    runner = ShadowRunner("mini", tmp_path / "logs", BudgetTracker(tmp_path / "budget"))
    with patch.object(runner, "evaluate_dispatch", return_value=[]), \
         patch.object(runner, "evaluate_all_gates", return_value=[]):
        runner.run()
        runner.run()
    decisions = parse_log_file(runner._log_file())
    assert len(decisions) == 2
    assert summarize_day(decisions).total_runs == 2


def test_report_output_cannot_be_written_into_published_checkout(tmp_path, monkeypatch):
    from cluster.control_plane.shadow_compare import main

    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr("sys.argv", ["shadow_compare", "--log-dir", str(tmp_path),
                                     "--output", str(root / "shadow-report.md")])
    with pytest.raises(ValueError):
        main()
    assert not (root / "shadow-report.md").exists()


def test_shadow_logs_are_private_and_budget_is_read_only(tmp_path, monkeypatch):
    budget = tmp_path / "unused-budget"
    monkeypatch.setenv("STORYPOOL_DATA_DIR", str(budget))
    runner = ShadowRunner("mini", tmp_path / "logs")
    runner._write_decision(ShadowDecision("2026-10-18T12:00:00Z", "gate", "would_skip", "budget", "mini"))
    assert not budget.exists()
    assert stat.S_IMODE(runner._log_file().stat().st_mode) == 0o600
    assert json.loads(runner._log_file().read_text())["run_id"] == runner.run_id


def test_existing_log_permissions_are_tightened(tmp_path):
    runner = ShadowRunner("mini", tmp_path / "logs", BudgetTracker(tmp_path / "budget"))
    runner._log_file().touch(mode=0o644)
    runner._write_decision(ShadowDecision("2026-10-18T12:00:00Z", "gate", "would_skip", "budget", "mini"))
    assert stat.S_IMODE(runner._log_file().stat().st_mode) == 0o600


def test_log_file_symlink_cannot_write_to_published_checkout(tmp_path):
    root = Path(__file__).resolve().parents[2]
    runner = ShadowRunner("mini", tmp_path / "logs", BudgetTracker(tmp_path / "budget"))
    runner._log_file().symlink_to(root / "index.html")
    with pytest.raises(ValueError):
        runner._write_decision(ShadowDecision("2026-10-18T12:00:00Z", "gate", "would_skip", "budget", "mini"))


def test_published_checkout_rejects_runtime_state_and_symlink_alias(tmp_path):
    root = Path(__file__).resolve().parents[2]
    alias = tmp_path / "checkout-alias"
    alias.symlink_to(root, target_is_directory=True)
    for path in (root / "shadow", alias / "shadow"):
        with pytest.raises(ValueError):
            BudgetTracker(path)
        with pytest.raises(ValueError):
            ShadowRunner("mini", path)


def test_pages_excludes_operational_docs_and_code():
    root = Path(__file__).resolve().parents[2]
    config = (root / "_config.yml").read_text()
    for path in ("cluster", "docs/design", "docs/research", ".config", ".local"):
        assert f"  - {path}\n" in config
    # A deny-only config cannot opt private paths back into the Pages build.
    parsed = yaml.safe_load(config)
    assert set(parsed) == {"exclude"}
    assert isinstance(parsed["exclude"], list)
    assert all(isinstance(path, str) for path in parsed["exclude"])
    assert {"cluster", "docs/design", "docs/research", ".config", ".local"} <= set(parsed["exclude"])


def test_control_plane_docs_stay_outside_public_docs():
    root = Path(__file__).resolve().parents[2]
    private_docs = root / "cluster" / "control_plane" / "_docs"
    for name in ("storypool-control-plane.md", "storypool-control-plane-research.md"):
        assert (private_docs / name).is_file()
        assert not list((root / "docs").rglob(name))


@pytest.mark.parametrize("name", ["install-shadow-mini.sh", "install-shadow-nucbox.sh"])
def test_installers_have_no_destructive_or_auth_setup_advice(name):
    path = Path(__file__).resolve().parents[1] / "control_plane" / "install" / name
    text = path.read_text()
    for command in ("rm -rf", "sudo", "gh auth login"):
        assert command not in text
    subprocess.run(["bash", "-n", str(path)], check=True)
