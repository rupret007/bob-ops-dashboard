#!/usr/bin/env python3
"""Fail-closed checks for first-class Abilities / Controls / Features."""
from __future__ import annotations

import unittest

from board_meta import CONTROL_ACTIONS, FIRST_CLASS_IDS, first_class_sections, merge_first_class


class BoardMetaTests(unittest.TestCase):
    def test_three_first_class_ids_in_order(self):
        ids = [s["id"] for s in first_class_sections()]
        self.assertEqual(ids, list(FIRST_CLASS_IDS))

    def test_no_secrets_or_csone_paths(self):
        blob = str(first_class_sections()).lower()
        for bad in ("ghp_", "github_pat_", "-----begin", "csone.cisco", "/customers/"):
            self.assertNotIn(bad, blob)
        self.assertIn("no secrets", blob)
        self.assertIn("no csone", blob)

    def test_allowlist_mentioned_not_widened(self):
        blob = str(first_class_sections())
        self.assertIn("jeffstory007@gmail.com", blob)
        self.assertNotIn("@gmail.com", blob.replace("jeffstory007@gmail.com", ""))

    def test_no_fake_order_or_send_buttons(self):
        for sec in first_class_sections():
            for p in sec["projects"]:
                act = p.get("control_action")
                if act:
                    self.assertIn(act, CONTROL_ACTIONS)
                notes = (p.get("notes") or "").lower()
                if "domino" in notes or "andrea" in notes and "text" in notes:
                    self.assertNotIn("control_action", p)

    def test_merge_prepends_and_dedupes(self):
        old = [{"id": "abilities", "title": "stale"}, {"id": "live-shipping", "title": "Live"}]
        out = merge_first_class(old)
        self.assertEqual(out[0]["id"], "abilities")
        self.assertEqual(out[0]["title"], "Abilities")
        self.assertEqual([s["id"] for s in out if s["id"] == "abilities"], ["abilities"])
        self.assertEqual(out[-1]["id"], "live-shipping")

    def test_honest_authority(self):
        blob = str(first_class_sections())
        self.assertIn("rupret007", blob)
        self.assertIn("BOB-APPROVE", blob)

    def test_security_features_from_pr1_survive(self):
        blob = str(first_class_sections())
        self.assertIn("html.escape", blob)
        self.assertIn("safeHref", blob)
        self.assertIn("unlockBusy", blob)
        self.assertIn("pollSeq", blob)
        self.assertIn("64-hex", blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
