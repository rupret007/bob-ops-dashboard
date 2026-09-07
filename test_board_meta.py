#!/usr/bin/env python3
"""Fail-closed checks for first-class Abilities / Decisions / How-this-board."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from board_meta import (
    CONTROL_ACTIONS,
    FIRST_CLASS_IDS,
    LATEST_VS_SOURCE_SIGNAL,
    REQUIRED_PUBLIC_REPOS,
    TYPE_TAB_IDS,
    TYPE_TAB_LABELS,
    age_gate_agents,
    agent_url_from_fields,
    attention_rank,
    board_content_fingerprint,
    compact_signal,
    compact_unknown_mac_probes,
    decision_href,
    detect_linear_pr_stack,
    drop_leftover_verify,
    extract_agent_url,
    extract_cloud_agents_from_prs,
    first_class_sections,
    focus_key,
    glance_html,
    glance_status,
    is_ci_noise,
    is_draft_pr,
    is_type_tab,
    is_quiet_lane,
    is_unexecuted_run,
    incomplete_public_collections,
    lane_hrefs,
    latest_release_url_from_repo,
    merge_cloud_agents,
    merge_first_class,
    parse_agents_blob,
    parse_cloud_agents,
    pending_risk_rank,
    pick_open_pr,
    pick_tip_ci,
    presentation,
    prune_closed_parked_prs,
    public_probe_detail,
    public_high_level_ci,
    pulls_url_from_repo,
    resolve_agents,
    safe_actions_url,
    safe_agent_url,
    safe_game_url,
    safe_pr_url,
    safe_pulls_url,
    safe_release_tag,
    safe_release_url,
    safe_repo_url,
    release_matches_tip,
    signal_href,
    sha_matches_tip,
    short_note,
    sort_pending,
    split_pending,
    status_from_fetch,
    tab_id,
    tab_label,
    type_tab_ids_for,
    type_tabs_html,
    unknown_mac_probes_html,
    visible_chip,
    parse_coord_issue,
    public_coord,
    coord_signal,
    coord_pr_url,
    coord_review_signal,
    status_with_coord_review,
)


class BoardMetaTests(unittest.TestCase):
    def test_required_public_collection_fails_closed(self):
        complete = [
            {
                "full_name": spec,
                "accessible": True,
                "collection_complete": True,
            }
            for spec in REQUIRED_PUBLIC_REPOS
        ]
        self.assertEqual(incomplete_public_collections(complete), [])

        missing = complete[1:]
        self.assertEqual(
            incomplete_public_collections(missing),
            [REQUIRED_PUBLIC_REPOS[0]],
        )

        inaccessible = [dict(row) for row in complete]
        inaccessible[2]["accessible"] = False
        self.assertEqual(
            incomplete_public_collections(inaccessible),
            [REQUIRED_PUBLIC_REPOS[2]],
        )

        partial = [dict(row) for row in complete]
        partial[-1]["collection_complete"] = False
        self.assertEqual(
            incomplete_public_collections(partial),
            [REQUIRED_PUBLIC_REPOS[-1]],
        )

        self.assertEqual(
            incomplete_public_collections(None),
            list(REQUIRED_PUBLIC_REPOS),
        )

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

    def test_closed_parked_prs_are_not_presented_as_current_work(self):
        open_url = "https://github.com/rupret007/story-corner-shelf/pull/3"
        closed_url = "https://github.com/rupret007/RadDadSite/pull/6"
        sections = [
            {
                "id": "parked",
                "projects": [
                    {"name": "Open parked PR", "status": "parked", "url": open_url},
                    {"name": "Closed parked PR", "status": "parked", "url": closed_url},
                    {
                        "name": "Dynamic open parked PR",
                        "status": "parked",
                        "url": "https://github.com/rupret007/story-corner-shelf",
                        "open_pr_url": open_url,
                    },
                    {
                        "name": "Parked non-PR work",
                        "status": "parked",
                        "url": "https://github.com/rupret007/story-corner-shelf",
                    },
                ],
            }
        ]
        out = prune_closed_parked_prs(sections, [open_url])
        names = [p["name"] for p in out[0]["projects"]]
        self.assertEqual(
            names,
            ["Open parked PR", "Dynamic open parked PR", "Parked non-PR work"],
        )
        self.assertNotIn(closed_url, str(out))

        fail_closed = prune_closed_parked_prs(sections, [])
        self.assertEqual(
            [p["name"] for p in fail_closed[0]["projects"]],
            ["Parked non-PR work"],
        )

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
        self.assertIn("Agents unknown", blob)
        self.assertIn("Actions cadence is ~15m", blob)
        self.assertIn("real GitHub links", blob)
        self.assertIn("Pages / skipped helpers / this board's refresh publisher cannot hide a fail", blob)
        self.assertIn("cannot beat a success or become Open CI", blob)
        self.assertIn("not a failed poll", blob)
        self.assertIn("cannot rewind", blob)
        self.assertIn("stopPolling bumps pollSeq", blob)
        self.assertIn("Open agent", blob)
        self.assertIn("never invent", blob.lower())
        self.assertIn("open pr", blob.lower())
        self.assertIn("openBlank", blob)
        self.assertIn("Open repo", blob)
        self.assertIn("Open CI", blob)
        self.assertIn("Play game", blob)
        self.assertIn("Vault, StoryBoard, Show Night, and WebJam work together", blob)
        self.assertIn("Latest != source", blob)
        self.assertIn("/releases/latest", blob)
        names = [p["name"] for s in first_class_sections() for p in s["projects"]]
        self.assertIn("Music stack", names)
        self.assertIn("Type tabs", names)
        self.assertIn("Each GitHub type is its own tab", blob)
        type_tabs = next(p for s in first_class_sections() if s["id"] == "features" for p in s["projects"] if p["name"] == "Type tabs")
        self.assertLessEqual(len(type_tabs["notes"]), 88)

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

    def test_type_tabs_use_existing_section_ids_only(self):
        self.assertEqual(
            TYPE_TAB_IDS,
            (
                "controls",
                "live-shipping",
                "apps-utilities",
                "cisco",
                "messaging",
                "private-media",
                "parked",
            ),
        )
        self.assertEqual(TYPE_TAB_LABELS["live-shipping"], "Live")
        self.assertEqual(TYPE_TAB_LABELS["apps-utilities"], "Apps")
        self.assertEqual(TYPE_TAB_LABELS["cisco"], "Cisco")
        self.assertEqual(TYPE_TAB_LABELS["parked"], "Parked")
        self.assertEqual(tab_id("live-shipping"), "live-shipping")
        self.assertEqual(tab_id("music"), "")
        self.assertEqual(tab_id("javascript:alert(1)"), "")
        self.assertEqual(tab_id("abilities"), "")
        self.assertEqual(tab_label("messaging"), "Bob")
        self.assertFalse(is_type_tab("features"))
        self.assertTrue(is_type_tab("cisco"))

    def test_glance_status_is_one_short_line(self):
        sections = [
            {
                "id": "live-shipping",
                "projects": [
                    {"name": "WebJam", "status": "yellow"},
                    {"name": "Show Night", "status": "green"},
                ],
            },
            {"id": "cisco", "projects": [{"name": "AdoptIQ", "status": "red"}]},
        ]
        pending = [{"id": "adoptiq-live-cisco", "title": "AdoptIQ", "risk": "high"}]
        g = glance_status(pending, sections)
        self.assertEqual(g["text"], "AdoptIQ")
        self.assertEqual(g["tab"], "controls")
        self.assertEqual(g["focus"], "decision:adoptiq-live-cisco")
        g4 = glance_status(pending * 4, sections)
        self.assertEqual(g4["text"], "AdoptIQ")
        self.assertNotIn("more", g4["text"])
        quiet_live = glance_status([], sections)
        self.assertEqual(quiet_live["text"], "AdoptIQ is red")
        self.assertEqual(quiet_live["tab"], "cisco")
        self.assertEqual(quiet_live["focus"], "project:adoptiq")
        yellow_only = glance_status(
            [],
            [{"id": "live-shipping", "projects": [{"name": "WebJam", "status": "yellow"}]}],
        )
        self.assertEqual(yellow_only["text"], "WebJam needs a look")
        self.assertEqual(yellow_only["tab"], "live-shipping")
        self.assertEqual(yellow_only["focus"], "project:webjam")
        leftover_jeff = glance_status(
            [],
            [{"id": "apps-utilities", "projects": [{"name": "Door", "status": "jeff-gate"}]}],
        )
        self.assertEqual(leftover_jeff, {"text": "Quiet", "tab": ""})
        leftover_media = glance_status(
            [],
            [{
                "id": "private-media",
                "projects": [{
                    "name": "Private media",
                    "status": "jeff-gate",
                    "chip": "Owner-only",
                }],
            }],
        )
        self.assertEqual(leftover_media, {"text": "Quiet", "tab": ""})
        self.assertEqual(glance_status([], []), {"text": "Quiet", "tab": ""})
        honest_live = glance_status(
            [],
            [
                {
                    "id": "live-shipping",
                    "projects": [{"name": "Turdanoid", "status": "green"}],
                },
                {
                    "id": "cisco",
                    "projects": [{"name": "AdoptIQ", "status": "parked"}],
                },
                {
                    "id": "messaging",
                    "projects": [{"name": "Bob the Bot", "status": "parked"}],
                },
                {
                    "id": "apps-utilities",
                    "projects": [{"name": "Story Shelf", "status": "yellow"}],
                },
            ],
        )
        self.assertEqual(
            honest_live,
            {
                "text": "Story Shelf needs a look",
                "tab": "apps-utilities",
                "focus": "project:story-shelf",
            },
        )

        owner_hold_plus_work = glance_status(
            [],
            [{
                "id": "apps-utilities",
                "projects": [
                    {"name": "Door", "status": "jeff-gate"},
                    {"name": "TACTrack", "status": "yellow"},
                ],
            }],
        )
        self.assertEqual(owner_hold_plus_work["text"], "TACTrack needs a look")
        self.assertEqual(owner_hold_plus_work["focus"], "project:tactrack")

    def test_focus_keys_are_stable_and_fail_closed(self):
        self.assertEqual(focus_key("project", "Andrea NanoBot"), "project:andrea-nanobot")
        self.assertEqual(focus_key("decision", "adoptiq-live_cisco.1"), "decision:adoptiq-live_cisco.1")
        self.assertEqual(focus_key("project", "<script>"), "project:script")
        self.assertEqual(focus_key("project", "\U0001f525"), "")
        self.assertEqual(focus_key("decision", "../../escape"), "")
        self.assertEqual(focus_key("decision", "x" * 65), "")
        self.assertEqual(focus_key("unknown", "WebJam"), "")

        fallback = glance_status(
            [{"id": "../../escape", "title": "Do not focus", "risk": "high"}],
            [{"id": "live-shipping", "projects": [{"name": "WebJam", "status": "yellow"}]}],
        )
        self.assertEqual(fallback["text"], "WebJam needs a look")
        self.assertEqual(fallback["focus"], "project:webjam")

    def test_refresh_standing_is_current_jeff_gates(self):
        blob = Path(__file__).with_name("refresh.sh").read_text()
        start = blob.find("\nstanding = [")
        end = blob.find("\npending_out =", start)
        self.assertGreater(start, 0)
        self.assertGreater(end, start)
        standing = blob[start:end]
        for want in ("che-live-pull", "logic-keys-wavs", "adoptiq-live-cisco"):
            self.assertIn('"id": "' + want + '"', standing)
        for drop in (
            "webjam-exploratory",
            "ballbeacon-signing",
            "sliding-door-physical",
            "private-media-upload",
        ):
            self.assertNotIn(drop, standing)

    def test_glance_status_names_the_three_standing_gates(self):
        pending = [
            {
                "id": "che-live-pull",
                "title": "Che live pull",
                "kind": "jeff-gate",
                "risk": "low",
            },
            {
                "id": "logic-keys-wavs",
                "title": "Logic keys and WAVs",
                "kind": "jeff-gate",
                "risk": "low",
            },
            {
                "id": "adoptiq-live-cisco",
                "title": "AdoptIQ live Cisco readiness",
                "kind": "owner-live-gate",
                "risk": "high",
            },
        ]
        g = glance_status(pending, [])
        # sort_pending puts high-risk first; AdoptIQ title is 28 chars so it fits.
        # First screen names that one next action -- never a leftover yes-count.
        self.assertEqual(g["text"], "AdoptIQ live Cisco readiness")
        self.assertEqual(g["tab"], "controls")
        self.assertEqual(g["focus"], "decision:adoptiq-live-cisco")
        self.assertNotIn("more", g["text"])

    def test_type_tabs_html_skips_empty_decisions_and_invented_ids(self):
        sections = [
            {"id": "live-shipping", "title": "Live shipping"},
            {"id": "apps-utilities", "title": "Apps & utilities"},
            {"id": "cisco", "title": "Cisco work"},
            {"id": "messaging", "title": "Messaging / Bob infra"},
            {"id": "private-media", "title": "Private media"},
            {"id": "parked", "title": "Parked"},
            {"id": "abilities", "title": "Abilities"},
        ]
        self.assertEqual(
            type_tab_ids_for(sections, []),
            [
                "live-shipping",
                "apps-utilities",
                "cisco",
                "messaging",
                "private-media",
                "parked",
            ],
        )
        self.assertNotIn("controls", type_tab_ids_for(sections, [{"id": "x"}]))
        html = type_tabs_html(sections, [{"id": "x"}])
        self.assertIn('id="type-tabs"', html)
        self.assertIn('data-tab="live-shipping"', html)
        self.assertIn(">Live<", html)
        self.assertIn(">Apps<", html)
        self.assertIn(">Cisco<", html)
        self.assertIn(">Bob<", html)
        self.assertNotIn("music", html)
        self.assertNotIn('id="tab-controls"', html)
        self.assertNotIn(">Decisions<", html)
        self.assertNotIn("data-tab=\"abilities\"", html)
        self.assertNotIn('aria-selected="true"', html)
        glance = glance_html([{"id": "x"}], sections)
        self.assertIn("Pending", glance)
        self.assertIn('data-tab="controls"', glance)
        self.assertIn('aria-label="Next action"', glance)
        self.assertIn('aria-controls="controls"', glance)
        self.assertIn('data-focus-target="decision:x"', glance)
        self.assertNotIn("<", glance_status([{"id": "x"}], sections)["text"])

    def test_unknown_mac_probes_collapse_to_one_honest_line(self):
        unknown = [
            {"id": "codex", "state": "unknown", "detail": "No Mac probe in this clone"},
            {"id": "cursor", "state": "unknown", "detail": "No Mac probe in this clone"},
            {"id": "claude", "state": "unknown", "detail": "No Mac probe in this clone"},
        ]
        self.assertTrue(compact_unknown_mac_probes(unknown))
        self.assertTrue(compact_unknown_mac_probes([]))
        self.assertTrue(compact_unknown_mac_probes(None))
        stale_running = [
            {"id": "codex", "state": "unknown", "detail": "PID 1 · probe stale (>45m)"},
            {"id": "cursor", "state": "unknown"},
            {"id": "claude", "state": "unknown"},
        ]
        self.assertTrue(compact_unknown_mac_probes(stale_running))
        html = unknown_mac_probes_html(unknown)
        self.assertIn("Agents unknown", html)
        self.assertNotIn("Running", html)
        self.assertIn("No Mac probe in this clone", html)
        self.assertNotIn("probe-agents-status.sh", unknown_mac_probes_html())
        self.assertNotIn("/Users", unknown_mac_probes_html([
            {"id": "codex", "state": "unknown", "detail": "/Users/owner/private/agents-status.json"},
        ]))
        self.assertEqual(public_probe_detail("/Users/owner/secret"), "No live Mac probe")
        self.assertEqual(public_probe_detail("bearer token xyz"), "No live Mac probe")
        live = [
            {"id": "codex", "state": "running", "detail": "PID 2"},
            {"id": "cursor", "state": "unknown"},
            {"id": "claude", "state": "unknown"},
        ]
        self.assertFalse(compact_unknown_mac_probes(live))
        self.assertFalse(
            compact_unknown_mac_probes(
                [{"id": "cursor", "state": "idle", "detail": "Cursor.app"}]
            )
        )
        cloud_only = [
            {"id": "bc-11111111-1111-1111-1111-111111111111", "state": "unknown"}
        ]
        self.assertTrue(compact_unknown_mac_probes(cloud_only))

    def test_compact_signal_skips_sha_and_zero_prs(self):
        self.assertEqual(compact_signal({"release": "v0.26.0", "tip_sha": "abc1234", "open_prs": 0}), "v0.26.0")
        self.assertEqual(
            compact_signal(
                {
                    "release": "v0.26.0",
                    "release_sha": "4b52080",
                    "tip_sha": "27530d8",
                    "open_prs": 0,
                }
            ),
            LATEST_VS_SOURCE_SIGNAL,
        )
        self.assertEqual(
            compact_signal(
                {
                    "release": "v0.26.0",
                    "release_sha": "4b52080",
                    "tip_sha": "4b52080",
                    "open_prs": 0,
                }
            ),
            "v0.26.0",
        )
        self.assertEqual(
            compact_signal(
                {
                    "release": "v0.26.0",
                    "release_sha": "4b52080",
                    "tip_sha": "27530d8",
                    "open_prs": 1,
                }
            ),
            "1 open PR",
        )
        self.assertEqual(
            compact_signal(
                {
                    "release": "v0.26.0",
                    "release_sha": "4b52080",
                    "tip_sha": "27530d8",
                    "ci": {"conclusion": "failure"},
                }
            ),
            "CI fail",
        )
        self.assertIsNone(
            compact_signal(
                {
                    "private": True,
                    "release": "v9.9.9",
                    "release_sha": "deadbee",
                    "tip_sha": "abc1234",
                }
            )
        )
        self.assertEqual(compact_signal({"open_prs": 1, "tip_sha": "abc1234"}), "1 open PR")
        self.assertEqual(compact_signal({"open_prs": 4}), "4 open PRs")
        self.assertEqual(
            compact_signal({"release": "v0.26.0", "open_prs": 1}),
            "1 open PR",
        )
        stack = [
            {"number": 10, "url": "https://github.com/rupret007/repo/pull/10"},
            {"number": 11, "url": "https://github.com/rupret007/repo/pull/11"},
            {"number": 12, "url": "https://github.com/rupret007/repo/pull/12"},
        ]
        self.assertEqual(
            compact_signal({"release": "v1", "open_prs": 3, "open_pr_stack": stack}),
            "Stack #10 -> #11 -> #12",
        )
        self.assertEqual(
            compact_signal({"release": "v1", "open_prs": 4, "open_pr_stack": stack}),
            "4 open PRs",
        )
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
            "CI pending",
        )
        self.assertEqual(
            compact_signal({"release": "v0.26.0", "ci": {"conclusion": "pending"}}),
            "CI pending",
        )
        self.assertIsNone(compact_signal({}))
        self.assertIsNone(compact_signal(None))

    def test_open_prs_signal_is_a_tap_not_dead_text(self):
        one = {
            "url": "https://github.com/rupret007/StoryBoard",
            "open_prs": 1,
            "open_pr_url": "https://github.com/rupret007/StoryBoard/pull/8",
            "ci": {"conclusion": "success"},
        }
        self.assertEqual(compact_signal(one), "1 open PR")
        self.assertEqual(signal_href(one), "https://github.com/rupret007/StoryBoard/pull/8")
        many = {
            "url": "https://github.com/rupret007/story-corner-shelf",
            "open_prs": 4,
            "open_pr_url": "https://github.com/rupret007/story-corner-shelf/pull/1",
            "ci": {"conclusion": "success"},
        }
        self.assertEqual(compact_signal(many), "4 open PRs")
        self.assertEqual(signal_href(many), "https://github.com/rupret007/story-corner-shelf/pulls")
        stacked = {
            "url": "https://github.com/rupret007/StoryLiner",
            "open_prs": 2,
            "open_pr_stack": [
                {"number": 11, "url": "https://github.com/rupret007/StoryLiner/pull/11"},
                {"number": 12, "url": "https://github.com/rupret007/StoryLiner/pull/12"},
            ],
            "release": "v1.0.0",
            "ci": {"conclusion": "success"},
        }
        self.assertEqual(compact_signal(stacked), "Stack #11 -> #12")
        self.assertEqual(signal_href(stacked), "https://github.com/rupret007/StoryLiner/pulls")
        orphan = {"url": "https://github.com/rupret007/RadDadSite", "open_prs": 1}
        self.assertEqual(signal_href(orphan), "https://github.com/rupret007/RadDadSite/pulls")
        no_repo = {
            "open_prs": 4,
            "open_pr_url": "https://github.com/rupret007/story-corner-shelf/pull/1",
        }
        self.assertEqual(signal_href(no_repo), "")
        self.assertEqual(
            signal_href(
                {
                    "url": "https://github.com/rupret007/webjam",
                    "release": "v0.26.0",
                    "ci": {
                        "conclusion": "pending",
                        "html_url": "https://github.com/rupret007/webjam/actions/runs/9",
                    },
                }
            ),
            "https://github.com/rupret007/webjam/actions/runs/9",
        )
        self.assertEqual(
            signal_href({"release": "v0.26.0", "ci": {"conclusion": "success"}, "open_prs": 0}),
            "",
        )
        diverged = {
            "url": "https://github.com/rupret007/webjam",
            "release": "v0.26.0",
            "release_sha": "4b52080",
            "tip_sha": "27530d8",
            "open_prs": 0,
            "ci": {"conclusion": "success"},
        }
        self.assertEqual(compact_signal(diverged), LATEST_VS_SOURCE_SIGNAL)
        self.assertEqual(signal_href(diverged), "https://github.com/rupret007/webjam/releases/latest")
        matched = {
            "url": "https://github.com/rupret007/webjam",
            "release": "v0.26.0",
            "release_sha": "4b5208098981943df8ddaf1fac31aa36c15146bb",
            "tip_sha": "4b52080",
            "open_prs": 0,
            "ci": {"conclusion": "success"},
        }
        self.assertEqual(compact_signal(matched), "v0.26.0")
        self.assertEqual(signal_href(matched), "https://github.com/rupret007/webjam/releases/latest")
        self.assertEqual(signal_href(None), "")
        self.assertEqual(
            safe_pulls_url("https://github.com/rupret007/webjam/pulls"),
            "https://github.com/rupret007/webjam/pulls",
        )
        self.assertEqual(safe_pulls_url("https://evil.example/rupret007/webjam/pulls"), "")
        self.assertEqual(safe_pulls_url("javascript:alert(1)"), "")
        self.assertEqual(safe_pulls_url("https://github.com/rupret007/webjam/pulls/1"), "")
        self.assertEqual(
            pulls_url_from_repo("https://github.com/rupret007/webjam"),
            "https://github.com/rupret007/webjam/pulls",
        )
        self.assertEqual(pulls_url_from_repo("https://evil.example/webjam"), "")
        self.assertEqual(safe_release_tag("v0.26.0"), "v0.26.0")
        self.assertEqual(safe_release_tag("../etc/passwd"), "")
        self.assertEqual(safe_release_tag("v1/../../x"), "")
        self.assertEqual(
            safe_release_url("https://github.com/rupret007/webjam/releases/latest"),
            "https://github.com/rupret007/webjam/releases/latest",
        )
        self.assertEqual(
            safe_release_url("https://github.com/rupret007/webjam/releases/tag/v0.26.0"),
            "https://github.com/rupret007/webjam/releases/tag/v0.26.0",
        )
        self.assertEqual(safe_release_url("https://evil.example/rupret007/webjam/releases/latest"), "")
        self.assertEqual(safe_release_url("https://github.com/rupret007/webjam/releases/tag/../v1"), "")
        self.assertEqual(
            latest_release_url_from_repo("https://github.com/rupret007/webjam"),
            "https://github.com/rupret007/webjam/releases/latest",
        )
        self.assertEqual(latest_release_url_from_repo("https://evil.example/webjam"), "")
        self.assertIs(
            release_matches_tip({"tip_sha": "27530d8", "release_sha": "4b52080"}),
            False,
        )
        self.assertIs(
            release_matches_tip(
                {
                    "tip_sha": "4b52080",
                    "release_sha": "4b5208098981943df8ddaf1fac31aa36c15146bb",
                }
            ),
            True,
        )
        self.assertIsNone(release_matches_tip({"tip_sha": "27530d8", "release": "v0.26.0"}))

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
        self.assertEqual(
            status_from_fetch(
                {
                    "accessible": True,
                    "open_prs": None,
                    "pr_listing_complete": False,
                    "ci": {"conclusion": "success"},
                }
            ),
            "yellow",
        )
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

    def test_high_level_lanes_do_not_diagnose_hosted_ci(self):
        private_fail = {
            "accessible": True,
            "private": True,
            "open_prs": 0,
            "ci": {"conclusion": "failure"},
        }
        self.assertEqual(status_from_fetch(private_fail, high_level=True), "yellow")
        self.assertEqual(
            status_from_fetch(private_fail, override="yellow", high_level=True),
            "yellow",
        )
        self.assertEqual(
            status_from_fetch(private_fail, jeff_gate=True, high_level=True),
            "jeff-gate",
        )
        self.assertEqual(status_from_fetch(private_fail), "red")
        self.assertEqual(
            status_from_fetch(
                {"accessible": False, "ci": {"conclusion": "failure"}},
                override="yellow",
                high_level=True,
            ),
            "yellow",
        )
        self.assertIsNone(
            compact_signal({"private": True, "ci": {"conclusion": "failure"}})
        )
        self.assertIsNone(
            compact_signal({"private": True, "ci": {"conclusion": "pending"}})
        )
        self.assertEqual(compact_signal({"ci": {"conclusion": "failure"}}), "CI fail")
        # Empty-runner / 0-step hosted red is not a product fail on a high-level row.
        empty_runner = {
            "accessible": True,
            "private": True,
            "open_prs": 0,
            "ci": {"conclusion": "failure", "run_started_at": None},
        }
        self.assertNotEqual(status_from_fetch(empty_runner, high_level=True), "red")
        self.assertEqual(status_from_fetch(empty_runner, high_level=True), "yellow")
        self.assertIsNone(compact_signal(empty_runner))
        self.assertEqual(public_high_level_ci({"conclusion": "failure"}), {})
        self.assertEqual(public_high_level_ci({"conclusion": "startup_failure"}), {})
        self.assertEqual(public_high_level_ci({"conclusion": "timed_out"}), {})
        self.assertEqual(public_high_level_ci({"conclusion": "pending"}), {"conclusion": "pending"})
        self.assertEqual(public_high_level_ci({}), {})
        self.assertEqual(public_high_level_ci(None), {})
        empty_jobs = {
            "head_branch": "main",
            "status": "completed",
            "conclusion": "failure",
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "head_sha": "deadbeeaaaa",
            "run_started_at": None,
            "jobs": [],
        }
        self.assertTrue(is_unexecuted_run(empty_jobs))
        self.assertIsNone(pick_tip_ci([empty_jobs], "main", "deadbee"))
        no_runner = {
            **empty_jobs,
            "jobs": [{"name": "test", "runner_name": None, "steps": []}],
        }
        self.assertTrue(is_unexecuted_run(no_runner))
        self.assertIsNone(pick_tip_ci([no_runner], "main", "deadbee"))
        for concl in ("startup_failure", "timed_out", "action_required"):
            hosted = {
                "accessible": True,
                "private": True,
                "ci": {"conclusion": concl},
            }
            self.assertNotEqual(
                status_from_fetch(hosted, high_level=True),
                "red",
                concl,
            )
            self.assertIsNone(compact_signal(hosted))
            self.assertEqual(
                compact_signal({"ci": {"conclusion": concl}}),
                "CI fail",
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
                "path": ".github/workflows/ci.yml",
                "head_sha": "abc1234dead",
                "created_at": "2026-08-23T05:50:00Z",
            },
            {
                "head_branch": "master",
                "status": "completed",
                "conclusion": "failure",
                "name": "WebJam CI",
                "path": ".github/workflows/ci.yml",
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

    def test_pages_success_cannot_hide_test_fail_on_same_sha(self):
        # Live RadDadSite 6762fe3: pages listed first, Test Site failed.
        runs = [
            {
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "name": "pages build and deployment",
                "path": "dynamic/pages/pages-build-deployment",
                "head_sha": "6762fe3aaaa",
            },
            {
                "head_branch": "main",
                "status": "completed",
                "conclusion": "failure",
                "name": "Test Site",
                "path": ".github/workflows/test.yml",
                "head_sha": "6762fe3aaaa",
            },
        ]
        picked = pick_tip_ci(runs, "main", "6762fe3")
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked["conclusion"], "failure")
        self.assertEqual(picked["name"], "Test Site")
        self.assertTrue(is_ci_noise(runs[0]))
        self.assertFalse(is_ci_noise(runs[1]))

    def test_skipped_helper_cannot_beat_success_or_become_open_ci(self):
        # Live Andrea ff13dc0: newest runs are skipped bump/token; CI succeeded.
        runs = [
            {
                "head_branch": "main",
                "status": "completed",
                "conclusion": "skipped",
                "name": "Bump version",
                "path": ".github/workflows/bump-version.yml",
                "head_sha": "ff13dc0dead",
                "html_url": "https://github.com/rupret007/Andrea_NanoBot/actions/runs/32624035652",
            },
            {
                "head_branch": "main",
                "status": "completed",
                "conclusion": "skipped",
                "name": "Update token count",
                "path": ".github/workflows/update-tokens.yml",
                "head_sha": "ff13dc0dead",
                "html_url": "https://github.com/rupret007/Andrea_NanoBot/actions/runs/32624035682",
            },
            {
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "name": "CI",
                "path": ".github/workflows/ci.yml",
                "head_sha": "ff13dc0dead",
                "html_url": "https://github.com/rupret007/Andrea_NanoBot/actions/runs/32624035660",
            },
            {
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "name": "AGI Layer CI",
                "path": ".github/workflows/agi-ci.yml",
                "head_sha": "ff13dc0dead",
                "html_url": "https://github.com/rupret007/Andrea_NanoBot/actions/runs/32624035653",
            },
        ]
        picked = pick_tip_ci(runs, "main", "ff13dc0")
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked["conclusion"], "success")
        self.assertEqual(picked["name"], "CI")
        self.assertEqual(
            picked["html_url"],
            "https://github.com/rupret007/Andrea_NanoBot/actions/runs/32624035660",
        )
        self.assertNotEqual(compact_signal({"ci": picked}), "CI pending")
        hrefs = lane_hrefs(
            {
                "url": "https://github.com/rupret007/Andrea_NanoBot",
                "ci": picked,
            }
        )
        self.assertEqual(
            hrefs.get("ci"),
            "https://github.com/rupret007/Andrea_NanoBot/actions/runs/32624035660",
        )

    def test_skipped_helper_cannot_hide_ci_fail_on_same_sha(self):
        # Live Andrea 770fd47: newest runs are skipped bump/token helpers.
        runs = [
            {
                "head_branch": "main",
                "status": "completed",
                "conclusion": "skipped",
                "name": "Bump version",
                "path": ".github/workflows/bump-version.yml",
                "head_sha": "770fd47dead",
            },
            {
                "head_branch": "main",
                "status": "completed",
                "conclusion": "skipped",
                "name": "Update token count",
                "path": ".github/workflows/update-tokens.yml",
                "head_sha": "770fd47dead",
            },
            {
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "name": "AGI Layer CI",
                "path": ".github/workflows/agi-ci.yml",
                "head_sha": "770fd47dead",
            },
            {
                "head_branch": "main",
                "status": "completed",
                "conclusion": "failure",
                "name": "CI",
                "path": ".github/workflows/ci.yml",
                "head_sha": "770fd47dead",
            },
        ]
        picked = pick_tip_ci(runs, "main", "770fd47")
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked["conclusion"], "failure")
        self.assertEqual(picked["name"], "CI")

    def test_new_tip_without_matching_ci_is_missing_not_invented_pending(self):
        runs = [
            {
                "head_branch": "master",
                "status": "completed",
                "conclusion": "success",
                "name": "WebJam CI",
                "path": ".github/workflows/ci.yml",
                "head_sha": "4a24c8cold",
            }
        ]
        picked = pick_tip_ci(runs, "master", "a4e95cb")
        self.assertIsNone(picked)
        # No comparable SHAs: the tag stays. Do not invent CI pending.
        self.assertEqual(compact_signal({"release": "v0.26.0", "ci": picked}), "v0.26.0")
        self.assertEqual(
            compact_signal(
                {
                    "release": "v0.26.0",
                    "release_sha": "4a24c8c",
                    "tip_sha": "a4e95cb",
                    "open_prs": 0,
                    "ci": picked,
                }
            ),
            "Latest != source",
        )
        self.assertEqual(
            status_from_fetch({"accessible": True, "open_prs": 0, "ci": picked}),
            "green",
        )
        queued = {
            "head_branch": "master",
            "status": "queued",
            "conclusion": "",
            "name": "WebJam CI",
            "path": ".github/workflows/ci.yml",
            "head_sha": "a4e95cbnew",
            "html_url": "https://github.com/rupret007/webjam/actions/runs/9",
        }
        live = pick_tip_ci([queued], "master", "a4e95cb")
        self.assertIsNotNone(live)
        assert live is not None
        self.assertEqual(live["conclusion"], "queued")
        self.assertEqual(live["html_url"], "https://github.com/rupret007/webjam/actions/runs/9")
        self.assertEqual(
            compact_signal({"release": "v0.26.0", "ci": live}),
            "CI pending",
        )
        self.assertIsNone(pick_tip_ci([], "master", "a4e95cb"))
        self.assertTrue(sha_matches_tip(runs[0], "4a24c8c"))
        self.assertFalse(sha_matches_tip(runs[0], "a4e95cb"))

    def test_latest_same_workflow_success_beats_older_fail(self):
        runs = [
            {
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "name": "Test Site",
                "path": ".github/workflows/test.yml",
                "head_sha": "a56c44fbbbb",
            },
            {
                "head_branch": "main",
                "status": "completed",
                "conclusion": "failure",
                "name": "Test Site",
                "path": ".github/workflows/test.yml",
                "head_sha": "a56c44fbbbb",
            },
        ]
        picked = pick_tip_ci(runs, "main", "a56c44f")
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked["conclusion"], "success")

    def test_pages_only_repo_is_no_ci(self):
        runs = [
            {
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "name": "pages build and deployment",
                "path": "dynamic/pages/pages-build-deployment",
                "head_sha": "abc1234eeee",
            }
        ]
        self.assertIsNone(pick_tip_ci(runs, "main", "abc1234"))

    def test_refresh_publisher_cannot_become_tip_ci(self):
        # Live bob-ops-dashboard 1bff551 / 9c38307: the scheduled refresh
        # painted CI running (self in-progress) and would paint Red if it
        # failed, even when QA claim smoke succeeded on the same SHA.
        refresh_running = {
            "head_branch": "main",
            "status": "in_progress",
            "conclusion": None,
            "name": "Refresh Bob Ops Dashboard",
            "path": ".github/workflows/refresh-dashboard.yml",
            "head_sha": "9c38307aaaa",
            "html_url": "https://github.com/rupret007/bob-ops-dashboard/actions/runs/33028162056",
            "created_at": "2026-08-27T00:50:26Z",
        }
        refresh_fail = {
            **refresh_running,
            "status": "completed",
            "conclusion": "failure",
        }
        pages = {
            "head_branch": "main",
            "status": "completed",
            "conclusion": "success",
            "name": "pages build and deployment",
            "path": "dynamic/pages/pages-build-deployment",
            "head_sha": "9c38307aaaa",
        }
        qa = {
            "head_branch": "main",
            "status": "completed",
            "conclusion": "success",
            "name": "QA claim smoke",
            "path": ".github/workflows/qa-claim-smoke.yml",
            "head_sha": "9c38307aaaa",
            "html_url": "https://github.com/rupret007/bob-ops-dashboard/actions/runs/33027541068",
            "created_at": "2026-08-27T00:38:54Z",
        }
        self.assertTrue(is_ci_noise(refresh_running))
        self.assertTrue(is_ci_noise(refresh_fail))
        self.assertTrue(is_ci_noise(pages))
        self.assertFalse(is_ci_noise(qa))

        running = pick_tip_ci([refresh_running, pages, qa], "main", "9c38307")
        self.assertIsNotNone(running)
        assert running is not None
        self.assertEqual(running["name"], "QA claim smoke")
        self.assertEqual(running["conclusion"], "success")
        self.assertNotEqual(compact_signal({"ci": running, "open_prs": 2}), "CI running")
        self.assertEqual(compact_signal({"ci": running, "open_prs": 2}), "2 open PRs")
        self.assertEqual(
            status_from_fetch({"accessible": True, "open_prs": 2, "ci": running}),
            "yellow",
        )

        failed = pick_tip_ci([refresh_fail, pages, qa], "main", "9c38307")
        self.assertIsNotNone(failed)
        assert failed is not None
        self.assertEqual(failed["name"], "QA claim smoke")
        self.assertEqual(failed["conclusion"], "success")
        self.assertNotEqual(compact_signal({"ci": failed, "open_prs": 2}), "CI fail")
        self.assertNotEqual(
            status_from_fetch({"accessible": True, "open_prs": 2, "ci": failed}),
            "red",
        )

        publisher_only = pick_tip_ci([refresh_running, pages], "main", "9c38307")
        self.assertIsNone(publisher_only)
        self.assertEqual(
            compact_signal({"ci": publisher_only, "open_prs": 2}),
            "2 open PRs",
        )

    def test_refresh_only_tip_does_not_inherit_older_qa_as_pending(self):
        # Live after #31: GITHUB_TOKEN refresh tips have Pages + refresh
        # only. Older QA (356b652) still sits in the 20-run window. That
        # must stay missing-CI, not invented "CI pending" with no Open CI.
        refresh = {
            "head_branch": "main",
            "status": "completed",
            "conclusion": "success",
            "name": "Refresh Bob Ops Dashboard",
            "path": ".github/workflows/refresh-dashboard.yml",
            "head_sha": "098cf5cbbbb",
        }
        pages = {
            "head_branch": "main",
            "status": "completed",
            "conclusion": "success",
            "name": "pages build and deployment",
            "path": "dynamic/pages/pages-build-deployment",
            "head_sha": "098cf5cbbbb",
        }
        older_qa = {
            "head_branch": "main",
            "status": "completed",
            "conclusion": "success",
            "name": "QA claim smoke",
            "path": ".github/workflows/qa-claim-smoke.yml",
            "head_sha": "356b652aaaa",
        }
        picked = pick_tip_ci([refresh, pages, older_qa], "main", "098cf5c")
        self.assertIsNone(picked)
        self.assertIsNone(compact_signal({"ci": picked, "open_prs": 0}))
        self.assertEqual(
            status_from_fetch({"accessible": True, "open_prs": 0, "ci": picked}),
            "green",
        )
        # Historical QA on another SHA is also missing CI, not invented pending.
        still_missing = pick_tip_ci([older_qa], "main", "098cf5c")
        self.assertIsNone(still_missing)
        self.assertIsNone(compact_signal({"ci": still_missing, "open_prs": 0}))
        self.assertEqual(
            status_from_fetch({"accessible": True, "open_prs": 0, "ci": still_missing}),
            "green",
        )

    def test_leftover_honesty_isolation_is_real_tip_ci(self):
        # Live Show Night 3c9c021: leftover-honesty.yml runs npm ci +
        # test:isolation. That is product tip CI, not an empty runner or
        # this board's refresh publisher.
        leftover = {
            "head_branch": "main",
            "status": "completed",
            "conclusion": "success",
            "name": "leftover-honesty",
            "path": ".github/workflows/leftover-honesty.yml",
            "head_sha": "3c9c0216c8c592234c114a11317efa1e9812c8e6",
            "html_url": "https://github.com/rupret007/rad-dad-show-night/actions/runs/33142362633",
            "created_at": "2026-08-28T04:37:48Z",
        }
        pages = {
            "head_branch": "main",
            "status": "completed",
            "conclusion": "success",
            "name": "pages build and deployment",
            "path": "dynamic/pages/pages-build-deployment",
            "head_sha": "3c9c0216c8c592234c114a11317efa1e9812c8e6",
        }
        self.assertFalse(is_ci_noise(leftover))
        self.assertTrue(is_ci_noise(pages))
        picked = pick_tip_ci([pages, leftover], "main", "3c9c021")
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked["name"], "leftover-honesty")
        self.assertEqual(picked["conclusion"], "success")
        project = {
            "name": "Show Night",
            "accessible": True,
            "url": "https://github.com/rupret007/rad-dad-show-night",
            "open_prs": 0,
            "ci": picked,
        }
        self.assertEqual(status_from_fetch(project), "green")
        self.assertIsNone(compact_signal(project))
        hrefs = lane_hrefs(project)
        self.assertEqual(
            hrefs["ci"],
            "https://github.com/rupret007/rad-dad-show-night/actions/runs/33142362633",
        )
        refresh = Path(__file__).with_name("refresh.sh").read_text()
        self.assertIn(
            "Live run sheet. GitHub is source; live Latest is Sites. Green CI is not Latest.",
            refresh,
        )
        self.assertNotIn("No CI is OK", refresh)
        self.assertLessEqual(
            len(
                "Live run sheet. GitHub is source; live Latest is Sites. Green CI is not Latest."
            ),
            88,
        )

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
            file_texts=[("file:/home/runner/work/dashboard/agents-status.json", json.dumps(stale))],
            previous=older_running,
            now=now,
        )
        self.assertEqual(src, "file:stale->unknown")
        self.assertEqual([a["state"] for a in agents], ["unknown", "unknown", "unknown"])
        self.assertNotIn("running", [a["state"] for a in agents])

        _, local_src = resolve_agents(
            file_texts=[("file:/Users/owner/private/agents-status.json", json.dumps(stale))],
            now=now,
        )
        self.assertEqual(local_src, "file:stale->unknown")

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
        path_parsed = parse_agents_blob(
            {
                "agents": [
                    {
                        "id": "codex",
                        "state": "unknown",
                        "detail": "/Users/owner/private/agents-status.json",
                    }
                ]
            }
        )
        self.assertIsNotNone(path_parsed)
        assert path_parsed is not None
        self.assertEqual(path_parsed[0]["detail"], "detail redacted")
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
        d = dict(a)
        d["sections"] = [
            {
                "id": "live-shipping",
                "title": "Live",
                "projects": [
                    {
                        "name": "WebJam",
                        "status": "yellow",
                        "ci": {"conclusion": "pending", "sha": "a4e95cb"},
                    }
                ],
            }
        ]
        self.assertNotEqual(board_content_fingerprint(a), board_content_fingerprint(d))
        latest_a = {
            "sections": [
                {
                    "id": "live-shipping",
                    "projects": [
                        {
                            "name": "WebJam",
                            "release": "v0.26.0",
                            "release_sha": "4b52080",
                            "tip_sha": "4b52080",
                        }
                    ],
                }
            ]
        }
        latest_b = {
            "sections": [
                {
                    "id": "live-shipping",
                    "projects": [
                        {
                            "name": "WebJam",
                            "release": "v0.26.0",
                            "release_sha": "4b52080",
                            "tip_sha": "27530d8",
                        }
                    ],
                }
            ]
        }
        self.assertNotEqual(
            board_content_fingerprint(latest_a), board_content_fingerprint(latest_b)
        )
        e = dict(d)
        e["cloud_agents"] = [
            {
                "id": "bc-12345678-1234-1234-1234-123456789abc",
                "url": "https://cursor.com/agents/bc-12345678-1234-1234-1234-123456789abc",
            }
        ]
        self.assertNotEqual(board_content_fingerprint(d), board_content_fingerprint(e))
        stack_a = {
            "sections": [
                {
                    "id": "live-shipping",
                    "projects": [
                        {"name": "StoryLiner", "open_prs": 2, "open_pr_stack": []}
                    ],
                }
            ]
        }
        stack_b = {
            "sections": [
                {
                    "id": "live-shipping",
                    "projects": [
                        {
                            "name": "StoryLiner",
                            "open_prs": 2,
                            "open_pr_stack": [
                                {"number": 11, "url": "https://github.com/rupret007/StoryLiner/pull/11"},
                                {"number": 12, "url": "https://github.com/rupret007/StoryLiner/pull/12"},
                            ],
                        }
                    ],
                }
            ]
        }
        self.assertNotEqual(
            board_content_fingerprint(stack_a), board_content_fingerprint(stack_b)
        )
        live_a = {
            "sections": [{"id": "live-shipping", "projects": [{"name": "Turdanoid"}]}]
        }
        live_b = {
            "sections": [
                {
                    "id": "live-shipping",
                    "projects": [
                        {
                            "name": "Turdanoid",
                            "live_game_url": "https://rupret007.github.io/Turdanoid/hub.html",
                        }
                    ],
                }
            ]
        }
        self.assertNotEqual(
            board_content_fingerprint(live_a), board_content_fingerprint(live_b)
        )

    def test_agent_and_work_urls_are_fail_closed(self):
        good_bc = "bc-8e16f06d-f73f-482c-987f-e13f2d3b9fb1"
        good = "https://cursor.com/agents/" + good_bc
        self.assertEqual(safe_agent_url(good + "?cursor_ref=x"), good)
        self.assertEqual(safe_agent_url("https://evil.example/agents/" + good_bc), "")
        self.assertEqual(safe_agent_url("javascript:alert(1)"), "")
        self.assertEqual(safe_agent_url("https://cursor.com/agents/bc-nope"), "")
        self.assertEqual(safe_agent_url(""), "")
        self.assertEqual(
            extract_agent_url(
                '<a href="https://cursor.com/agents/' + good_bc + '?cursor_ref=pr_footer">x</a>'
            ),
            good,
        )
        self.assertEqual(
            extract_agent_url("https://cursor.com/background-agent?bcId=" + good_bc + "&x=1"),
            good,
        )
        self.assertEqual(extract_agent_url("no agent here"), "")
        self.assertEqual(agent_url_from_fields({"bc_id": good_bc}), good)
        self.assertEqual(agent_url_from_fields({"bc_id": "bc-invented"}), "")
        self.assertEqual(agent_url_from_fields({"url": "https://example/nope"}), "")
        self.assertEqual(
            safe_pr_url("https://github.com/rupret007/webjam/pull/21?foo=1"),
            "https://github.com/rupret007/webjam/pull/21",
        )
        self.assertEqual(safe_pr_url("https://github.com/evil/webjam/pull/21"), "")
        self.assertEqual(safe_pr_url("https://github.com/rupret007/webjam"), "")
        self.assertEqual(
            safe_actions_url("https://github.com/rupret007/Andrea_NanoBot/actions/runs/99"),
            "https://github.com/rupret007/Andrea_NanoBot/actions/runs/99",
        )
        self.assertEqual(safe_actions_url("https://github.com/rupret007/Andrea_NanoBot/actions"), "")
        self.assertEqual(safe_repo_url("https://github.com/rupret007/webjam/"), "https://github.com/rupret007/webjam")
        self.assertEqual(safe_repo_url("https://github.com/rupret007/webjam/issues"), "")
        self.assertEqual(
            safe_pr_url("https://github.com/0xc0re/barker/pull/41"),
            "https://github.com/0xc0re/barker/pull/41",
        )
        self.assertEqual(
            safe_actions_url("https://github.com/0xc0re/barker/actions/runs/9"),
            "https://github.com/0xc0re/barker/actions/runs/9",
        )
        self.assertEqual(
            safe_repo_url("https://github.com/0xc0re/barker"),
            "https://github.com/0xc0re/barker",
        )
        self.assertEqual(
            safe_pulls_url("https://github.com/0xc0re/barker/pulls"),
            "https://github.com/0xc0re/barker/pulls",
        )
        self.assertEqual(safe_repo_url("https://github.com/0xc0re/other"), "")
        self.assertEqual(
            safe_game_url("https://rupret007.github.io/Turdanoid/hub.html?from=board"),
            "https://rupret007.github.io/Turdanoid/hub.html",
        )
        self.assertEqual(safe_game_url("https://rupret007.github.io/Turdanoid/"), "")
        self.assertEqual(safe_game_url("https://rupret007.github.io/Turdanoid/index.html"), "")
        self.assertEqual(safe_game_url("https://evil.example/Turdanoid/hub.html"), "")
        self.assertEqual(safe_game_url("javascript:alert(1)"), "")

    def test_pick_open_pr_prefers_ready_and_is_safe(self):
        picked = pick_open_pr(
            [
                {
                    "html_url": "https://github.com/rupret007/webjam/pull/9",
                    "number": 9,
                    "title": "draft",
                    "draft": True,
                    "updated_at": "2026-08-23T07:00:00Z",
                },
                {
                    "html_url": "https://github.com/rupret007/webjam/pull/21",
                    "number": 21,
                    "title": "ready",
                    "draft": False,
                    "updated_at": "2026-08-23T06:00:00Z",
                },
            ]
        )
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked["url"], "https://github.com/rupret007/webjam/pull/21")
        self.assertEqual(picked["number"], 21)
        self.assertFalse(picked["draft"])
        self.assertIsNone(pick_open_pr([{"html_url": "https://evil.example/pull/1"}]))
        self.assertIsNone(pick_open_pr(None))
        leftover = {
            "html_url": "https://github.com/rupret007/webjam/pull/49",
            "number": 49,
            "title": "Rebuild Pocket Stage kit on Mac desktop CI",
            "draft": True,
            "updated_at": "2026-08-27T07:09:29Z",
        }
        self.assertTrue(is_draft_pr(leftover))
        self.assertIsNone(pick_open_pr([leftover]))
        self.assertIsNone(
            pick_open_pr(
                [
                    leftover,
                    {
                        "html_url": "https://github.com/rupret007/webjam/pull/37",
                        "number": 37,
                        "title": "Retry shared canvas delivery after peer control arrives",
                        "draft": True,
                        "updated_at": "2026-08-26T20:04:18Z",
                    },
                ]
            )
        )

    def test_parked_leftover_drafts_are_not_active_cloud_agents(self):
        leftover_bc = "bc-48233059-c14e-4168-ae78-15566aa55495"
        leftover = {
            "number": 49,
            "title": "Rebuild Pocket Stage kit on Mac desktop CI",
            "html_url": "https://github.com/rupret007/webjam/pull/49",
            "body": (
                "Draft only. Do not merge from here. "
                "https://cursor.com/agents/" + leftover_bc
            ),
            "draft": True,
            "state": "open",
            "updated_at": "2026-08-27T07:09:29Z",
            "base": {"repo": {"full_name": "rupret007/webjam"}},
            "head": {"repo": {"full_name": "rupret007/webjam"}},
        }
        parked_37 = {
            "number": 37,
            "title": "Retry shared canvas delivery after peer control arrives",
            "html_url": "https://github.com/rupret007/webjam/pull/37",
            "body": "See https://cursor.com/agents/bc-11111111-1111-1111-1111-111111111111",
            "draft": True,
            "state": "open",
            "base": {"repo": {"full_name": "rupret007/webjam"}},
            "head": {"repo": {"full_name": "rupret007/webjam"}},
        }
        self.assertEqual(extract_cloud_agents_from_prs([leftover, parked_37]), [])
        ready_bc = "bc-8e16f06d-f73f-482c-987f-e13f2d3b9fb1"
        ready = {
            "number": 54,
            "title": "Polish WebJam UI",
            "html_url": "https://github.com/rupret007/webjam/pull/54",
            "body": "See https://cursor.com/agents/" + ready_bc,
            "draft": False,
            "state": "open",
            "updated_at": "2026-08-28T23:33:01Z",
            "base": {"repo": {"full_name": "rupret007/webjam"}},
            "head": {"repo": {"full_name": "rupret007/webjam"}},
        }
        found = extract_cloud_agents_from_prs([leftover, ready])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["url"], "https://cursor.com/agents/" + ready_bc)
        self.assertEqual(found[0]["name"], "PR #54")

    def test_refresh_drops_leftover_jeff_gates(self):
        blob = Path(__file__).with_name("refresh.sh").read_text()
        self.assertNotIn('project("webjam", jeff_gate=True', blob)
        self.assertNotIn(
            'project("Sliding-Glass-Door-PETG-Screw", jeff_gate=True', blob
        )
        self.assertIn("public_high_level_ci", blob)
        for want in ("che-live-pull", "logic-keys-wavs", "adoptiq-live-cisco"):
            self.assertIn('"id": "' + want + '"', blob)

    def test_refresh_drops_standing_yellow_overrides(self):
        blob = Path(__file__).with_name("refresh.sh").read_text()
        for pin in (
            'project("Turdanoid", status="yellow"',
            'project("AdoptIQ", status="yellow"',
            'project("TACTrack", status="yellow"',
            'project("Bob-the-Bot", status="yellow"',
        ):
            self.assertNotIn(pin, blob)
        self.assertIn('project("Turdanoid",', blob)
        self.assertIn('project("AdoptIQ", high_level_only=True,', blob)
        self.assertIn('project("TACTrack", high_level_only=True,', blob)
        self.assertIn('project("Bob-the-Bot", high_level_only=True,', blob)

    def test_linear_pr_stack_requires_one_complete_same_repo_chain(self):
        repo = {"full_name": "rupret007/repo"}

        def row(number, base, head, *, head_repo=repo, url_repo="repo"):
            return {
                "number": number,
                "html_url": f"https://github.com/rupret007/{url_repo}/pull/{number}",
                "base": {"ref": base, "repo": repo},
                "head": {"ref": head, "repo": head_repo},
            }

        shuffled = [
            row(12, "feature-eleven", "feature-twelve"),
            row(10, "main", "feature-ten"),
            row(11, "feature-ten", "feature-eleven"),
        ]
        self.assertEqual(
            detect_linear_pr_stack(shuffled, "main"),
            [
                {"number": 10, "url": "https://github.com/rupret007/repo/pull/10"},
                {"number": 11, "url": "https://github.com/rupret007/repo/pull/11"},
                {"number": 12, "url": "https://github.com/rupret007/repo/pull/12"},
            ],
        )
        self.assertEqual(
            detect_linear_pr_stack(
                [row(10, "main", "a"), row(11, "main", "b")], "main"
            ),
            [],
        )
        self.assertEqual(
            detect_linear_pr_stack(
                [row(10, "main", "a"), row(11, "a", "b", head_repo={"full_name": "fork/repo"})],
                "main",
            ),
            [],
        )
        missing = row(10, "main", "a")
        missing["head"].pop("repo")
        self.assertEqual(detect_linear_pr_stack([missing, row(11, "a", "b")], "main"), [])
        self.assertEqual(detect_linear_pr_stack([row(10, "main", "a")], "main"), [])
        self.assertEqual(detect_linear_pr_stack(None, "main"), [])
        barker_repo = {"full_name": "0xc0re/barker"}
        barker = [
            {
                "number": 42,
                "html_url": "https://github.com/0xc0re/barker/pull/42",
                "base": {"ref": "feature-41", "repo": barker_repo},
                "head": {"ref": "feature-42", "repo": barker_repo},
            },
            {
                "number": 41,
                "html_url": "https://github.com/0xc0re/barker/pull/41",
                "base": {"ref": "main", "repo": barker_repo},
                "head": {"ref": "feature-41", "repo": barker_repo},
            },
        ]
        self.assertEqual(
            detect_linear_pr_stack(barker, "main"),
            [
                {"number": 41, "url": "https://github.com/0xc0re/barker/pull/41"},
                {"number": 42, "url": "https://github.com/0xc0re/barker/pull/42"},
            ],
        )

    def test_cloud_agents_never_invent_bc_or_running(self):
        good_bc = "bc-8e16f06d-f73f-482c-987f-e13f2d3b9fb1"
        good = "https://cursor.com/agents/" + good_bc
        blob = {
            "agents": [
                {"id": "codex", "state": "idle", "detail": "ok"},
                {
                    "id": "ghost",
                    "state": "running",
                    "url": "https://evil.example/agents/" + good_bc,
                },
            ],
            "cloud_agents": [
                {
                    "name": "Seed",
                    "state": "running",
                    "url": good,
                    "pr_url": "https://github.com/rupret007/bob-ops-dashboard/pull/8",
                },
                {"name": "Fake", "bc_id": "not-a-bc", "url": "https://cursor.com/agents/nope"},
            ],
        }
        mac = parse_agents_blob(blob)
        self.assertIsNotNone(mac)
        assert mac is not None
        self.assertEqual([a["id"] for a in mac], ["codex", "cursor", "claude"])
        cloud = parse_cloud_agents(blob)
        self.assertEqual(len(cloud), 1)
        self.assertEqual(cloud[0]["id"], good_bc)
        self.assertEqual(cloud[0]["url"], good)
        self.assertEqual(cloud[0]["state"], "unknown")
        self.assertEqual(cloud[0]["pr_url"], "https://github.com/rupret007/bob-ops-dashboard/pull/8")
        self.assertEqual(merge_cloud_agents([{"url": "https://example.invalid"}]), [])
        found = extract_cloud_agents_from_prs(
            [
                {
                    "number": 8,
                    "title": "poll honesty",
                    "html_url": "https://github.com/rupret007/bob-ops-dashboard/pull/8",
                    "body": "See " + good + "?cursor_ref=pr_footer",
                    "updated_at": "2026-08-23T06:48:00Z",
                    "state": "open",
                    "base": {"repo": {"full_name": "rupret007/bob-ops-dashboard"}},
                    "head": {"repo": {"full_name": "rupret007/bob-ops-dashboard"}},
                },
                {
                    "number": 9,
                    "title": "fork cannot advertise a public work link",
                    "html_url": "https://github.com/rupret007/bob-ops-dashboard/pull/9",
                    "body": "See " + good,
                    "state": "open",
                    "base": {"repo": {"full_name": "rupret007/bob-ops-dashboard"}},
                    "head": {"repo": {"full_name": "other/fork"}},
                },
                {
                    "number": 10,
                    "title": "closed cannot advertise a public work link",
                    "html_url": "https://github.com/rupret007/bob-ops-dashboard/pull/10",
                    "body": "See " + good,
                    "state": "closed",
                    "base": {"repo": {"full_name": "rupret007/bob-ops-dashboard"}},
                    "head": {"repo": {"full_name": "rupret007/bob-ops-dashboard"}},
                },
                {
                    "number": 11,
                    "title": "unknown state cannot advertise a public work link",
                    "html_url": "https://github.com/rupret007/bob-ops-dashboard/pull/11",
                    "body": "See " + good,
                    "base": {"repo": {"full_name": "rupret007/bob-ops-dashboard"}},
                    "head": {"repo": {"full_name": "rupret007/bob-ops-dashboard"}},
                },
            ]
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["url"], good)
        self.assertEqual(found[0]["name"], "PR #8")
        self.assertEqual(found[0]["state"], "unknown")

    def test_lane_hrefs_prefer_pr_and_skip_missing(self):
        hrefs = lane_hrefs(
            {
                "url": "https://github.com/rupret007/webjam",
                "open_pr_url": "https://github.com/rupret007/webjam/pull/21",
                "agent_url": "https://cursor.com/agents/bc-8e16f06d-f73f-482c-987f-e13f2d3b9fb1",
                "live_game_url": "https://rupret007.github.io/Turdanoid/hub.html",
                "ci": {
                    "conclusion": "failure",
                    "html_url": "https://github.com/rupret007/webjam/actions/runs/44",
                },
            }
        )
        self.assertEqual(hrefs["title"], "https://github.com/rupret007/webjam/pull/21")
        self.assertEqual(hrefs["pr"], "https://github.com/rupret007/webjam/pull/21")
        self.assertEqual(hrefs["repo"], "https://github.com/rupret007/webjam")
        self.assertEqual(hrefs["ci"], "https://github.com/rupret007/webjam/actions/runs/44")
        self.assertEqual(hrefs["game"], "https://rupret007.github.io/Turdanoid/hub.html")
        self.assertTrue(hrefs["agent"].startswith("https://cursor.com/agents/bc-"))
        bare = lane_hrefs({"name": "Show Night", "url": None, "ci": {}})
        self.assertEqual(bare, {"title": ""})
        self.assertEqual(lane_hrefs(None), {})
        skipped = lane_hrefs(
            {
                "url": "https://github.com/rupret007/Andrea_NanoBot",
                "ci": {
                    "conclusion": "skipped",
                    "html_url": "https://github.com/rupret007/Andrea_NanoBot/actions/runs/32624035652",
                },
            }
        )
        self.assertNotIn("ci", skipped)
        cancelled = lane_hrefs(
            {
                "url": "https://github.com/rupret007/Andrea_NanoBot",
                "ci": {
                    "conclusion": "cancelled",
                    "html_url": "https://github.com/rupret007/Andrea_NanoBot/actions/runs/9",
                },
            }
        )
        self.assertNotIn("ci", cancelled)

    def test_pick_tip_ci_keeps_run_url_and_pending_has_none(self):
        runs = [
            {
                "head_branch": "main",
                "status": "completed",
                "conclusion": "failure",
                "name": "CI",
                "path": ".github/workflows/ci.yml",
                "head_sha": "770fd47dead",
                "html_url": "https://github.com/rupret007/Andrea_NanoBot/actions/runs/77",
            }
        ]
        picked = pick_tip_ci(runs, "main", "770fd47")
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked["html_url"], "https://github.com/rupret007/Andrea_NanoBot/actions/runs/77")
        missing = pick_tip_ci(
            [
                {
                    "head_branch": "master",
                    "status": "completed",
                    "conclusion": "success",
                    "name": "WebJam CI",
                    "path": ".github/workflows/ci.yml",
                    "head_sha": "4a24c8cold",
                    "html_url": "https://github.com/rupret007/webjam/actions/runs/1",
                }
            ],
            "master",
            "a4e95cb",
        )
        self.assertIsNone(missing)

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




class CoordLeaseTests(unittest.TestCase):
    def test_coord_issue_lease_and_public_sanitize(self):
        now = datetime(2026, 8, 28, 22, 0, tzinfo=timezone.utc)
        issue = {
            "number": 4,
            "title": "coord: rupret007/webjam",
            "url": "https://github.com/rupret007/Bob-the-Bot/issues/4",
            "body": "\n".join([
                "- agent: Codex",
                "- sha: df69d203c99afab1e9d2cbcfd389362944cd936a",
                "- branch: leftover/docs",
                "- pr: 55",
                "- claimed_scope: leftover honesty after #54",
                "- holds: #37 #49",
                "- next_action: leftover #55 waiting Karen",
                "- lease_until: 2026-08-29T03:00:00+00:00",
            ]),
        }
        parsed = parse_coord_issue(issue, now=now)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["repo"], "webjam")
        self.assertEqual(parsed["agent"], "codex")
        self.assertEqual(parsed["lease_state"], "active")
        refs = [{
            "number": 55,
            "url": "https://github.com/rupret007/webjam/pull/55",
            "draft": True,
        }]
        public = public_coord(parsed, open_pr_refs=refs)
        self.assertEqual(public["pr"], 55)
        self.assertEqual(public["pr_url"], "https://github.com/rupret007/webjam/pull/55")
        self.assertTrue(public["pr_draft"])
        self.assertEqual(public["sha"], "df69d20")
        private = public_coord(parsed, private_lane=True, open_pr_refs=refs)
        self.assertNotIn("sha", private)
        self.assertNotIn("pr", private)
        self.assertEqual(coord_signal({"coord": public}), "Codex lease")
        self.assertEqual(compact_signal({"coord": public, "open_prs": 3}), "Codex lease")
        self.assertEqual(compact_signal({"ci": {"conclusion": "failure"}, "coord": public}), "CI fail")
        self.assertIsNone(coord_signal({"private": True, "coord": public}))
        self.assertEqual(signal_href({"coord": public}), "")
        expired = parse_coord_issue(issue, now=datetime(2026, 8, 30, tzinfo=timezone.utc))
        assert expired is not None
        self.assertEqual(expired["lease_state"], "expired")
        self.assertEqual(expired["agent"], "none")
        leftover = {
            "repo_url": "https://github.com/rupret007/webjam",
            "status": "green",
            "coord": public_coord(expired, open_pr_refs=refs),
        }
        self.assertEqual(leftover["coord"]["pr"], 55)
        self.assertTrue(leftover["coord"]["pr_draft"])
        self.assertEqual(coord_pr_url(leftover), "")
        self.assertIsNone(coord_review_signal(leftover))
        self.assertIsNone(compact_signal(leftover))
        self.assertEqual(status_with_coord_review("green", leftover), "green")
        self.assertEqual(
            glance_status([], [{"id": "live-shipping", "projects": [leftover]}]),
            {"text": "Quiet", "tab": ""},
        )
        self.assertIsNone(parse_coord_issue({"title": "random issue"}))

    def test_coord_pr_needs_same_repo_live_open_receipt(self):
        parsed = {
            "owner": "rupret007",
            "repo": "StoryBoard",
            "agent": "none",
            "lease_state": "none",
            "sha": "abc1234",
            "pr": "https://github.com/rupret007/StoryBoard/pull/23",
        }
        ref = {
            "number": 23,
            "url": "https://github.com/rupret007/StoryBoard/pull/23",
            "draft": True,
        }
        self.assertNotIn("pr", public_coord(parsed))
        self.assertNotIn("pr", public_coord(parsed, open_pr_refs=[]))
        self.assertNotIn(
            "pr",
            public_coord(
                parsed,
                open_pr_refs=[dict(ref, url="https://github.com/rupret007/webjam/pull/23")],
            ),
        )
        cross_repo_claim = dict(
            parsed, pr="https://github.com/rupret007/webjam/pull/23"
        )
        self.assertNotIn("pr", public_coord(cross_repo_claim, open_pr_refs=[ref]))
        self.assertNotIn(
            "pr",
            public_coord(parsed, open_pr_refs=[dict(ref, number=24)]),
        )
        self.assertNotIn(
            "pr",
            public_coord(parsed, open_pr_refs=[dict(ref, draft="true")]),
        )

        public = public_coord(parsed, open_pr_refs=[ref])
        leftover = {
            "repo_url": "https://github.com/rupret007/StoryBoard",
            "status": "green",
            "coord": public,
        }
        self.assertEqual(public["pr"], 23)
        self.assertTrue(public["pr_draft"])
        self.assertEqual(coord_pr_url(leftover), "")
        self.assertIsNone(coord_review_signal(leftover))
        self.assertIsNone(compact_signal(leftover))
        self.assertEqual(signal_href(leftover), "")
        self.assertNotEqual(lane_hrefs(leftover).get("title"), ref["url"])
        self.assertEqual(status_with_coord_review("green", leftover), "green")
        self.assertEqual(status_with_coord_review("red", leftover), "red")
        self.assertEqual(status_with_coord_review("jeff-gate", leftover), "jeff-gate")
        self.assertEqual(
            glance_status([], [{"id": "live-shipping", "projects": [leftover]}]),
            {"text": "Quiet", "tab": ""},
        )

        ready_ref = dict(ref, draft=False)
        ready_public = public_coord(parsed, open_pr_refs=[ready_ref])
        project = {
            "repo_url": "https://github.com/rupret007/StoryBoard",
            "status": "green",
            "coord": ready_public,
        }
        self.assertEqual(
            coord_pr_url(project),
            "https://github.com/rupret007/StoryBoard/pull/23",
        )
        self.assertEqual(coord_review_signal(project), "PR #23")
        self.assertEqual(compact_signal(project), "PR #23")
        self.assertEqual(signal_href(project), ready_ref["url"])
        self.assertEqual(lane_hrefs(project)["title"], ready_ref["url"])
        self.assertEqual(status_with_coord_review("green", project), "yellow")
        self.assertEqual(status_with_coord_review("red", project), "red")
        self.assertEqual(status_with_coord_review("jeff-gate", project), "jeff-gate")

        active = dict(project)
        active["coord"] = dict(ready_public, agent="grok", lease_state="active")
        self.assertEqual(compact_signal(active), "Grok lease")
        self.assertEqual(signal_href(active), "")
        failing = dict(project, ci={"conclusion": "failure"})
        self.assertEqual(compact_signal(failing), "CI fail")

        private = dict(project, private=True)
        self.assertEqual(coord_pr_url(private), "")
        self.assertIsNone(coord_review_signal(private))
        self.assertEqual(status_with_coord_review("green", private), "green")
        tampered = dict(project, coord=dict(ready_public, pr_url="https://github.com/rupret007/webjam/pull/23"))
        self.assertEqual(coord_pr_url(tampered), "")
        self.assertIsNone(coord_review_signal(tampered))


if __name__ == "__main__":
    unittest.main(verbosity=2)
