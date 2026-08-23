#!/usr/bin/env python3
"""First-class Abilities / Decisions / How-this-board cards (honest, no fake buttons)."""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

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
# Pulse strip only. Extra probe rows never become a fourth invented pill.
AGENT_IDS = ("codex", "cursor", "claude")
AGENT_NAMES = {"codex": "Codex", "cursor": "Cursor", "claude": "Claude"}
AGENT_STATES = frozenset({"running", "idle", "installed", "down", "unknown"})
AGENT_STATE_CHIP = {
    "running": ("green", "Running"),
    "idle": ("yellow", "Idle"),
    "installed": ("parked", "Installed"),
    "down": ("red", "Down"),
    "unknown": ("parked", "Unknown"),
}
AGENT_FRESH_SEC = 45 * 60
AGENT_SECRET_WORDS = (
    "token",
    "secret",
    "bearer",
    "CSOne",
    "csone",
    "keeper",
    "password",
    "api_key",
    "apikey",
)
BOARD_TZ = "America/Chicago"


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


def status_from_fetch(
    repo: Any,
    *,
    override: str | None = None,
    jeff_gate: bool = False,
) -> str:
    """Lane status from live gh. Missing CI is OK (green). Inaccessible is parked.

    Curated override / jeff_gate win so Cisco high-level notes can stay yellow
    without pretending we fetched private CI. Empty ``ci: {}`` must not become
    Yellow -- that was inventing a problem when no Actions run exists.
    """
    if jeff_gate:
        return "jeff-gate"
    if override:
        return override
    if not isinstance(repo, dict) or not repo.get("accessible"):
        return "parked"
    ci = repo.get("ci")
    concl = ""
    if isinstance(ci, dict):
        concl = str(ci.get("conclusion") or "").strip().lower()
    if concl == "failure":
        return "red"
    try:
        n = int(repo.get("open_prs") or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return "yellow"
    if concl in ("", "success", "skipped", "cancelled"):
        return "green"
    return "yellow"


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


def split_pending(items: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Urgent inbox vs collapsed lower-risk. Unknown risk stays visible."""
    attn: list[dict[str, Any]] = []
    low: list[dict[str, Any]] = []
    for it in sort_pending(items):
        if pending_risk_rank(it) == PENDING_RISK_ORDER["low"]:
            low.append(it)
        else:
            attn.append(it)
    return attn, low


def parse_checked_at(ts: Any, default_tz: str = BOARD_TZ) -> float | None:
    """Unix seconds from an ISO timestamp. Naive values use Jeff TZ. Fail-closed."""
    if ts is None:
        return None
    raw = str(ts).strip()
    if not raw:
        return None
    try:
        t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        try:
            t = t.replace(tzinfo=ZoneInfo(default_tz))
        except Exception:
            return None
    return t.timestamp()


def agent_is_fresh(
    agent: Any,
    now: float | None = None,
    max_age_sec: int = AGENT_FRESH_SEC,
) -> bool:
    """True only when this row has a parseable checked_at inside the window."""
    if not isinstance(agent, dict):
        return False
    checked = parse_checked_at(agent.get("checked_at"))
    if checked is None:
        return False
    clock = time.time() if now is None else now
    # Invented future stamps fail closed. A few minutes of clock skew is OK.
    if checked - clock > 5 * 60:
        return False
    return (clock - checked) < max_age_sec


def _redact_agent_detail(detail: str) -> str:
    text = detail[:200]
    low = text.lower()
    for bad in AGENT_SECRET_WORDS:
        if bad.lower() in low:
            return "detail redacted"
    return text


def safe_agent(raw: Any, fallback_id: str) -> dict[str, Any]:
    """Public agent row. Unknown state if missing/invalid. No extra ids invented."""
    if not isinstance(raw, dict):
        raw = {}
    aid = str(raw.get("id") or fallback_id).strip().lower()[:32] or fallback_id
    name = str(raw.get("name") or AGENT_NAMES.get(aid) or aid.title())[:48]
    state = str(raw.get("state") or "unknown").strip().lower()
    if state not in AGENT_STATES:
        state = "unknown"
    detail = _redact_agent_detail(str(raw.get("detail") or ""))
    return {
        "id": aid,
        "name": name,
        "state": state,
        "detail": detail,
        "checked_at": raw.get("checked_at"),
    }


def default_agents(
    state: str = "unknown",
    detail: str = "No Mac probe yet -- run probe-agents-status.sh",
) -> list[dict[str, Any]]:
    return [safe_agent({"id": i, "name": AGENT_NAMES[i], "state": state, "detail": detail}, i) for i in AGENT_IDS]


def parse_agents_blob(blob: Any) -> list[dict[str, Any]] | None:
    """Parse probe JSON. Always returns the three known ids or None if unusable."""
    if blob is None:
        return None
    if isinstance(blob, str):
        blob = blob.strip()
        if not blob:
            return None
        try:
            blob = json.loads(blob)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if isinstance(blob, dict) and isinstance(blob.get("agents"), list):
        rows = blob["agents"]
    elif isinstance(blob, list):
        rows = blob
    else:
        return None
    by: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        agent = safe_agent(row, str(row.get("id") or "agent"))
        if agent["id"] in AGENT_IDS:
            by[agent["id"]] = agent
    if not by:
        return None
    out: list[dict[str, Any]] = []
    for i in AGENT_IDS:
        out.append(
            by.get(i)
            or safe_agent(
                {"id": i, "name": AGENT_NAMES[i], "state": "unknown", "detail": "missing from probe"},
                i,
            )
        )
    return out


def age_gate_agent(agent: Any, now: float | None = None) -> dict[str, Any]:
    """Running/idle/installed/down without a fresh timestamp become unknown."""
    row = safe_agent(agent, str((agent or {}).get("id") if isinstance(agent, dict) else "agent"))
    if agent_is_fresh(row, now=now):
        return row
    detail = (row.get("detail") or "stale").strip()
    if "probe stale" not in detail.lower():
        detail = (detail + " · probe stale (>45m)").strip(" ·")
    row["state"] = "unknown"
    row["detail"] = _redact_agent_detail(detail)
    return row


def age_gate_agents(agents: Any, now: float | None = None) -> list[dict[str, Any]]:
    """Per-row age gate. Never keep Running from a stale or untimestamped probe."""
    parsed = parse_agents_blob(agents)
    if not parsed:
        return default_agents()
    return [age_gate_agent(a, now=now) for a in parsed]


def resolve_agents(
    *,
    env_blob: str | None = None,
    file_texts: list[tuple[str, str]] | None = None,
    previous: Any = None,
    now: float | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """First parseable source wins, then age-gate. Do not fall through to an older Running."""
    candidates: list[tuple[Any, str]] = []
    if env_blob:
        candidates.append((env_blob, "env:AGENTS_STATUS_JSON"))
    for label, text in file_texts or []:
        candidates.append((text, label))
    if previous is not None:
        candidates.append((previous, "previous"))
    for blob, src in candidates:
        parsed = parse_agents_blob(blob)
        if not parsed:
            continue
        gated = [age_gate_agent(a, now=now) for a in parsed]
        if any(agent_is_fresh(a, now=now) for a in parsed):
            return gated, src
        return gated, src + ":stale->unknown"
    return default_agents(), "default:unknown"


def board_content_fingerprint(data: Any) -> str:
    """Stable paint key. Timestamps / agents_source / decisions are ignored."""
    if not isinstance(data, dict):
        return ""

    def agent_key(a: Any) -> list[Any]:
        if not isinstance(a, dict):
            return []
        return [a.get("id"), a.get("state"), a.get("detail")]

    def pending_key(it: Any) -> list[Any]:
        if not isinstance(it, dict):
            return []
        return [it.get("id"), it.get("title"), it.get("risk"), it.get("detail")]

    def project_key(p: Any) -> list[Any]:
        if not isinstance(p, dict):
            return []
        ci = p.get("ci") if isinstance(p.get("ci"), dict) else {}
        return [
            p.get("name"),
            p.get("status"),
            p.get("chip"),
            p.get("notes"),
            p.get("open_prs"),
            p.get("release"),
            p.get("tip_sha"),
            ci.get("conclusion") if ci else None,
        ]

    sections = []
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        sections.append(
            [
                sec.get("id"),
                sec.get("title"),
                [project_key(p) for p in (sec.get("projects") or [])],
            ]
        )
    payload = {
        "pending": [pending_key(it) for it in (data.get("pending") or [])],
        "agents": [agent_key(a) for a in (data.get("agents") or [])],
        "sections": sections,
        "fetched": data.get("fetched_repos") or [],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


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
                    "Client fetches status.json every 30s (pauses when the tab is hidden). Repaints when board content changes -- not on every 15m Actions timestamp. No full reload.",
                    chip="Feature",
                ),
                _card(
                    "Agents strip",
                    "Codex / Cursor / Claude from a Mac probe. Stale or untimestamped probes paint Unknown. Never invent Running. Token-like words redacted.",
                    chip="Feature",
                ),
                _card(
                    "Silence banner",
                    "Actions cadence is ~15m. If refresh is quiet for ~45m the page warns that chips may be stale.",
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
