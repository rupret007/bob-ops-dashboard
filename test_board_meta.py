#!/usr/bin/env python3
"""Fail-closed checks for first-class Abilities / Decisions / How-this-board."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from board_meta import (
    CONTROL_ACTIONS,
    FIRST_CLASS_IDS,
    age_gate_agents,
    attention_rank,
    board_content_fingerprint,
    compact_signal,
    decision_href,
    drop_leftover_verify,
    first_class_sections,
    is_quiet_lane,
    merge_first_class,
    parse_agents_blob,
    pending_risk_rank,
    pick_tip_ci,
    presentation,
    resolve_agents,
    short_note,
    sort_pending,
    split_pending,
    status_from_fetch,
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

    def test_soft_paint_and_agent_honesty_copy(self):
        blob = str(first_class_sections())
        self.assertIn("not on every 15m", blob)
        self.assertIn("pageshow", blob)
        self.assertIn("Never invent Running", blob)
        self.assertIn("Actions cadence is ~15m", blob)
        self.assertIn("real GitHub links", blob)

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
        self.assertEqual(
            compact_signal({"release": "v0.26.0", "ci": {"conclusion": "failure"}, "open_prs": 1}),
            "CI fail",
        )
        self.assertEqual(
            compact_signal({"release": "v0.26.0", "ci": {"conclusion": "in_progress"}}),
            "CI running",
        )
        self.assertEqual(
            compact_signal({"release": "v0.26.0", "ci": {"conclusion": "queued"}}),
            "CI running",
        )
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

    def test_status_from_fetch_empty_ci_is_green_not_yellow(self):
        green_empty = {"accessible": True, "open_prs": 0, "ci": None}
        self.assertEqual(status_from_fetch(green_empty), "green")
        self.assertEqual(status_from_fetch({"accessible": True, "open_prs": 0, "ci": {}}), "green")
        self.assertEqual(
            status_from_fetch({"accessible": True, "open_prs": 0, "ci": {"conclusion": "success"}}),
            "green",
        )
        self.assertEqual(
            status_from_fetch({"accessible": True, "open_prs": 0, "ci": {"conclusion": "failure"}}),
            "red",
        )
        self.assertEqual(status_from_fetch({"accessible": True, "open_prs": 2, "ci": None}), "yellow")
        self.assertEqual(status_from_fetch({"accessible": False}), "parked")
        self.assertEqual(status_from_fetch({"accessible": False}, override="yellow"), "yellow")
        self.assertEqual(status_from_fetch(green_empty, jeff_gate=True), "jeff-gate")
        self.assertEqual(status_from_fetch(None), "parked")
        failing = {"accessible": True, "open_prs": 1, "ci": {"conclusion": "failure"}}
        self.assertEqual(status_from_fetch(failing, jeff_gate=True), "red")
        self.assertEqual(
            status_from_fetch(
                {"accessible": True, "open_prs": 0, "ci": {"conclusion": "in_progress"}},
                jeff_gate=True,
            ),
            "yellow",
        )
        self.assertEqual(
            status_from_fetch(
                {"accessible": True, "open_prs": 0, "ci": {"conclusion": "success"}},
                jeff_gate=True,
            ),
            "jeff-gate",
        )
        self.assertEqual(status_from_fetch(failing, override="parked"), "parked")
        self.assertEqual(
            status_from_fetch(
                {"accessible": True, "open_prs": 0, "ci": {"conclusion": "timed_out"}}
            ),
            "red",
        )

    def test_pick_tip_ci_does_not_skip_in_progress(self):
        runs = [
            {
                "head_branch": "feat",
                "status": "completed",
                "conclusion": "failure",
                "name": "PR CI",
                "head_sha": "fffffff",
            },
            {
                "head_branch": "master",
                "status": "in_progress",
                "conclusion": None,
                "name": "WebJam CI",
                "head_sha": "abc1234dead",
                "created_at": "2026-08-23T05:50:00Z",
            },
            {
                "head_branch": "master",
                "status": "completed",
                "conclusion": "failure",
                "name": "WebJam CI",
                "head_sha": "5280686",
            },
        ]
        picked = pick_tip_ci(runs, "master")
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked["conclusion"], "in_progress")
        self.assertEqual(picked["sha"], "abc1234")
        self.assertEqual(pick_tip_ci(runs, "main"), None)
        self.assertIsNone(pick_tip_ci(None, "master"))

    def test_decision_href_is_safe_and_stable(self):
        from urllib.parse import parse_qs, urlparse

        url = decision_href("APPROVE", "dashboard-refresh", "Force dashboard refresh + push")
        self.assertTrue(
            url.startswith("https://github.com/rupret007/bob-ops-dashboard/issues/new?")
        )
        q = parse_qs(urlparse(url).query)
        self.assertEqual(q["title"][0], "BOB-APPROVE: dashboard-refresh")
        self.assertIn("rupret007", q["body"][0])
        self.assertNotIn("at:", q["body"][0])
        self.assertEqual(decision_href("DELETE", "dashboard-refresh", "nope"), "")
        self.assertEqual(decision_href("APPROVE", "bad id", "nope"), "")
        self.assertEqual(decision_href("APPROVE", "", "nope"), "")

    def test_split_pending_keeps_unknown_risk_visible(self):
        attn, low = split_pending(
            [
                {"id": "a", "risk": "low"},
                {"id": "b", "risk": "high"},
                {"id": "c", "risk": "mystery"},
                {"id": "d", "risk": "medium"},
            ]
        )
        self.assertEqual([p["id"] for p in attn], ["b", "d", "c"])
        self.assertEqual([p["id"] for p in low], ["a"])
        self.assertEqual(split_pending(None), ([], []))

    def test_stale_or_untimestamped_agents_never_stay_running(self):
        now_dt = datetime(2026, 8, 23, 5, 26, tzinfo=timezone.utc)
        now = now_dt.timestamp()
        stale_ts = "2026-08-22T20:45:00-05:00"  # hours older than `now`
        fresh_ts = (now_dt - timedelta(minutes=10)).isoformat()
        stale = parse_agents_blob(
            {
                "agents": [
                    {"id": "codex", "name": "Codex", "state": "running", "detail": "PID 1", "checked_at": stale_ts},
                    {"id": "cursor", "name": "Cursor", "state": "running", "detail": "app", "checked_at": stale_ts},
                    {"id": "claude", "name": "Claude", "state": "installed", "detail": "app", "checked_at": stale_ts},
                ]
            }
        )
        gated = age_gate_agents(stale, now=now)
        self.assertEqual([a["state"] for a in gated], ["unknown", "unknown", "unknown"])
        self.assertTrue(all("probe stale" in a["detail"] for a in gated))
        self.assertEqual(len(gated), 3)

        untimestamped = age_gate_agents(
            [{"id": "codex", "state": "running", "detail": "PID 9", "checked_at": None}],
            now=now,
        )
        self.assertEqual(untimestamped[0]["state"], "unknown")

        fresh = age_gate_agents(
            [
                {"id": "codex", "state": "running", "detail": "PID 2", "checked_at": fresh_ts},
                {"id": "cursor", "state": "idle", "detail": "app", "checked_at": fresh_ts},
                {"id": "claude", "state": "installed", "detail": "app", "checked_at": fresh_ts},
            ],
            now=now,
        )
        self.assertEqual([a["state"] for a in fresh], ["running", "idle", "installed"])

    def test_future_checked_at_is_not_treated_as_live(self):
        now = datetime(2026, 8, 23, 5, 26, tzinfo=timezone.utc).timestamp()
        gated = age_gate_agents(
            [
                {
                    "id": "codex",
                    "state": "running",
                    "detail": "time travel",
                    "checked_at": "2026-08-24T12:00:00-05:00",
                }
            ],
            now=now,
        )
        self.assertEqual(gated[0]["state"], "unknown")

    def test_resolve_agents_does_not_fall_through_to_older_running(self):
        now = datetime(2026, 8, 23, 5, 26, tzinfo=timezone.utc).timestamp()
        stale = {
            "agents": [
                {"id": "codex", "state": "running", "detail": "seed", "checked_at": "2026-08-22T20:45:00-05:00"},
                {"id": "cursor", "state": "running", "detail": "seed", "checked_at": "2026-08-22T20:45:00-05:00"},
                {"id": "claude", "state": "installed", "detail": "seed", "checked_at": "2026-08-22T20:45:00-05:00"},
            ]
        }
        older_running = [
            {"id": "codex", "state": "running", "detail": "old", "checked_at": "2026-08-22T18:00:00-05:00"},
            {"id": "cursor", "state": "running", "detail": "old", "checked_at": "2026-08-22T18:00:00-05:00"},
            {"id": "claude", "state": "running", "detail": "old", "checked_at": "2026-08-22T18:00:00-05:00"},
        ]
        agents, src = resolve_agents(
            file_texts=[("file:agents-status.json", json.dumps(stale))],
            previous=older_running,
            now=now,
        )
        self.assertTrue(src.endswith("stale->unknown"))
        self.assertEqual([a["state"] for a in agents], ["unknown", "unknown", "unknown"])
        self.assertNotIn("running", [a["state"] for a in agents])

    def test_parse_agents_drops_extra_ids_and_redacts_secrets(self):
        parsed = parse_agents_blob(
            {
                "agents": [
                    {"id": "codex", "state": "idle", "detail": "bearer token xyz", "checked_at": "x"},
                    {"id": "ghost", "state": "running", "detail": "invented"},
                ]
            }
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual([a["id"] for a in parsed], ["codex", "cursor", "claude"])
        self.assertEqual(parsed[0]["detail"], "detail redacted")
        self.assertEqual(parsed[0]["state"], "idle")
        self.assertEqual(parsed[1]["state"], "unknown")
        self.assertIsNone(parse_agents_blob("not-json"))
        self.assertIsNone(parse_agents_blob(None))

    def test_board_fingerprint_ignores_refresh_timestamps(self):
        a = {
            "generated_at": "one",
            "generated_at_display": "Mon",
            "refresh_started_ms": 1,
            "agents_source": "file:x",
            "pending": [{"id": "text-send", "risk": "high", "title": "Send", "detail": "x"}],
            "agents": [{"id": "codex", "state": "unknown", "detail": "none"}],
            "sections": [{"id": "live-shipping", "title": "Live", "projects": [{"name": "WebJam", "status": "jeff-gate"}]}],
            "fetched_repos": ["webjam"],
            "decisions": [{"id": "old"}],
        }
        b = dict(a)
        b["generated_at"] = "two"
        b["generated_at_display"] = "Tue"
        b["refresh_started_ms"] = 99
        b["agents_source"] = "previous:stale->unknown"
        self.assertEqual(board_content_fingerprint(a), board_content_fingerprint(b))
        c = dict(a)
        c["pending"] = [{"id": "text-send", "risk": "high", "title": "Send", "detail": "changed"}]
        self.assertNotEqual(board_content_fingerprint(a), board_content_fingerprint(c))

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
