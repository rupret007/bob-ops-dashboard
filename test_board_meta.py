#!/usr/bin/env python3
"""Fail-closed checks for first-class Abilities / Decisions / How-this-board."""
from __future__ import annotations

import unittest

from board_meta import (
    CONTROL_ACTIONS,
    FIRST_CLASS_IDS,
    attention_rank,
    compact_signal,
    drop_leftover_verify,
    first_class_sections,
    is_quiet_lane,
    merge_first_class,
    pending_risk_rank,
    presentation,
    short_note,
    sort_pending,
    visible_chip,
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
        for bad in ("unlock", "otp", "6-digit", "one-time", "sha256", "localstorage gate", "how to ask"):
            self.assertNotIn(bad, blob)
        self.assertNotIn("jeffstory007@gmail.com", blob)
        names = [p["name"] for s in first_class_sections() for p in s["projects"]]
        self.assertNotIn("Ask Bob for a new code", names)
        self.assertNotIn("Email Unlock codes", names)

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

    def test_merge_ops_first_then_abilities_features(self):
        old = [
            {"id": "abilities", "title": "stale"},
            {"id": "live-shipping", "title": "Live"},
            {"id": "parked", "title": "Parked"},
            {"id": "active-agents", "title": "Agents"},
        ]
        out = merge_first_class(old)
        ids = [s["id"] for s in out]
        self.assertEqual(ids[0], "controls")
        self.assertEqual(out[0]["title"], "Decisions")
        self.assertEqual(ids[1], "live-shipping")
        self.assertEqual(ids[2], "active-agents")
        self.assertEqual(ids[-2], "abilities")
        self.assertEqual(ids[-1], "features")
        self.assertEqual([s["id"] for s in out if s["id"] == "abilities"], ["abilities"])

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

    def test_visible_chip_hides_section_type_labels(self):
        self.assertIsNone(visible_chip({"chip": "Control", "status": "green"}))
        self.assertIsNone(visible_chip({"chip": "Feature"}))
        self.assertIsNone(visible_chip({"chip": "Ability"}))
        self.assertEqual(visible_chip({"chip": "Jeff-gate"}), "Jeff-gate")
        self.assertEqual(visible_chip({"chip": "Green"}), "Green")
        self.assertIsNone(visible_chip({}))

    def test_presentation_hierarchy(self):
        self.assertEqual(presentation("controls"), "pending")
        self.assertEqual(presentation("active-agents"), "pulse")
        self.assertEqual(presentation("live-shipping"), "primary")
        self.assertEqual(presentation("cisco"), "secondary")
        self.assertEqual(presentation("parked"), "secondary")
        self.assertEqual(presentation("abilities"), "footer")
        self.assertEqual(presentation("features"), "footer")

    def test_compact_signal_skips_sha_and_zero_prs(self):
        self.assertEqual(compact_signal({"release": "v0.26.0", "tip_sha": "abc1234", "open_prs": 0}), "v0.26.0")
        self.assertEqual(compact_signal({"open_prs": 1, "tip_sha": "abc1234"}), "1 open PR")
        self.assertEqual(compact_signal({"open_prs": 4}), "4 open PRs")
        self.assertIsNone(compact_signal({"tip_sha": "abc1234", "open_prs": 0, "product_sha": "deadbee"}))
        self.assertIsNone(compact_signal({"ci": {"name": "CI", "conclusion": "success"}, "open_prs": 0}))
        self.assertEqual(compact_signal({"ci": {"conclusion": "failure"}}), "CI fail")
        self.assertIsNone(compact_signal({}))
        self.assertIsNone(compact_signal(None))

    def test_quiet_lane_and_attention(self):
        self.assertTrue(is_quiet_lane({"status": "green"}))
        self.assertFalse(is_quiet_lane({"status": "jeff-gate"}))
        self.assertLess(attention_rank({"status": "jeff-gate"}), attention_rank({"status": "green"}))
        self.assertLess(attention_rank({"status": "red"}), attention_rank({"status": "yellow"}))
        self.assertEqual(attention_rank(None), 9)

    def test_pending_sorts_high_risk_first(self):
        self.assertLess(pending_risk_rank({"risk": "high"}), pending_risk_rank({"risk": "low"}))
        out = sort_pending(
            [
                {"id": "a", "risk": "low"},
                {"id": "b", "risk": "high"},
                {"id": "c", "risk": "medium"},
            ]
        )
        self.assertEqual([p["id"] for p in out], ["b", "c", "a"])
        self.assertEqual(sort_pending(None), [])

    def test_short_note_phone_safe(self):
        self.assertEqual(short_note("Quiet green unless CI says otherwise."), "Quiet green unless CI says otherwise.")
        long = "PR #21 merged (docs on 5ca6ba5). Release stays v0.26.0 until Jeff names v0.27. Exploratory click-through Jeff-gated."
        out = short_note(long, 88)
        self.assertLessEqual(len(out), 91)
        self.assertTrue(out.endswith("..."))
        self.assertNotIn("\n", out)
        self.assertEqual(short_note(""), "")
        self.assertEqual(short_note(None), "")

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
