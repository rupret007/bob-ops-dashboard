"""Usage rendering stays honest and public-safe for incomplete snapshots."""
import json
import unittest

from board_meta import normalize_usage_machines, usage_machines_chart_bars, usage_machines_html


def snapshot(**fields):
    return {"days": [{"date": "2026-10-16", **fields}]}


class UsageRendererTests(unittest.TestCase):
    def test_zero_measurements_remain_visible(self):
        data = snapshot(
            codex={"total_tokens": 0}, gemini={"tokens": 0, "usd": 0},
            cursor={"tokens": 0}, claude={"tokens": 0}, minimax={"tokens": 0},
            merged_prs={"count": 0},
            machines={"mini": {"load1": 0, "mem_used_gb": 0, "swap_used_gb": 0, "disk_free_gb": 0}},
        )
        html = usage_machines_html(data)
        self.assertIn("0 tok · $0.00", html)
        for label in ("Cursor", "Claude", "MiniMax"):
            self.assertIn(f"<th>{label}</th><td>0 tok", html)
        self.assertIn("<th>Merged PRs</th><td>0 · unknown tok/PR", html)
        self.assertIn("load 0 · mem 0.0G · swap 0.0G · disk free 0G", html)

    def test_missing_tokens_are_not_inferred_from_known_spend(self):
        for provider, label in (("cursor", "Cursor"), ("claude", "Claude"), ("minimax", "MiniMax")):
            with self.subTest(provider=provider):
                html = usage_machines_html(snapshot(**{provider: {"tokens": None, "usd": 1}}))
                self.assertIn(f"<th>{label}</th><td>unknown tok", html)
                self.assertNotIn(f"<th>{label}</th><td>0 tok", html)
        html = usage_machines_html(snapshot(merged_prs={"count": 1, "tokens_per_pr": 0}))
        self.assertIn("1 · 0 tok/PR", html)

    def test_invalid_numeric_values_render_unknown_without_exceptions(self):
        invalid = (None, True, False, "bad", "1", "", [], {}, -1, float("nan"), float("inf"), -float("inf"), 10**1000)
        for value in invalid:
            with self.subTest(value=repr(value)[:40]):
                data = snapshot(
                    codex={"total_tokens": value, "output_tokens": value},
                    gemini={"tokens": value, "usd": value}, cursor={"tokens": value, "usd": 1},
                    merged_prs={"count": value},
                    machines={"mini": {"load1": value, "mem_used_gb": value, "disk_free_gb": value}},
                )
                clean = normalize_usage_machines(data)
                self.assertEqual(clean["days"][0]["display"], {
                    "codex_total_tokens": "unknown", "gemini_tokens": "unknown",
                    "gemini_usd": "unknown", "merged_prs_count": "unknown",
                })
                self.assertIsNone(usage_machines_chart_bars(clean["days"])[0]["value"])
                html = usage_machines_html(data)
                self.assertIn("unknown total", html)
                self.assertIn("unknown tok · $unknown", html)
                self.assertIn("load unknown · mem unknown", html)
                json.dumps(clean, allow_nan=False)

    def test_chart_distinguishes_zero_and_missing_from_positive_values(self):
        days = [{"date": str(i), "codex": {"total_tokens": n}}
                for i, n in enumerate((0, None, 1, 100, "bad", float("inf"), True, 1e308))]
        bars = usage_machines_chart_bars(days)
        self.assertEqual([b["height_pct"] for b in bars[:7]], [0, 0, 4, 4, 0, 0, 0])
        self.assertEqual(bars[-1]["height_pct"], 100)

    def test_malformed_containers_do_not_crash(self):
        for value in (None, 1, True, "bad", {}, []):
            with self.subTest(value=value):
                self.assertIsInstance(usage_machines_html({"days": value, "hosts": value}), str)
                self.assertIsInstance(usage_machines_html(snapshot(codex=value, machines={"mini": value})), str)

    def test_malformed_embedded_scorecards_do_not_crash(self):
        for value in (None, 1, True, "bad", [], [1], {}, {"24h": [1]}, {"24h": {"providers": 1}}):
            with self.subTest(value=value):
                data = snapshot(codex={"total_tokens": 1})
                data["scorecards"] = {"v": "scorecards/v1", "windows": value, "generated_at": 1e308}
                self.assertIn("1 total", usage_machines_html(data))
                self.assertEqual(normalize_usage_machines(data)["scorecards"]["generated_at"], "")
        data["scorecards"] = {
            "v": "scorecards/v1", "generated_at": 10**1000,
            "windows": {"24h": {"providers": {"codex": {"tokens": {"value": 10**1000}}}}},
        }
        self.assertIn("no data", usage_machines_html(data))

    def test_public_normalization_copies_only_known_aggregate_fields(self):
        marker = "private-metadata-must-not-publish"
        data = snapshot(
            codex={"total_tokens": 5, "path": marker},
            codex_by_host={"mini": {"total_tokens": 5, "path": marker}, marker: {"total_tokens": 1}},
            gemini={"tokens": 5, "usd": 0, "detail": marker},
            lanes={"codex": 0, marker: 5}, cursor={"tokens": 0, "source": marker},
            machines={"mini": {"load1": 0, "path": marker}, marker: {"load1": 0}},
            merged_prs={"count": 1, "tokens_per_pr": 0, "detail": marker},
        )
        data["hosts"] = ["mini", marker]
        self.assertNotIn(marker, json.dumps(normalize_usage_machines(data)))
        self.assertNotIn(marker, usage_machines_html(data))
