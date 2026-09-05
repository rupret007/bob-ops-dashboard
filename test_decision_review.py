"""Exact public review receipts; no GitHub, provider, or owner data reads."""
import json
import unittest
from urllib.parse import parse_qs, urlsplit

from board_meta import decision_review_identity, decision_review_item, review_decision_href


ITEM = {
    "id": "fixture-check",
    "title": "Review this fixture",
    "detail": "Read the full boundary before choosing.\nNo deployment or external send.",
    "risk": "high",
    "kind": "owner-gate",
}
STAMP = "2026-09-05T01:22:00-05:00"


class DecisionReviewTests(unittest.TestCase):
    def test_canonical_fields_preserve_exact_context_and_ignore_extras(self):
        self.assertEqual(decision_review_item({**ITEM, "private_extra": "not copied"}), ITEM)
        expected = [ITEM[key] for key in ("id", "title", "detail", "risk", "kind")]
        self.assertEqual(json.loads(decision_review_identity(ITEM)), expected)
        self.assertNotIn("private_extra", decision_review_identity({**ITEM, "private_extra": "not copied"}))

    def test_defaults_only_apply_to_missing_optional_fields(self):
        item = {"id": "fixture", "title": "Review", "risk": "low"}
        self.assertEqual(decision_review_item(item), {**item, "detail": "", "kind": "ops"})
        for field in ("detail", "kind"):
            self.assertIsNone(decision_review_item({**item, field: None}))
        self.assertIsNone(decision_review_item({"id": "fixture", "title": "Review"}))

    def test_every_published_context_change_invalidates_identity(self):
        before = decision_review_identity(ITEM)
        for key, value in {"id": "another", "title": "Changed", "detail": "Changed", "risk": "low", "kind": "ops"}.items():
            with self.subTest(key=key):
                self.assertNotEqual(before, decision_review_identity({**ITEM, key: value}))
        self.assertEqual(before, decision_review_identity({**ITEM, "checked_at": "later"}))

    def test_untrusted_ids_and_coercion_are_rejected(self):
        for ident in (None, 3, "", "x" * 65, " x", "x\n", "x/y", "é", "x<script>"):
            with self.subTest(ident=ident):
                self.assertIsNone(decision_review_item({**ITEM, "id": ident}))
        for value in (None, [], 3, "fixture"):
            self.assertIsNone(decision_review_item(value))
            self.assertEqual(decision_review_identity(value), "")

    def test_risk_and_kind_are_strict(self):
        for risk in ("HIGH", "High", " low", "unknown", None, 1, []):
            with self.subTest(risk=risk):
                self.assertIsNone(decision_review_item({**ITEM, "risk": risk}))
        for kind in ("", " ", "x" * 65, "é", "line\nbreak", "x\x7f", None):
            self.assertIsNone(decision_review_item({**ITEM, "kind": kind}))

    def test_text_is_not_truncated_and_uses_browser_utf16_limits(self):
        for key, limit in (("title", 160), ("detail", 2000)):
            self.assertIsNotNone(decision_review_item({**ITEM, key: "a" * limit}))
            self.assertIsNone(decision_review_item({**ITEM, key: "a" * (limit + 1)}))
            self.assertIsNotNone(decision_review_item({**ITEM, key: "🎸" * (limit // 2)}))
            self.assertIsNone(decision_review_item({**ITEM, key: "🎸" * (limit // 2 + 1)}))
            for bad in (None, 5, "broken\ud800", "null\x00", "escape\x1b", "del\x7f"):
                self.assertIsNone(decision_review_item({**ITEM, key: bad}))
        for title in ("", " \t\r\n", "one\ntwo", "one\ttwo", "one\rtwo", "\ufeff", "\u00a0\ufeff"):
            self.assertIsNone(decision_review_item({**ITEM, "title": title}))
        self.assertIsNotNone(decision_review_item({**ITEM, "title": "\u0085"}))
        self.assertIsNotNone(decision_review_item({**ITEM, "detail": "one\r\n\ttwo"}))

    def test_url_contains_complete_decoded_receipt_and_owner_boundary(self):
        item = {**ITEM, "detail": 'More than seventy-two chars: ' + 'exact & <text> "🎸" ' * 10}
        for verb in ("APPROVE", "HOLD", "DENY"):
            result = urlsplit(review_decision_href(verb, item, STAMP))
            self.assertEqual((result.scheme, result.netloc, result.path), ("https", "github.com", "/rupret007/bob-ops-dashboard/issues/new"))
            query = parse_qs(result.query)
            self.assertEqual(query["title"], [f"BOB-{verb}: fixture-check"])
            body = query["body"][0]
            self.assertIn("Reviewed public detail:\n" + item["detail"], body)
            self.assertIn("reviewed_snapshot: " + STAMP, body)
            self.assertIn("reviewed_item: " + decision_review_identity(item), body)
            self.assertIn("High-risk still needs the draft shown in chat before acting.", body)
            self.assertIn("re-check current work before acting.", body)

    def test_explicit_timezone_and_real_calendar_dates_required(self):
        for stamp in (STAMP, "2024-02-29T23:59:59.123456Z", "2026-09-05T06:22:00+00:00"):
            self.assertTrue(review_decision_href("HOLD", ITEM, stamp))
        for stamp in (None, 4, "", "2026-09-05", "2026-09-05T06:22:00", "2026-02-29T00:00:00Z", "2026-09-31T00:00:00Z", "0000-01-01T00:00:00Z", "2026-09-05T24:00:00Z", "2026-09-05T06:22:60Z", "2026-09-05T06:22:00+01:70", "2026-09-05T06:22:00+24:00", "2026-09-05T06:22:00.1234567Z", STAMP + "\n"):
            with self.subTest(stamp=stamp):
                self.assertEqual(review_decision_href("HOLD", ITEM, stamp), "")

    def test_long_encoded_url_fails_closed_without_partial_receipt(self):
        item = {**ITEM, "detail": "界" * 2000}
        self.assertIsNotNone(decision_review_item(item))
        self.assertEqual(review_decision_href("APPROVE", item, STAMP), "")
        for verb in (None, "approve", " APPROVE", "MERGE", 1):
            self.assertEqual(review_decision_href(verb, ITEM, STAMP), "")
        self.assertEqual(review_decision_href("APPROVE", {**ITEM, "risk": "unknown"}, STAMP), "")


if __name__ == "__main__":
    unittest.main()
