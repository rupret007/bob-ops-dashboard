#!/usr/bin/env python3
"""First-class Abilities / Controls / Features cards (honest, no fake buttons)."""
from __future__ import annotations

from typing import Any

FIRST_CLASS_IDS = ("abilities", "controls", "features")
CONTROL_ACTIONS = frozenset({"refresh-hint", "open-repo", "mark-reviewed"})


def drop_leftover_verify(status: Any) -> bool:
    """Fail-closed: never keep an OTP verify block on a public board."""
    if not isinstance(status, dict) or "verify" not in status:
        return False
    status.pop("verify", None)
    return True


def _card(
    name: str,
    notes: str,
    *,
    status: str = "green",
    chip: str = "Ability",
    url: str | None = None,
    control_action: str | None = None,
    action_label: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": name,
        "status": status,
        "chip": chip,
        "notes": notes,
    }
    if url:
        row["url"] = url
    if control_action:
        if control_action not in CONTROL_ACTIONS:
            raise ValueError("control_action not allowlisted: " + control_action)
        row["control_action"] = control_action
        row["action_label"] = action_label or name
    return row


def first_class_sections() -> list[dict[str, Any]]:
    return [
        {
            "id": "abilities",
            "title": "Abilities",
            "projects": [
                _card(
                    "Rebuild this board",
                    "Actions cron every 15m plus ./refresh.sh. Live gh SHAs/CI. No secrets on the public page.",
                ),
                _card(
                    "Cursor Cloud Agents",
                    "Repo work in Cursor Cloud. Jeff-gated / cost ceiling. High-level only here.",
                ),
                _card(
                    "Local Codex goals",
                    "Mac Codex loop for AdoptIQ and ops lanes. High-risk goals still shown in chat before launch.",
                ),
                _card(
                    "Texts via Andrea",
                    "After Jeff yes only. Never auto-send. Draft must already exist. No send button on this page.",
                    status="jeff-gate",
                    chip="After yes",
                ),
                _card(
                    "Life-ops / Domino's",
                    "Food, errands, Domino's: chat yes only. Honest: there is no order button on this board.",
                    status="jeff-gate",
                    chip="After yes",
                ),
                _card(
                    "Music producer help",
                    "Logic Pro is home. Moises / Suno need Jeff login. LogicProMCP needs Jeff GUI grants.",
                    status="jeff-gate",
                    chip="Jeff-gate",
                ),
                _card(
                    "Cisco CS desktop (high-level)",
                    "AdoptIQ / TACTrack appear as high-level Cisco CS desktop only. No CSOne paths or customer rows.",
                    status="parked",
                    chip="High-level",
                ),
                _card(
                    "GitHub issue inbox",
                    "BOB-APPROVE / BOB-DENY / BOB-HOLD from rupret007 is the real yes. The board only opens the draft.",
                    status="jeff-gate",
                    chip="Authority",
                ),
            ],
        },
        {
            "id": "controls",
            "title": "Controls",
            "projects": [
                _card(
                    "Approve / Hold / Deny",
                    "Pending items are public. Each button opens a GitHub issue as rupret007. That issue is the real yes. No silent act.",
                    status="jeff-gate",
                    chip="Control",
                ),
                _card(
                    "Copy refresh command",
                    "Copies ./refresh.sh --push. Does not run it.",
                    status="green",
                    chip="Control",
                    control_action="refresh-hint",
                    action_label="Copy command",
                ),
                _card(
                    "Open dashboard repo",
                    "Public repo. Real merge/authority stays on GitHub as rupret007.",
                    status="green",
                    chip="Control",
                    url="https://github.com/rupret007/bob-ops-dashboard",
                    control_action="open-repo",
                    action_label="Open repo",
                ),
                _card(
                    "Mark board reviewed",
                    "Local timestamp on this phone only. Does not notify Bob or change GitHub.",
                    status="green",
                    chip="Control",
                    control_action="mark-reviewed",
                    action_label="Mark reviewed",
                ),
            ],
        },
        {
            "id": "features",
            "title": "Features",
            "projects": [
                _card(
                    "Public board",
                    "Possession of the public URL is enough. GitHub login rupret007 is the real authority.",
                    chip="Feature",
                ),
                _card(
                    "Soft-paint poll",
                    "Client fetches status.json every 30s (pauses when the tab is hidden). Repaints the board when generated_at changes. No full reload.",
                    chip="Feature",
                ),
                _card(
                    "Agents strip",
                    "Codex / Cursor / Claude state from a Mac probe. Public fields only. Token-like words redacted.",
                    chip="Feature",
                ),
                _card(
                    "Live shipping chips",
                    "Green / yellow / red / parked / Jeff-gate from live gh tips, open PRs, and Actions.",
                    chip="Feature",
                ),
                _card(
                    "Cisco high-level",
                    "AdoptIQ Build 115 and TACTrack as public summaries. No CSOne, no customer paths.",
                    chip="Feature",
                ),
                _card(
                    "Music producer gates",
                    "Logic home is green. LogicProMCP and Moises / Suno stay Jeff-gate until Jeff grants access.",
                    chip="Feature",
                ),
                _card(
                    "Parked lane",
                    "Closet shelf and catalog lanes stay parked unless Jeff unparks.",
                    status="parked",
                    chip="Feature",
                ),
                _card(
                    "Silence banner",
                    "If refresh is quiet for ~45m the page warns that chips may be stale.",
                    chip="Feature",
                ),
                _card(
                    "XSS-safe cards",
                    "html.escape + safeHref on server render. Soft-paint uses esc/safeHref. ASCII-safe JS (no smart quotes).",
                    chip="Feature",
                ),
                _card(
                    "Poll / decide race guards",
                    "pollSeq / pendingSeq drop stale fetches. decideBusy gates Approve / Hold / Deny double-clicks.",
                    chip="Feature",
                ),
                _card(
                    "Public Pages",
                    "Free-plan GitHub Pages from main. No secrets, tokens, Keeper material, or private handoff text.",
                    chip="Feature",
                ),
            ],
        },
    ]


def merge_first_class(sections: list[Any] | None) -> list[dict[str, Any]]:
    rest: list[dict[str, Any]] = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        if sec.get("id") in FIRST_CLASS_IDS:
            continue
        rest.append(sec)
    return first_class_sections() + rest
