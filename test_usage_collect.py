#!/usr/bin/env python3
"""Usage & machines collector baseline tests (fixtures only — no live gh)."""

from __future__ import annotations

import json
import unittest
import tempfile
from pathlib import Path

from usage_collect import (
    TokenTotals,
    aggregate_gemini_spend,
    aggregate_provider_usage,
    collect_codex_for_day,
    collect_host_snapshot,
    last_codex_token_usage,
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


if __name__ == "__main__":
    unittest.main()
