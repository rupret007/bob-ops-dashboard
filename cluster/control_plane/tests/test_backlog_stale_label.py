import tempfile
from pathlib import Path
from unittest import mock

from cluster.control_plane import backlog
from cluster.control_plane.local_karen.verdict import Verdict


def _pr(sha, label):
    return {"number": 5, "headRefOid": sha, "isDraft": False, "labels": [{"name": label}],
            "statusCheckRollup": [], "title": "t"}


def test_stale_karen_label_is_requeued_but_current_one_is_not():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        verdict = Verdict("rupret007", "demo", 5, "b" * 40, False,
                          "Reviewed functional defect", "2026-10-18T12:00:00+00:00", "codex", "low", .9,
                          [], [{"reviewer": "codex", "verdict": "FAIL", "reason": "Reviewed functional defect",
                                "findings": {"functional_issues": ["file.py:12 returns wrong value"]}}])
        (d / ("rupret007_demo_5_" + "b" * 12 + "_KARENFAIL.json")).write_text(verdict.to_json())
        for sha, expected in (("a" * 40, [5]), ("b" * 40, [])):
            with mock.patch.object(backlog, "_local_verdict_dir", return_value=d), \
                    mock.patch.object(backlog, "get_open_prs", return_value=[_pr(sha, "karen-fail")]):
                got = [p["pr"] for p in backlog.label_queued_prs(["rupret007/demo"], dry_run=True)]
                assert got == expected


def test_empty_artifact_and_draft_cannot_suppress_or_trigger_review(tmp_path):
    (tmp_path / ("rupret007_demo_5_" + "b" * 12 + "_KARENFAIL.json")).write_text("{}")
    pr = _pr("b" * 40, "karen-fail")
    with mock.patch.object(backlog, "_local_verdict_dir", return_value=tmp_path), \
            mock.patch.object(backlog, "get_open_prs", return_value=[pr]):
        assert len(backlog.label_queued_prs(["rupret007/demo"], dry_run=True)) == 1
        pr["isDraft"] = True
        assert backlog.label_queued_prs(["rupret007/demo"], dry_run=True) == []
