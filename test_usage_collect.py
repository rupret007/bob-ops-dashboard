#!/usr/bin/env python3
"""Usage & machines collector baseline tests (fixtures only — no live gh)."""

from __future__ import annotations

import errno
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import mock_open, patch

from usage_collect import (
    TokenTotals,
    _read_mem_linux,
    _sum_optional_tokens,
    aggregate_gemini_spend,
    aggregate_provider_usage,
    collect_codex_for_day,
    collect_host_snapshot,
    last_codex_token_usage,
    load_merged_usage,
    merge_host_snapshots,
    parse_gemini_spend_line,
)

ROOT = Path(__file__).resolve().parent
FIX = ROOT / "tests" / "fixtures" / "usage_machines_baseline"

BASELINE_CODEX_MINI = {
    "2026-10-12": 182_000_000,
    "2026-10-13": 312_000_000,
    "2026-10-14": 203_000_000,
    "2026-10-15": 249_000_000,
    "2026-10-16": 697_000_000,
}
BASELINE_CODEX_OUTPUT_OCT16 = 3_480_000
BASELINE_GEMINI_OCT16 = (11_600_000, 42.38)
BASELINE_MERGED_PRS = {
    "2026-10-12": 130,
    "2026-10-13": 110,
    "2026-10-14": 72,
    "2026-10-15": 23,
    "2026-10-16": 75,
}


def _gh_baseline(_owner: str, day: str) -> int:
    return int(BASELINE_MERGED_PRS.get(day, 0))


class UsageCollectBaselineTests(unittest.TestCase):
    def test_last_token_count_per_session(self):
        session = FIX / "mini/codex/sessions/2026/10/16/session-a.jsonl"
        totals = last_codex_token_usage(session)
        self.assertIsNotNone(totals)
        self.assertEqual(697_000_000, totals.total_tokens)
        self.assertEqual(3_480_000, totals.output_tokens)

    def test_codex_daily_totals_match_baseline(self):
        sessions = FIX / "mini/codex/sessions"
        for day, expect in BASELINE_CODEX_MINI.items():
            got = collect_codex_for_day(sessions, day)
            self.assertEqual(expect, got.total_tokens, day)
        oct16 = collect_codex_for_day(sessions, "2026-10-16")
        self.assertEqual(BASELINE_CODEX_OUTPUT_OCT16, oct16.output_tokens)

    def test_gemini_spend_oct16(self):
        agg = aggregate_gemini_spend(FIX / "lane-wt/logs/gemini-spend.txt")
        row = agg["2026-10-16"]
        self.assertEqual(BASELINE_GEMINI_OCT16[0], int(row["tokens"]))
        self.assertAlmostEqual(BASELINE_GEMINI_OCT16[1], float(row["usd"]), places=2)

    def test_provider_usage_lane_counts(self):
        lanes = aggregate_provider_usage(FIX / "lane-wt/logs/provider-usage.tsv")
        self.assertEqual(1, lanes["2026-10-16"]["codex"])
        self.assertEqual(3, lanes["2026-10-16"]["cursor"])

    def test_host_snapshot_merged_prs_baseline(self):
        snap = collect_host_snapshot(
            host="mini",
            sessions_root=FIX / "mini/codex/sessions",
            lane_logs=FIX / "lane-wt/logs",
            days=list(BASELINE_CODEX_MINI.keys()),
            gh_search=_gh_baseline,
        )
        by_day = {r["date"]: r for r in snap["days"]}
        for day, count in BASELINE_MERGED_PRS.items():
            self.assertEqual(count, by_day[day]["merged_prs"]["count"], day)
        self.assertEqual(
            BASELINE_GEMINI_OCT16[0],
            int(by_day["2026-10-16"]["gemini"]["tokens"]),
        )

    def test_merge_host_snapshots_codex_by_host(self):
        mini = collect_host_snapshot(
            host="mini",
            sessions_root=FIX / "mini/codex/sessions",
            lane_logs=FIX / "lane-wt/logs",
            days=["2026-10-16"],
            gh_search=_gh_baseline,
            collect_merged=False,
        )
        merged = merge_host_snapshots([mini])
        day = merged["days"][-1]
        self.assertEqual(697_000_000, day["codex"]["total_tokens"])
        self.assertEqual(697_000_000, day["codex_by_host"]["mini"]["total_tokens"])

    def test_grok_bot_without_source_is_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = [
                collect_host_snapshot(
                    host=host, sessions_root=root / "sessions", lane_logs=root / "logs",
                    days=["2026-10-16"], collect_merged=False,
                )
                for host in ("mini", "nucbox")
            ]
        for snapshot in [*snapshots, merge_host_snapshots(snapshots)]:
            grok = snapshot["days"][0]["grok_bot"]
            self.assertEqual({"status": "unknown", "source": None}, grok)
            self.assertNotEqual(0, grok["status"])

    def test_parse_gemini_line_rejects_garbage(self):
        self.assertIsNone(parse_gemini_spend_line("not a log line"))

    def test_token_totals_add(self):
        a = TokenTotals(total_tokens=1, output_tokens=2)
        b = TokenTotals(total_tokens=3, output_tokens=4)
        self.assertEqual(4, (a + b).total_tokens)

    def test_mem_linux_converts_kb_with_1024_squared(self):
        meminfo = (
            "MemTotal:       2097152 kB\n"
            "MemAvailable:   1048576 kB\n"
            "SwapTotal:       2097152 kB\n"
            "SwapFree:        1048576 kB\n"
        )
        with patch("builtins.open", mock_open(read_data=meminfo)):
            mem_used_gb, swap_used_gb = _read_mem_linux()
        self.assertEqual(1.0, mem_used_gb)
        self.assertEqual(1.0, swap_used_gb)

    def test_mem_without_proc_meminfo_returns_none(self):
        with patch("builtins.open", side_effect=FileNotFoundError(errno.ENOENT, "No such file")):
            mem_used_gb, swap_used_gb = _read_mem_linux()
        self.assertIsNone(mem_used_gb)
        self.assertIsNone(swap_used_gb)

    def test_codex_missing_optional_token_fields_stay_none(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session.jsonl"
            session.write_text(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {"total_tokens": 42},
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            totals = last_codex_token_usage(session)
        self.assertIsNotNone(totals)
        self.assertEqual(42, totals.total_tokens)
        self.assertIsNone(totals.cached_tokens)
        self.assertIsNone(totals.output_tokens)
        self.assertIsNone(totals.reasoning_tokens)
        row = totals.to_dict()
        self.assertIsNone(row["cached_tokens"])
        self.assertIsNone(row["output_tokens"])
        self.assertIsNone(row["reasoning_tokens"])

    def test_host_snapshot_machines_only_on_latest_day(self):
        snap = collect_host_snapshot(
            host="mini",
            sessions_root=FIX / "mini/codex/sessions",
            lane_logs=FIX / "lane-wt/logs",
            days=list(BASELINE_CODEX_MINI.keys()),
            gh_search=_gh_baseline,
            collect_merged=False,
        )
        by_day = {r["date"]: r for r in snap["days"]}
        latest = max(BASELINE_CODEX_MINI.keys())
        for day in BASELINE_CODEX_MINI:
            machines = by_day[day].get("machines")
            if day == latest:
                self.assertIsInstance(machines, dict)
                self.assertIn("mini", machines)
            else:
                self.assertIsNone(machines)


class PublishedUsageRegressionTests(unittest.TestCase):
    """Exercise the host JSON -> publication loader -> normalized status path."""

    def publish(self, snapshots):
        from board_meta import normalize_usage_machines

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for snapshot in snapshots:
                (root / f"usage-host-{snapshot['host']}.json").write_text(
                    json.dumps(snapshot), encoding="utf-8"
                )
            return normalize_usage_machines(load_merged_usage(root))

    def snapshot(self, host, metrics, day="2026-10-16", machines=None):
        return {"host": host, "days": [
            {"date": day, "codex": metrics, "machines": machines}
        ]}

    def test_optional_sum_requires_every_part(self):
        for a, b in ((None, None), (None, 7), (7, None), (0, None), (None, 0)):
            with self.subTest(a=a, b=b):
                self.assertIsNone(_sum_optional_tokens(a, b))
        self.assertEqual(0, _sum_optional_tokens(0, 0))
        self.assertEqual(12, _sum_optional_tokens(5, 7))

    def test_published_missing_and_null_fields_remain_unknown(self):
        for metrics in (
            {"total_tokens": 42},
            {"total_tokens": 42, "cached_tokens": None,
             "output_tokens": None, "reasoning_tokens": None},
        ):
            with self.subTest(metrics=metrics):
                row = self.publish([self.snapshot("mini", metrics)])["days"][0]
                self.assertEqual(42, row["codex"]["total_tokens"])
                for field in ("cached_tokens", "output_tokens", "reasoning_tokens"):
                    self.assertIsNone(row["codex"][field], field)

    def test_published_default_token_fields_remain_unknown(self):
        row = self.publish([
            self.snapshot("mini", TokenTotals(total_tokens=42).to_dict())
        ])["days"][0]
        for field in ("cached_tokens", "output_tokens", "reasoning_tokens"):
            self.assertIsNone(row["codex"][field], field)

    def test_published_mixed_hosts_remain_unknown_in_either_order(self):
        known = {"total_tokens": 42, "cached_tokens": 7,
                 "output_tokens": 8, "reasoning_tokens": 9}
        for unknown in ({"total_tokens": 10}, {"total_tokens": 10,
                        "cached_tokens": None, "output_tokens": None,
                        "reasoning_tokens": None}):
            snapshots = [self.snapshot("mini", known), self.snapshot("nucbox", unknown)]
            for ordered in (snapshots, list(reversed(snapshots))):
                with self.subTest(unknown=unknown, order=[s["host"] for s in ordered]):
                    row = self.publish(ordered)["days"][0]
                    self.assertEqual(52, row["codex"]["total_tokens"])
                    for field in ("cached_tokens", "output_tokens", "reasoning_tokens"):
                        self.assertIsNone(row["codex"][field], field)
                        self.assertEqual(known[field], row["codex_by_host"]["mini"][field])

    def test_published_known_counts_and_explicit_zero_are_preserved(self):
        for hosts in (("mini",), ("mini", "nucbox")):
            for value in (0, 7):
                metrics = {"total_tokens": 42, "cached_tokens": value,
                           "output_tokens": value, "reasoning_tokens": value}
                row = self.publish([self.snapshot(host, metrics) for host in hosts])["days"][0]
                self.assertEqual(42 * len(hosts), row["codex"]["total_tokens"])
                for field in ("cached_tokens", "output_tokens", "reasoning_tokens"):
                    self.assertEqual(value * len(hosts), row["codex"][field], field)

    def test_published_mixed_sessions_remain_unknown(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                day_dir = root / "sessions/2026/10/16"
                day_dir.mkdir(parents=True)
                usages = [{"total_tokens": 42, "cached_input_tokens": 7,
                           "output_tokens": 8, "reasoning_output_tokens": 9},
                          {"total_tokens": 10}]
                if reverse:
                    usages.reverse()
                for i, usage in enumerate(usages):
                    (day_dir / f"session-{i}.jsonl").write_text(json.dumps({
                        "type": "event_msg", "payload": {"type": "token_count",
                        "info": {"total_token_usage": usage}}
                    }) + "\n", encoding="utf-8")
                with patch("usage_collect.collect_machine_snapshot", return_value={}):
                    snapshot = collect_host_snapshot(
                        host="mini", sessions_root=root / "sessions", lane_logs=root / "logs",
                        days=["2026-10-16"], collect_merged=False,
                    )
                row = self.publish([snapshot])["days"][0]
                self.assertEqual(52, row["codex"]["total_tokens"])
                for field in ("cached_tokens", "output_tokens", "reasoning_tokens"):
                    self.assertIsNone(row["codex"][field], field)

    def test_published_disjoint_host_days_null_historical_machines(self):
        snapshots = [
            self.snapshot("mini", {"total_tokens": 42}, day="2026-10-15",
                          machines={"mini": {"mem_used_gb": 2.0}}),
            self.snapshot("nucbox", {"total_tokens": 10}, day="2026-10-16",
                          machines={"nucbox": {"mem_used_gb": 3.0}}),
        ]
        for ordered in (snapshots, list(reversed(snapshots))):
            rows = self.publish(ordered)["days"]
            self.assertEqual("2026-10-15", rows[0]["date"])
            self.assertIsNone(rows[0]["machines"])
            self.assertEqual({"nucbox": {"mem_used_gb": 3.0}}, rows[1]["machines"])

    def test_committed_unknown_measurements_are_null(self):
        data = json.loads((ROOT / "usage-machines.json").read_text(encoding="utf-8"))
        for row in data["days"]:
            for metrics in (row["codex"], row["codex_by_host"]["mini"]):
                self.assertIsNone(metrics["cached_tokens"], row["date"])
                self.assertIsNone(metrics["reasoning_tokens"], row["date"])
        latest = max(data["days"], key=lambda row: row["date"])
        self.assertIsNone(latest["machines"]["mini"]["mem_used_gb"])
        self.assertIsNone(latest["machines"]["mini"]["swap_used_gb"])

    def test_published_unknown_machine_values_have_no_unit(self):
        from board_meta import usage_machines_html

        for value, expected in ((None, "unknown"), (0, "0.0G"), (1.5, "1.5G")):
            blob = self.publish([self.snapshot("mini", {"total_tokens": 42}, machines={
                "mini": {"mem_used_gb": value, "swap_used_gb": value}
            })])
            html = usage_machines_html(blob)
            self.assertIn(f"mem {expected} · swap {expected} · disk free", html)
            self.assertNotIn("unknownG", html)


class UsageMachinesPanelTests(unittest.TestCase):
    def test_usage_html_renders_no_data_for_optional_providers(self):
        from board_meta import normalize_usage_machines, usage_machines_html

        blob = {
            "v": "usage-machines/v1",
            "days": [
                {
                    "date": "2026-10-16",
                    "codex": {"total_tokens": 100, "output_tokens": 1},
                    "gemini": None,
                    "cursor": None,
                    "claude": None,
                    "minimax": None,
                }
            ],
        }
        html = usage_machines_html(normalize_usage_machines(blob))
        self.assertIn("no data", html)
        self.assertIn("Codex", html)
        self.assertNotIn("/home/", html)

    def test_grok_bot_placeholder_with_legacy_or_empty_history(self):
        from board_meta import normalize_usage_machines, usage_machines_html

        for blob in ({}, {"days": [{"date": "2026-10-16"}]}):
            normalized = normalize_usage_machines(blob)
            for day in normalized["days"]:
                self.assertEqual({"status": "unknown", "source": None}, day["grok_bot"])
            html = usage_machines_html(normalized)
            self.assertIn("Grok Bot (conserve-only)", html)
            self.assertIn("unknown — no usage source; conserve-only", html)

    def test_committed_baseline_json_matches_codex(self):
        path = ROOT / "usage-machines.json"
        self.assertTrue(path.is_file(), "usage-machines.json must ship baseline aggregates")
        data = json.loads(path.read_text(encoding="utf-8"))
        by_day = {d["date"]: d for d in data.get("days") or []}
        self.assertEqual(697_000_000, by_day["2026-10-16"]["codex_by_host"]["mini"]["total_tokens"])

    def test_committed_usage_machines_not_copied_to_historical_days(self):
        path = ROOT / "usage-machines.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        days = data.get("days") or []
        self.assertGreaterEqual(len(days), 2)
        latest = max(str(d.get("date") or "") for d in days)
        for row in days:
            day = str(row.get("date") or "")
            machines = row.get("machines")
            if day == latest:
                self.assertIsInstance(machines, dict)
            else:
                self.assertIsNone(machines)

    def test_mac_mem_none_renders_unknown_in_panel(self):
        from board_meta import normalize_usage_machines, usage_machines_html

        blob = {
            "v": "usage-machines/v1",
            "days": [
                {
                    "date": "2026-10-16",
                    "codex": {"total_tokens": 1, "output_tokens": None},
                    "machines": {
                        "mini": {
                            "load1": 1.0,
                            "mem_used_gb": None,
                            "swap_used_gb": None,
                            "disk_free_gb": 10.0,
                        }
                    },
                }
            ],
        }
        html = usage_machines_html(normalize_usage_machines(blob))
        self.assertIn("mem unknown", html)
        self.assertIn("swap unknown", html)
        self.assertIn("unknown out", html)

    def test_codex_optional_tokens_render_unknown_in_panel(self):
        from board_meta import normalize_usage_machines, usage_machines_html

        blob = {
            "v": "usage-machines/v1",
            "days": [
                {
                    "date": "2026-10-16",
                    "codex": {
                        "total_tokens": 100,
                        "cached_tokens": None,
                        "output_tokens": None,
                        "reasoning_tokens": None,
                    },
                }
            ],
        }
        html = usage_machines_html(normalize_usage_machines(blob))
        self.assertIn("cached unknown", html)
        self.assertIn("reasoning unknown", html)


if __name__ == "__main__":
    unittest.main()
