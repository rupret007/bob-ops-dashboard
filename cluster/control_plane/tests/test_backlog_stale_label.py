import tempfile
from pathlib import Path
from unittest import mock

from cluster.control_plane import backlog


def _pr(sha, label):
    return {"number": 5, "headRefOid": sha, "isDraft": False, "labels": [{"name": label}],
            "statusCheckRollup": [], "title": "t"}


def test_stale_karen_label_is_requeued_but_current_one_is_not():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / ("rupret007_demo_5_" + "b" * 12 + "_KARENFAIL.json")).write_text("{}")
        for sha, expected in (("a" * 40, [5]), ("b" * 40, [])):
            with mock.patch.object(backlog, "_local_verdict_dir", return_value=d), \
                    mock.patch.object(backlog, "get_open_prs", return_value=[_pr(sha, "karen-fail")]):
                got = [p["pr"] for p in backlog.label_queued_prs(["rupret007/demo"], dry_run=True)]
                assert got == expected
