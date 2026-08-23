#!/usr/bin/env python3
"""Fail-closed checks for first-class Abilities / Controls / Features."""
from __future__ import annotations

import unittest

from board_meta import (
    CONTROL_ACTIONS,
    FIRST_CLASS_IDS,
    drop_leftover_verify,
    first_class_sections,
    merge_first_class,
)


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

    def test_no_otp_or_unlock_copy(self):
        blob = str(first_class_sections()).lower()
        for bad in ("unlock", "otp", "6-digit", "one-time", "sha256", "localstorage gate"):
            self.assertNotIn(bad, blob)
        self.assertNotIn("jeffstory007@gmail.com", blob)

    def test_control_actions_have_no_ask_code(self):
        self.assertEqual(CONTROL_ACTIONS, frozenset({"refresh-hint", "open-repo", "mark-reviewed"}))
        self.assertNotIn("ask-code", CONTROL_ACTIONS)

    def test_no_fake_order_or_send_buttons(self):
        for sec in first_class_sections():
            for p in sec["projects"]:
                act = p.get("control_action")
                if act:
                    self.assertIn(act, CONTROL_ACTIONS)
                notes = (p.get("notes") or "").lower()
                if "domino" in notes or ("andrea" in notes and "text" in notes):
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
        self.assertIn("Possession of the public URL", blob)
        self.assertNotIn("verified device", blob.lower())
        self.assertNotIn("Verified UI", blob)

    def test_security_features_from_pr1_survive_without_unlock(self):
        blob = str(first_class_sections())
        self.assertIn("html.escape", blob)
        self.assertIn("safeHref", blob)
        self.assertIn("pollSeq", blob)
        self.assertIn("decideBusy", blob)
        self.assertNotIn("unlockBusy", blob)
        self.assertNotIn("64-hex", blob)

    def test_drop_leftover_verify_fail_closed(self):
        status = {
            "generated_at": "x",
            "verify": {
                "email": "jeffstory007@gmail.com",
                "sha256": "aa" * 32,
                "exp": 1,
            },
        }
        self.assertTrue(drop_leftover_verify(status))
        self.assertNotIn("verify", status)
        self.assertFalse(drop_leftover_verify(status))
        self.assertFalse(drop_leftover_verify(None))
        self.assertFalse(drop_leftover_verify("nope"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
