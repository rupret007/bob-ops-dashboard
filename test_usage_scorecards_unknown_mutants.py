"""Regression guards: unknown measurements must not collapse to zero."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from scorecards import build_scorecards, metric, normalize_scorecards, numeric, status, total
from usage_collect import _default_gh_merged_count, collect_host_snapshot, tokens_for_merged_prs

ROOT = Path(__file__).resolve().parent
FIX = ROOT / "tests" / "fixtures" / "usage_machines_baseline"
NOW = 1_700_000_000.0


class ScorecardNumericGuards(unittest.TestCase):
    def test_bool_values_are_not_numeric(self):
        self.assertIs(type(True), bool)
        self.assertFalse(numeric(True))
        self.assertFalse(numeric(False))
        for name in ("tokens", "usd", "karen_first_pass_pct"):
            self.assertEqual("no data", status(name, True))
            self.assertEqual("no data", status(name, False))

    def test_negative_token_spend_stays_unknown(self):
        records = [
            dict(kind="spend", provider="codex", at=NOW - 1, tokens=-5, usd=1.0),
        ]
        card = build_scorecards(records, now=NOW)["windows"]["24h"]["providers"]["codex"]
        self.assertIsNone(card["tokens"]["value"])
        self.assertEqual("no data", card["tokens"]["status"])
        self.assertIsNone(total("tokens", records, "tokens")["value"])

    def test_saved_metric_without_value_stays_unknown_not_zero(self):
        data = build_scorecards([], now=NOW)
        token = data["windows"]["24h"]["providers"]["codex"]["tokens"]
        token.pop("value", None)
        data["windows"]["24h"]["providers"]["codex"]["tokens"] = token
        clean = normalize_scorecards(data)
        self.assertIsNone(clean["windows"]["24h"]["providers"]["codex"]["tokens"]["value"])

    def test_metric_helper_does_not_default_unknown_to_zero(self):
        item = metric("tokens", None, 0, 0)
        self.assertIsNone(item["value"])
        self.assertNotEqual(0, item["value"])


class MergedPrUnknownGuards(unittest.TestCase):
    def test_tokens_for_merged_prs_unknown_count(self):
        out = tokens_for_merged_prs(None, {}, None, None)
        self.assertIsNone(out["count"])
        self.assertIsNone(out["tokens_total"])

    def test_gh_search_failure_is_unknown_not_zero(self):
        with patch("subprocess.check_output", side_effect=OSError("offline")):
            self.assertIsNone(_default_gh_merged_count("rupret007", "2026-10-16"))

    def test_host_snapshot_gh_failure_merged_count_unknown(self):
        def fail(_owner: str, _day: str) -> None:
            return None

        snap = collect_host_snapshot(
            host="mini",
            sessions_root=FIX / "mini/codex/sessions",
            lane_logs=FIX / "lane-wt/logs",
            days=["2026-10-16"],
            gh_search=fail,
        )
        merged = snap["days"][0]["merged_prs"]
        self.assertIsNone(merged["count"])
        self.assertNotEqual(0, merged["count"])


class UsagePanelUnknownGuards(unittest.TestCase):
    def test_gemini_none_tokens_and_spend_not_zero_display(self):
        from board_meta import normalize_usage_machines, usage_machines_html

        blob = normalize_usage_machines(
            {
                "v": "usage-machines/v1",
                "days": [
                    {
                        "date": "2026-10-16",
                        "codex": {"total_tokens": 1, "output_tokens": None},
                        "gemini": {"tokens": None, "usd": None},
                    }
                ],
            }
        )
        html = usage_machines_html(blob)
        self.assertIn("unknown tok", html)
        self.assertIn("$unknown", html)
        self.assertNotIn("$0.00", html)
        self.assertNotIn("0 tok", html)

    def test_merged_prs_unknown_count_not_shown_as_zero(self):
        from board_meta import normalize_usage_machines, usage_machines_html

        blob = normalize_usage_machines(
            {
                "v": "usage-machines/v1",
                "days": [
                    {
                        "date": "2026-10-16",
                        "codex": {"total_tokens": 1, "output_tokens": None},
                        "merged_prs": {"count": None, "tokens_total": None, "tokens_per_pr": None},
                    }
                ],
            }
        )
        html = usage_machines_html(blob)
        self.assertNotIn("<th>Merged PRs</th><td>0", html)
        self.assertNotIn("Merged PRs</th><td>0 ·", html)


if __name__ == "__main__":
    unittest.main()
