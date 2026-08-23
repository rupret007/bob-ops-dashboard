#!/usr/bin/env python3
"""First-class Abilities / Decisions / How-this-board cards (honest, no fake buttons)."""
from __future__ import annotations

from typing import Any

FIRST_CLASS_IDS = ("abilities", "controls", "features")
CONTROL_ACTIONS = frozenset({"refresh-hint", "open-repo", "mark-reviewed"})
# Visual board: pulse (agents) is outside sections. Ops lanes after pending.
OPS_SECTION_ORDER = (
    "live-shipping",
    "active-agents",
    "cisco",
    "messaging",
    "music-producer",
    "parked",
)
PRIMARY_SECTION_IDS = frozenset({"live-shipping"})
SECONDARY_SECTION_IDS = frozenset({"cisco", "messaging", "music-producer", "parked"})
COLLAPSED_SECTION_IDS = frozenset({"abilities", "features"})
# Agents live in the top pulse strip -- do not paint as a card grid.
PULSE_SECTION_IDS = frozenset({"active-agents"})
# Section-type labels -- noisy on phone. Real status chips (Green / Jeff-gate) stay.
SECTION_TYPE_CHIPS = frozenset({"Ability", "Control", "Feature"})
ATTENTION_ORDER = {
    "red": 0,
    "jeff-gate": 1,
    "yellow": 2,
    "green": 3,
    "parked": 4,
}
PENDING_RISK_ORDER = {"high": 0, "medium": 1, "low": 2}


def drop_leftover_verify(status: Any) -> bool:
    """Fail-closed: never keep an OTP verify block on a public board."""
    if not isinstance(status, dict) or "verify" not in status:
        return False
    status.pop("verify", None)
    return True


def visible_chip(project: Any) -> str | None:
    """Return a status chip label, or None when it would only repeat the section name."""
    if not isinstance(project, dict):
        return None
    label = str(project.get("chip") or "").strip()
    if not label or label in SECTION_TYPE_CHIPS:
        return None
    return label


def presentation(section_id: Any) -> str:
    """How a section should paint: pending, pulse, primary, secondary, footer, other."""
    sid = str(section_id or "")
    if sid == "controls":
        return "pending"
    if sid in PULSE_SECTION_IDS:
        return "pulse"
    if sid in PRIMARY_SECTION_IDS:
        return "primary"
    if sid in SECONDARY_SECTION_IDS:
        return "secondary"
    if sid in COLLAPSED_SECTION_IDS:
        return "footer"
    return "secondary"


def attention_rank(project: Any) -> int:
    """Lower = needs Jeff sooner. Unknown statuses sort last."""
    if not isinstance(project, dict):
        return 9
    return ATTENTION_ORDER.get(str(project.get("status") or ""), 5)


def is_quiet_lane(project: Any) -> bool:
    """Green lanes are scan-quiet -- hide essay notes on the phone."""
    if not isinstance(project, dict):
        return False
    return str(project.get("status") or "") == "green"


def compact_signal(project: Any) -> str | None:
    """One scan signal. Skip SHA / product SHA / '0 open PRs' / success-CI essays."""
    if not isinstance(project, dict):
        return None
    rel = str(project.get("release") or "").strip()
    if rel:
        return rel
    n = project.get("open_prs")
    try:
        count = int(n) if n is not None else 0
    except (TypeError, ValueError):
        count = 0
    if count > 0:
        return str(count) + (" open PR" if count == 1 else " open PRs")
    ci = project.get("ci")
    if isinstance(ci, dict):
        concl = str(ci.get("conclusion") or "").strip().lower()
        if concl == "failure":
            return "CI fail"
        if concl and concl not in ("success", "skipped", "cancelled"):
            return concl
    return None


def pending_risk_rank(item: Any) -> int:
    """Lower = needs Jeff sooner. Unknown risk sorts last."""
    if not isinstance(item, dict):
        return 9
    return PENDING_RISK_ORDER.get(str(item.get("risk") or "").lower(), 5)


def sort_pending(items: Any) -> list[dict[str, Any]]:
    """High-risk first so the inbox is a ten-second scan, not a card stack."""
    rows = [it for it in (items or []) if isinstance(it, dict)]
    return sorted(rows, key=pending_risk_rank)


def short_note(notes: Any, limit: int = 88) -> str:
    """Phone-safe note. CSS also clamps; this keeps first-paint copy short."""
    text = " ".join(str(notes or "").split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(".,;:")
    if len(cut) < 24:
        cut = text[: limit - 1]
    return cut + "..."


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
            "title": "Decisions",
            "projects": [
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
            "title": "How this board works",
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
    """Pending first, then live shipping / agents / quieter ops; abilities + plumbing last."""
    fc = {s["id"]: s for s in first_class_sections()}
    rest: list[dict[str, Any]] = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        if sec.get("id") in FIRST_CLASS_IDS:
            continue
        rest.append(sec)
    rest_by = {s.get("id"): s for s in rest}
    out: list[dict[str, Any]] = [fc["controls"]]
    seen: set[str] = {"controls"}
    for sid in OPS_SECTION_ORDER:
        row = rest_by.get(sid)
        if row:
            out.append(row)
            seen.add(str(sid))
    for sec in rest:
        sid = str(sec.get("id") or "")
        if sid in seen:
            continue
        out.append(sec)
        seen.add(sid)
    out.append(fc["abilities"])
    out.append(fc["features"])
    return out
