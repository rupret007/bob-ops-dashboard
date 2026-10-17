import unittest
from datetime import datetime
from unittest import mock

from live.aggregator import _dedupe_prs, _throughput_counts, apply_karen_to_prs
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")


class ThroughputTests(unittest.TestCase):
    def setUp(self):
        import live.aggregator as agg

        agg._THROUGHPUT_CACHE = {"at": 0.0, "owner": "", "data": {}}

    def test_dedupe_repo_number(self):
        rows = [
            {"number": 1, "repository": {"name": "bob-brain"}},
            {"number": 1, "repository": {"name": "bob-brain"}},
            {"number": 2, "repository": {"name": "bob-brain"}},
        ]
        self.assertEqual(2, len(_dedupe_prs(rows)))

    def test_throughput_owner_search_once_per_day(self):
        fixed = datetime(2026, 10, 16, 12, 0, tzinfo=CT).timestamp()

        def clock():
            return fixed

        calls = []

        def fake_merged(cmd, **kwargs):
            calls.append(cmd)
            return '[{"number":1,"repository":{"name":"bob-brain"}},{"number":2,"repository":{"name":"webjam"}}]'

        def fake_open(cmd, **kwargs):
            calls.append(cmd)
            return '[{"number":9,"repository":{"name":"bob-brain"}}]'

        def dispatch(cmd, **kw):
            if any(str(a).startswith("--created=") for a in cmd):
                return fake_open(cmd, **kw)
            return fake_merged(cmd, **kw)

        with mock.patch("live.aggregator.subprocess.check_output") as co:
            co.side_effect = dispatch
            out = _throughput_counts("rupret007", clock=clock)
        self.assertEqual(2, out["merged_today"])
        self.assertEqual(1, out["opened_today"])
        merged_cmds = [c for c in calls if any("--merged-at=" in str(a) for a in c)]
        self.assertEqual(2, len(merged_cmds))
        self.assertTrue(all("rupret007" in str(c) for c in merged_cmds))
        out2 = _throughput_counts("rupret007", clock=clock)
        self.assertEqual(out["merged_today"], out2["merged_today"])
        self.assertEqual(2, len(merged_cmds))

    def test_pipeline_merged_matches_throughput(self):
        import time

        def fake_fetch(repos, owner="rupret007", clock=time.time):
            return {
                "prs": [
                    {
                        "repo": "bob-brain",
                        "number": 1,
                        "title": "t",
                        "isDraft": False,
                        "headRefOid": "abcd1234",
                        "ci_state": "success",
                        "ci_failing": False,
                        "karen_pass": False,
                        "stage": "ci",
                    }
                ],
                "stages": {},
                "throughput": {
                    "merged_today": 45,
                    "opened_today": 3,
                    "merged_7d_avg": 10.0,
                    "opened_7d_avg": 0.0,
                    "gates_pass": 0,
                    "gates_fail": 0,
                },
                "fetched_at": clock(),
            }

        data = fake_fetch([], clock=time.time)
        apply_karen_to_prs(data["prs"], [], {"default": "bob-brain"})
        from live.aggregator import _stage_counts

        stages = _stage_counts(data["prs"], data["throughput"]["merged_today"])
        self.assertEqual(45, stages["merged_today"])
        self.assertEqual(45, data["throughput"]["merged_today"])


class KarenStageTests(unittest.TestCase):
    def test_karen_ready_fail(self):
        prs = [
            {
                "repo": "bob-brain",
                "number": 10,
                "isDraft": False,
                "headRefOid": "deadbeef",
                "ci_state": "success",
                "ci_failing": False,
            }
        ]
        gates = [
            {"pr": 10, "sha": "deadbeef", "verdict": "REQUESTED"},
        ]
        apply_karen_to_prs(prs, gates, {"default": "bob-brain"})
        self.assertEqual("karen", prs[0]["stage"])

        gates.append({"pr": 10, "sha": "deadbeef", "verdict": "PASS"})
        apply_karen_to_prs(prs, gates, {"default": "bob-brain"})
        self.assertEqual("ready", prs[0]["stage"])

        gates.append({"pr": 10, "sha": "deadbeef", "verdict": "FAIL"})
        apply_karen_to_prs(prs, gates, {"default": "bob-brain"})
        self.assertEqual("karen_fail", prs[0]["stage"])


if __name__ == "__main__":
    unittest.main()
