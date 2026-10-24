"""Lane repair admission must preserve the existing human-review risk policy."""
from unittest.mock import MagicMock, patch

import pytest

from cluster.control_plane import dispatcher, fix_worker
from cluster.control_plane.coordination import ClaimClient
from cluster.control_plane.job_ledger import JobLedger
from cluster.control_plane.local_karen.verdict import Verdict


HEAD = "a" * 40


def failed_verdict(tier="low", security_issues=None):
    return Verdict(
        owner="owner", repo="project", pr_number=7, head_sha=HEAD,
        passed=False, reason="The reviewed report loses existing output rows.",
        timestamp="2026-10-21T00:00:00+00:00", reviewer="karen", tier=tier,
        confidence=0.9, check_results=[],
        review_results=[dict(
            reviewer="codex", verdict="FAIL",
            reason="Preserve existing report rows and verify their values.",
            findings=dict(functional_issues=["The report drops existing rows."],
                          security_issues=security_issues or []),
        )],
    )


@pytest.mark.parametrize("tier", ["high", "security", "critical"])
def test_lane_findings_hold_existing_human_review_tiers(tier):
    with patch.object(fix_worker, "load_verdict", return_value=failed_verdict(tier)), \
         patch.object(fix_worker.KarenConfig, "load", return_value=object()):
        assert fix_worker.findings_for_pr("owner/project", 7, HEAD) == []


def test_low_risk_reviewed_functional_findings_remain_available():
    with patch.object(fix_worker, "load_verdict", return_value=failed_verdict()), \
         patch.object(fix_worker.KarenConfig, "load", return_value=object()):
        findings = fix_worker.findings_for_pr("owner/project", 7, HEAD)
    assert any("report drops existing rows" in item for item in findings)


def test_security_findings_hold_a_low_tier_verdict_too():
    verdict = failed_verdict(security_issues=["An existing access boundary is missing."])
    with patch.object(fix_worker, "load_verdict", return_value=verdict), \
         patch.object(fix_worker.KarenConfig, "load", return_value=object()):
        assert fix_worker.findings_for_pr("owner/project", 7, HEAD) == []


def test_live_lane_refuses_human_review_tier_before_claim_or_job(tmp_path):
    ledger = JobLedger(tmp_path / "jobs")
    instance = dispatcher.Dispatcher(
        "nucbox", tmp_path / "dispatch", job_ledger=ledger, budget_tracker=MagicMock()
    )
    with patch.object(instance, "check_machine_guards", return_value=(True, "ok", {})), \
         patch.object(instance, "select_provider", return_value="codex"), \
         patch.object(dispatcher, "REPOS", ["owner/project"]), \
         patch.object(dispatcher, "_get_open_prs", return_value=[dict(number=7, headRefOid=HEAD)]), \
         patch.object(dispatcher, "_get_active_lanes", return_value={}), \
         patch.object(dispatcher, "_has_codex_work_queued", return_value=False), \
         patch.object(fix_worker, "load_verdict", return_value=failed_verdict("security")), \
         patch.object(fix_worker.KarenConfig, "load", return_value=object()), \
         patch.object(dispatcher, "qualified_runtime") as runtime, \
         patch("cluster.control_plane.ci_review_refresh.repair_preflight", return_value="repair"), \
         patch("cluster.control_plane.node_control.is_draining", return_value=False), \
         patch.object(ClaimClient, "from_env", side_effect=AssertionError("No cluster access")) as client, \
         patch.object(dispatcher, "_spawn_worker") as spawn:
        decisions = instance.evaluate(live=True)
    assert decisions[0].action == "skip"
    assert decisions[0].extra["reason_code"] == "missing-reviewed-findings"
    runtime.assert_not_called()
    client.assert_not_called()
    spawn.assert_not_called()
    assert ledger._load_jobs() == []
