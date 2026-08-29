#!/usr/bin/env python3
"""First-class Abilities / Decisions / How-this-board cards (honest, no fake buttons)."""
from __future__ import annotations

import html as html_lib
import json
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

FIRST_CLASS_IDS = ("abilities", "controls", "features")
CONTROL_ACTIONS = frozenset({"refresh-hint", "open-repo", "mark-reviewed"})
# Visual board: pulse (agents) is outside sections. Ops lanes after pending.
OPS_SECTION_ORDER = (
    "live-shipping",
    "apps-utilities",
    "active-agents",
    "cisco",
    "messaging",
    "private-media",
    "parked",
)
PRIMARY_SECTION_IDS = frozenset({"live-shipping"})
SECONDARY_SECTION_IDS = frozenset(
    {"apps-utilities", "cisco", "messaging", "private-media", "parked"}
)
COLLAPSED_SECTION_IDS = frozenset({"abilities", "features"})
# Agents live in the top pulse strip -- do not paint as a card grid.
PULSE_SECTION_IDS = frozenset({"active-agents"})
# Phone tabs use types already on the board. Do not invent ids.
TYPE_TAB_IDS = (
    "controls",
    "live-shipping",
    "apps-utilities",
    "cisco",
    "messaging",
    "private-media",
    "parked",
)
TYPE_TAB_LABELS = {
    "controls": "Decisions",
    "live-shipping": "Live",
    "apps-utilities": "Apps",
    "cisco": "Cisco",
    "messaging": "Bob",
    "private-media": "Media",
    "parked": "Parked",
}
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
# Live CI honesty. Jeff-gate / release chips must not hide a red or running tip.
CI_FAIL_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "action_required", "startup_failure"}
)
CI_ACTIVE_CONCLUSIONS = frozenset(
    {"in_progress", "queued", "waiting", "pending", "requested"}
)
CI_RUNNING_CONCLUSIONS = frozenset({"in_progress", "waiting"})
CI_PENDING_CONCLUSIONS = frozenset({"queued", "pending", "requested"})
CI_OK_CONCLUSIONS = frozenset({"", "success", "skipped", "cancelled"})
# Skipped / cancelled helpers are "OK" for lane color, but they are not the
# tip test run. Success must beat them, and they must not become Open CI.
CI_SKIP_CONCLUSIONS = frozenset({"skipped", "cancelled"})
# Pages / docs deploys and this board's scheduled refresh publisher are not
# test CI. They must not hide a tip fail, become Open CI, or paint Red /
# CI running / CI pending on the public dashboard lane.
CI_NOISE_MARKERS = (
    "pages-build-deployment",
    "pages build and deployment",
    "github-pages",
    "github pages",
    "deploy-pages",
    "deploy pages",
    "refresh bob ops dashboard",
)
CI_NOISE_FILENAMES = frozenset(
    {"pages.yml", "pages.yaml", "refresh-dashboard.yml"}
)
DECISION_VERBS = frozenset({"APPROVE", "HOLD", "DENY"})
DECISION_ISSUE_NEW = "https://github.com/rupret007/bob-ops-dashboard/issues/new"
COORD_HOME = "rupret007/Bob-the-Bot"
COORD_AGENTS = frozenset({"none", "codex", "grok", "claude"})
COORD_TITLE_RE = re.compile(
    r"^coord:\s*(?:(?P<owner>[A-Za-z0-9_.-]+)/)?(?P<repo>[A-Za-z0-9_.-]+)\s*$",
    re.I,
)
COORD_FIELD_RE = re.compile(r"^-\s*([a-z_]+)\s*:\s*(.*)$", re.I)
PENDING_ID_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
BC_ID_RE = re.compile(
    r"^bc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)
BC_ID_FIND_RE = re.compile(
    r"bc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
AGENT_HREF_RE = re.compile(
    r"https://cursor\.com/agents/(bc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)
AGENT_BCID_QUERY_RE = re.compile(
    r"https://cursor\.com/background-agent\?[^\s\"'<>]*bcId="
    r"(bc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)
GITHUB_REPO_PATH = r"(?:rupret007/[A-Za-z0-9._-]+|0xc0re/barker)"
PR_URL_RE = re.compile(
    rf"^https://github\.com/{GITHUB_REPO_PATH}/pull/[1-9][0-9]*$", re.I
)
ACTIONS_URL_RE = re.compile(
    rf"^https://github\.com/{GITHUB_REPO_PATH}/actions/runs/[1-9][0-9]*$", re.I
)
REPO_URL_RE = re.compile(rf"^https://github\.com/{GITHUB_REPO_PATH}$", re.I)
PULLS_URL_RE = re.compile(rf"^https://github\.com/{GITHUB_REPO_PATH}/pulls$", re.I)
SAFE_RELEASE_TAG = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
RELEASE_TAG_RE = re.compile(rf"^{SAFE_RELEASE_TAG}$")
RELEASE_LATEST_URL_RE = re.compile(
    rf"^https://github\.com/{GITHUB_REPO_PATH}/releases/latest$", re.I
)
RELEASE_TAG_URL_RE = re.compile(
    rf"^https://github\.com/{GITHUB_REPO_PATH}/releases/tag/{SAFE_RELEASE_TAG}$", re.I
)
LATEST_VS_SOURCE_SIGNAL = "Latest != source"
TURDANOID_HUB_URL = "https://rupret007.github.io/Turdanoid/hub.html"
CLOUD_AGENT_LIMIT = 3


def drop_leftover_verify(status: Any) -> bool:
    """Fail-closed: never keep an OTP verify block on a public board."""
    if not isinstance(status, dict) or "verify" not in status:
        return False
    status.pop("verify", None)
    return True


def _clean_public_url(url: Any) -> str:
    """Strip, unescape, drop query/hash. Empty when unsafe characters remain."""
    if url is None:
        return ""
    s = html_lib.unescape(str(url).strip())
    if not s or any(c in s for c in (" ", "\n", "\r", "\t", "<", ">", '"', "'", "\\")):
        return ""
    s = s.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return s


def safe_agent_url(url: Any) -> str:
    """Only https://cursor.com/agents/bc-<uuid>. Never invent a bc-id."""
    s = _clean_public_url(url)
    m = AGENT_HREF_RE.match(s)
    if not m:
        return ""
    return "https://cursor.com/agents/" + m.group(1).lower()


def safe_pr_url(url: Any) -> str:
    """Only a rupret007 pull request URL."""
    s = _clean_public_url(url)
    return s if PR_URL_RE.match(s) else ""


def safe_actions_url(url: Any) -> str:
    """Only a rupret007 Actions run URL."""
    s = _clean_public_url(url)
    return s if ACTIONS_URL_RE.match(s) else ""


def safe_repo_url(url: Any) -> str:
    """Only a rupret007 repository home URL."""
    s = _clean_public_url(url)
    return s if REPO_URL_RE.match(s) else ""


def safe_pulls_url(url: Any) -> str:
    """Only a rupret007 repo /pulls index. Never invent a repo name."""
    s = _clean_public_url(url)
    return s if PULLS_URL_RE.match(s) else ""


def safe_release_tag(tag: Any) -> str:
    """Allowlisted GitHub release tag. Empty when the name could be a path."""
    s = str(tag or "").strip()
    if not RELEASE_TAG_RE.fullmatch(s) or ".." in s or "/" in s:
        return ""
    return s


def safe_release_url(url: Any) -> str:
    """Only an allowlisted Latest pointer or tag URL. Never invent a repo."""
    s = _clean_public_url(url)
    if RELEASE_LATEST_URL_RE.match(s) or RELEASE_TAG_URL_RE.match(s):
        return s
    return ""


def latest_release_url_from_repo(url: Any) -> str:
    """Derive /releases/latest from an allowlisted repo home. Empty if unknown."""
    repo = safe_repo_url(url)
    return (repo + "/releases/latest") if repo else ""


def release_matches_tip(project: Any) -> bool | None:
    """True when Latest SHA is this tip, False when proven different, None if unknown."""
    if not isinstance(project, dict):
        return None
    tip = str(project.get("tip_sha") or "").strip()
    rel = str(project.get("release_sha") or "").strip()
    if not tip or not rel:
        return None
    return sha_matches_tip({"head_sha": rel}, tip)


def safe_game_url(url: Any) -> str:
    """Allow only the explicitly public Turdanoid game hub."""
    s = _clean_public_url(url)
    return TURDANOID_HUB_URL if s.lower() == TURDANOID_HUB_URL.lower() else ""


def pulls_url_from_repo(url: Any) -> str:
    """Derive /pulls from an allowlisted repo home. Empty if the repo is unknown."""
    repo = safe_repo_url(url)
    return (repo + "/pulls") if repo else ""


def prune_closed_parked_prs(
    sections: list[Any] | None, open_pr_urls: Any
) -> list[dict[str, Any]]:
    """Keep a parked PR only when the current refresh proved it is open."""
    current = {
        url
        for raw in (open_pr_urls or [])
        if (url := safe_pr_url(raw))
    }
    out: list[dict[str, Any]] = []
    for raw_section in sections or []:
        if not isinstance(raw_section, dict):
            continue
        section = dict(raw_section)
        projects: list[Any] = []
        for raw_project in raw_section.get("projects") or []:
            if not isinstance(raw_project, dict):
                continue
            project = dict(raw_project)
            parked_pr = ""
            if str(project.get("status") or "").lower() == "parked":
                parked_pr = safe_pr_url(project.get("open_pr_url")) or safe_pr_url(
                    project.get("url")
                )
            if parked_pr and parked_pr not in current:
                continue
            projects.append(project)
        section["projects"] = projects
        out.append(section)
    return out


def extract_agent_url(text: Any) -> str:
    """First real cursor.com agent URL or bcId= in text. Empty if none."""
    raw = html_lib.unescape(str(text or ""))
    m = AGENT_HREF_RE.search(raw)
    if m:
        return "https://cursor.com/agents/" + m.group(1).lower()
    q = AGENT_BCID_QUERY_RE.search(raw)
    if q:
        return "https://cursor.com/agents/" + q.group(1).lower()
    return ""


def agent_url_from_fields(raw: Any) -> str:
    """URL from url/agent_url or a provided bc_id. Does not mint UUIDs."""
    if not isinstance(raw, dict):
        return ""
    direct = safe_agent_url(raw.get("url") or raw.get("agent_url"))
    if direct:
        return direct
    extracted = extract_agent_url(raw.get("url") or raw.get("agent_url") or raw.get("detail") or "")
    if extracted:
        return extracted
    bc = str(raw.get("bc_id") or raw.get("bcId") or "").strip().lower()
    if BC_ID_RE.match(bc):
        return "https://cursor.com/agents/" + bc
    return ""


def is_draft_pr(raw: Any) -> bool:
    """True only when GitHub already marked the PR a draft. Missing field is not a draft."""
    if not isinstance(raw, dict):
        return False
    if "draft" in raw:
        return bool(raw.get("draft"))
    if "isDraft" in raw:
        return bool(raw.get("isDraft"))
    return False


def pick_open_pr(prs: Any) -> dict[str, Any] | None:
    """Newest ready open PR. Draft / parked leftovers are never the featured PR."""
    parsed: list[dict[str, Any]] = []
    for p in prs or []:
        if not isinstance(p, dict):
            continue
        if is_draft_pr(p):
            continue
        url = safe_pr_url(p.get("html_url") or p.get("url"))
        if not url:
            continue
        draft = False
        ts = str(
            p.get("updated_at")
            or p.get("updatedAt")
            or p.get("created_at")
            or p.get("createdAt")
            or ""
        )
        num = p.get("number")
        try:
            number = int(num) if num is not None else None
        except (TypeError, ValueError):
            number = None
        parsed.append(
            {
                "url": url,
                "number": number,
                "title": str(p.get("title") or "")[:160],
                "draft": draft,
                "updated": ts,
            }
        )
    if not parsed:
        return None
    ready = [p for p in parsed if not p["draft"]]
    if not ready:
        return None
    pool = sorted(ready, key=lambda p: p.get("updated") or "", reverse=True)
    top = dict(pool[0])
    top.pop("updated", None)
    return top


def detect_linear_pr_stack(prs: Any, default_branch: Any) -> list[dict[str, Any]]:
    """Return a complete base-to-tip PR chain, otherwise fail closed.

    A stack is only useful when every open PR belongs to one same-repository,
    non-branching chain rooted at the default branch. Partial or ambiguous
    ordering is worse than the honest open-PR count, so any malformed row,
    fork head, duplicate base/head/number, cycle, or unrelated PR returns
    an empty list.
    """
    branch = str(default_branch or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}", branch):
        return []
    if ".." in branch or "@{" in branch or "//" in branch or branch.endswith(("/", ".")):
        return []
    if not isinstance(prs, list) or len(prs) < 2:
        return []

    parsed: list[dict[str, Any]] = []
    repo_name = ""
    numbers: set[int] = set()
    heads: set[str] = set()
    bases: set[str] = set()
    for raw in prs:
        if not isinstance(raw, dict):
            return []
        url = safe_pr_url(raw.get("html_url") or raw.get("url"))
        if not url:
            return []
        url_match = re.fullmatch(
            rf"https://github\.com/({GITHUB_REPO_PATH})/pull/([1-9][0-9]*)",
            url,
            re.I,
        )
        if not url_match:
            return []
        this_repo = url_match.group(1).lower()
        if repo_name and this_repo != repo_name:
            return []
        repo_name = this_repo

        if isinstance(raw.get("number"), bool):
            return []
        try:
            number = int(raw.get("number"))
        except (TypeError, ValueError):
            return []
        if number <= 0 or number in numbers or number != int(url_match.group(2)):
            return []

        base = raw.get("base") if isinstance(raw.get("base"), dict) else {}
        head = raw.get("head") if isinstance(raw.get("head"), dict) else {}
        base_ref = str(base.get("ref") or "").strip()
        head_ref = str(head.get("ref") or "").strip()
        for ref in (base_ref, head_ref):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}", ref):
                return []
            if ".." in ref or "@{" in ref or "//" in ref or ref.endswith(("/", ".")):
                return []
        if head_ref == base_ref or head_ref in heads or base_ref in bases:
            return []

        base_repo = base.get("repo") if isinstance(base.get("repo"), dict) else {}
        head_repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}
        base_full = str(base_repo.get("full_name") or "").strip().lower()
        head_full = str(head_repo.get("full_name") or "").strip().lower()
        # GitHub's pulls endpoint supplies these fields. Missing/deleted/forked
        # head metadata is not enough evidence to advertise a safe stack.
        if base_full != repo_name or head_full != repo_name:
            return []

        numbers.add(number)
        heads.add(head_ref)
        bases.add(base_ref)
        parsed.append(
            {
                "number": number,
                "url": url,
                "base": base_ref,
                "head": head_ref,
            }
        )

    roots = [p for p in parsed if p["base"] == branch]
    if len(roots) != 1:
        return []
    by_base = {p["base"]: p for p in parsed}
    ordered: list[dict[str, Any]] = []
    seen: set[int] = set()
    current: dict[str, Any] | None = roots[0]
    while current is not None:
        number = int(current["number"])
        if number in seen:
            return []
        seen.add(number)
        ordered.append({"number": number, "url": current["url"]})
        current = by_base.get(str(current["head"]))
    if len(ordered) != len(parsed):
        return []
    return ordered


def _stack_signal(project: dict[str, Any]) -> str:
    """Validated compact label for a complete stack; empty when uncertain."""
    stack = project.get("open_pr_stack")
    if not isinstance(stack, list) or len(stack) < 2:
        return ""
    try:
        count = int(project.get("open_prs") or 0)
    except (TypeError, ValueError):
        return ""
    if count != len(stack):
        return ""
    numbers: list[int] = []
    seen: set[int] = set()
    for raw in stack:
        if not isinstance(raw, dict) or isinstance(raw.get("number"), bool):
            return ""
        number = raw.get("number")
        if not isinstance(number, int):
            return ""
        url = safe_pr_url(raw.get("url"))
        if number <= 0 or number in seen or not url or not url.endswith("/pull/" + str(number)):
            return ""
        seen.add(number)
        numbers.append(number)
    if len(numbers) <= 4:
        return "Stack " + " -> ".join("#" + str(number) for number in numbers)
    return str(len(numbers)) + "-PR stack"


def parse_cloud_agent_row(raw: Any) -> dict[str, Any] | None:
    """One cloud-agent row. Requires a real agent URL. State is never invented Running."""
    if not isinstance(raw, dict):
        return None
    url = agent_url_from_fields(raw)
    if not url:
        return None
    bc = url.rsplit("/", 1)[-1].lower()
    name = str(raw.get("name") or "Cloud").strip()[:32] or "Cloud"
    detail = _redact_agent_detail(str(raw.get("detail") or "Cloud Agent"))
    return {
        "id": bc,
        "name": name,
        "state": "unknown",
        "detail": detail,
        "url": url,
        "pr_url": safe_pr_url(raw.get("pr_url") or raw.get("pr")),
        "checked_at": raw.get("checked_at"),
    }


def merge_cloud_agents(*groups: Any, limit: int = CLOUD_AGENT_LIMIT) -> list[dict[str, Any]]:
    """Dedupe by agent URL. Only rows with a real bc-id URL. Cap the pulse strip."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for group in groups:
        for raw in group or []:
            row = parse_cloud_agent_row(raw)
            if not row or row["url"] in seen:
                continue
            seen.add(row["url"])
            out.append(row)
            if len(out) >= limit:
                return out
    return out


def extract_cloud_agents_from_prs(prs: Any, *, limit: int = CLOUD_AGENT_LIMIT) -> list[dict[str, Any]]:
    """Cloud agents from ready open same-repository PRs. Drafts/parked leftovers never paint."""
    rows: list[dict[str, Any]] = []
    items = [p for p in (prs or []) if isinstance(p, dict)]
    items.sort(
        key=lambda p: str(p.get("updated_at") or p.get("updatedAt") or ""),
        reverse=True,
    )
    for p in items:
        if str(p.get("state") or "").strip().lower() != "open":
            continue
        if is_draft_pr(p):
            continue
        pr_url = safe_pr_url(p.get("html_url") or p.get("url"))
        base = p.get("base") if isinstance(p.get("base"), dict) else {}
        head = p.get("head") if isinstance(p.get("head"), dict) else {}
        base_repo = base.get("repo") if isinstance(base.get("repo"), dict) else {}
        head_repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}
        base_full = str(base_repo.get("full_name") or "").strip().lower()
        head_full = str(head_repo.get("full_name") or "").strip().lower()
        if not pr_url or not base_full or base_full != head_full:
            continue
        if not pr_url.lower().startswith("https://github.com/" + base_full + "/pull/"):
            continue
        url = extract_agent_url(
            " ".join(
                [
                    str(p.get("body") or ""),
                    str(p.get("title") or ""),
                    str(p.get("html_url") or ""),
                ]
            )
        )
        if not url:
            continue
        num = p.get("number")
        name = "PR #" + str(num) if num not in (None, "") else "Cloud"
        title = str(p.get("title") or "").strip()
        rows.append(
            {
                "name": name,
                "detail": title[:160] or "Open Cloud Agent",
                "url": url,
                "pr_url": pr_url,
            }
        )
    return merge_cloud_agents(rows, limit=limit)


def parse_cloud_agents(blob: Any) -> list[dict[str, Any]]:
    """Probe/status cloud_agents plus extra agent rows that already have a valid URL."""
    if blob is None:
        return []
    if isinstance(blob, str):
        blob = blob.strip()
        if not blob:
            return []
        try:
            blob = json.loads(blob)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    groups: list[Any] = []
    if isinstance(blob, dict):
        if isinstance(blob.get("cloud_agents"), list):
            groups.append(blob["cloud_agents"])
        extra = []
        for row in blob.get("agents") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("id") or "").strip().lower() in AGENT_IDS:
                continue
            extra.append(row)
        if extra:
            groups.append(extra)
    elif isinstance(blob, list):
        groups.append(blob)
    return merge_cloud_agents(*groups)


def lane_hrefs(project: Any) -> dict[str, str]:
    """Fail-closed lane taps. Missing URLs stay empty — no fake PR/CI/agent."""
    if not isinstance(project, dict):
        return {}
    ci = project.get("ci") if isinstance(project.get("ci"), dict) else {}
    repo = safe_repo_url(project.get("repo_url") or project.get("html_url"))
    if not repo:
        raw = project.get("url")
        repo = safe_repo_url(raw)
    pr = safe_pr_url(project.get("open_pr_url")) or safe_pr_url(project.get("url"))
    agent = safe_agent_url(project.get("agent_url")) or agent_url_from_fields(project)
    game = safe_game_url(project.get("live_game_url"))
    concl = str(ci.get("conclusion") or "").strip().lower()
    actions = ""
    if concl not in CI_SKIP_CONCLUSIONS:
        actions = safe_actions_url(ci.get("html_url") or project.get("ci_url"))
    title = pr or repo
    out = {"title": title}
    if agent:
        out["agent"] = agent
    if pr:
        out["pr"] = pr
    if repo:
        out["repo"] = repo
    if actions:
        out["ci"] = actions
    if game:
        out["game"] = game
    return out


def signal_href(project: Any) -> str:
    """Tap target for the compact signal. Empty means dead text; never invent.

    CI fail/running/pending already tap the Actions run when a run URL is
    known. N open PRs must do the same: one known PR opens that PR;
    two or more open the repo pulls list. Never pretend one PR is all of
    them. Never invent a PR number or host.
    """
    if not isinstance(project, dict):
        return ""
    signal = compact_signal(project)
    if not signal:
        return ""
    hrefs = lane_hrefs(project)
    text = str(signal)
    if text.endswith(" lease"):
        return ""
    if text.startswith("CI"):
        return hrefs.get("ci") or ""
    if text.startswith("Stack ") or text.endswith("-PR stack") or "open PR" in text:
        try:
            n = int(project.get("open_prs") or 0)
        except (TypeError, ValueError):
            n = 0
        pulls = pulls_url_from_repo(hrefs.get("repo") or "")
        if n > 1:
            return pulls
        return hrefs.get("pr") or pulls
    rel = str(project.get("release") or "").strip()
    if text == LATEST_VS_SOURCE_SIGNAL or (rel and text == rel):
        return latest_release_url_from_repo(hrefs.get("repo") or "") or safe_release_url(
            project.get("release_url")
        )
    return ""


def visible_chip(project: Any) -> str | None:
    """Return a status chip label, or None when it would only repeat the section name."""
    if not isinstance(project, dict):
        return None
    label = str(project.get("chip") or "").strip()
    if not label or label in SECTION_TYPE_CHIPS:
        return None
    return label


def mac_probe_known(agent: Any) -> bool:
    """True when a Codex/Cursor/Claude pill has a live non-unknown state."""
    if not isinstance(agent, dict):
        return False
    aid = str(agent.get("id") or "")
    if aid not in AGENT_IDS:
        return False
    state = str(agent.get("state") or "unknown").strip().lower()
    return state in AGENT_STATES and state != "unknown"


def compact_unknown_mac_probes(agents: Any) -> bool:
    """True when the box has no live Mac probe. Never treat missing as Running."""
    rows = [
        a
        for a in (agents or [])
        if isinstance(a, dict) and str(a.get("id") or "") in AGENT_IDS
    ]
    return not any(mac_probe_known(a) for a in rows)


def unknown_mac_probes_html(agents: Any = None) -> str:
    """One honest first-screen line. Visible text is never Running."""
    detail = "No Mac probe yet -- run probe-agents-status.sh"
    for a in agents or []:
        if not isinstance(a, dict) or str(a.get("id") or "") not in AGENT_IDS:
            continue
        text = str(a.get("detail") or "").strip()
        if text:
            detail = text
            break
    return (
        '<p class="agents-unknown" id="agents-unknown" title="'
        + html_lib.escape(detail)
        + '">Agents unknown</p>'
    )


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


def tab_id(raw: Any) -> str:
    """Allowlisted type id only. Invented hashes and leftover ids stay empty."""
    sid = str(raw or "")
    return sid if sid in TYPE_TAB_LABELS else ""


def tab_label(section_id: Any) -> str:
    """Short phone label for an existing type. Empty when the id is unknown."""
    sid = tab_id(section_id)
    return TYPE_TAB_LABELS.get(sid, "")


def is_type_tab(section_id: Any) -> bool:
    """True for the real project-type sections that get a phone tab."""
    return bool(tab_id(section_id))


def type_tab_ids_for(sections: Any, pending: Any) -> list[str]:
    """Tabs to paint. Decisions only when something needs a yes."""
    present = {
        str(sec.get("id") or "")
        for sec in (sections or [])
        if isinstance(sec, dict)
    }
    out: list[str] = []
    for sid in TYPE_TAB_IDS:
        if sid == "controls":
            if any(isinstance(it, dict) for it in (pending or [])):
                out.append(sid)
            continue
        if sid in present:
            out.append(sid)
    return out


def glance_pending_title(item: Any) -> str:
    """Trimmed pending title for the first-screen glance. Cap ~28 chars."""
    if not isinstance(item, dict):
        return ""
    title = str(item.get("title") or "").strip()
    if len(title) > 28:
        title = title[:28].rstrip()
    return title


def glance_status(pending: Any, sections: Any) -> dict[str, str]:
    """One short first-screen line. Names the gate, not a yes-count."""
    rows = sort_pending(pending)
    if rows:
        title = glance_pending_title(rows[0]) or "Pending"
        n = len(rows)
        text = title if n == 1 else title + " + " + str(n - 1) + " more"
        return {"text": text, "tab": "controls"}
    worst_rank = 99
    worst_id = ""
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        sid = tab_id(sec.get("id"))
        if not sid or sid == "controls":
            continue
        for project in sec.get("projects") or []:
            rank = attention_rank(project)
            if rank < worst_rank:
                worst_rank = rank
                worst_id = sid
    if worst_rank <= 2 and worst_id:
        label = tab_label(worst_id)
        if worst_rank == 0:
            return {"text": label + " is red", "tab": worst_id}
        if worst_rank == 1:
            return {"text": label + " is waiting on Jeff", "tab": worst_id}
        return {"text": label + " needs a look", "tab": worst_id}
    return {"text": "Quiet", "tab": ""}


def glance_html(pending: Any, sections: Any) -> str:
    """First-screen status. Taps an existing type when there is somewhere to go."""
    glance = glance_status(pending, sections)
    text = html_lib.escape(str(glance.get("text") or "Quiet"))
    sid = tab_id(glance.get("tab"))
    extra = ' data-tab="' + html_lib.escape(sid) + '"' if sid else ""
    return (
        '<button type="button" class="board-glance" id="board-glance"'
        + extra
        + ">"
        + text
        + "</button>"
    )


def type_tabs_html(sections: Any, pending: Any, selected: Any = "") -> str:
    """Phone tab bar for types already in the data. First paint selects none."""
    want = tab_id(selected)
    buttons: list[str] = []
    for sid in type_tab_ids_for(sections, pending):
        label = html_lib.escape(tab_label(sid))
        sid_e = html_lib.escape(sid)
        aria = "true" if sid == want else "false"
        buttons.append(
            '<button type="button" role="tab" id="tab-'
            + sid_e
            + '" data-tab="'
            + sid_e
            + '" aria-controls="'
            + sid_e
            + '" aria-selected="'
            + aria
            + '">'
            + label
            + "</button>"
        )
    return (
        '<nav class="type-tabs" id="type-tabs" role="tablist" '
        'aria-label="Project type">' + "".join(buttons) + "</nav>"
    )


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


def ci_conclusion(repo: Any) -> str:
    """Normalized tip CI conclusion. Empty when missing or not a dict."""
    if not isinstance(repo, dict):
        return ""
    ci = repo.get("ci")
    if not isinstance(ci, dict):
        return ""
    return str(ci.get("conclusion") or "").strip().lower()


def is_unexecuted_run(run: Any) -> bool:
    """Hosted job with empty steps / no runner / never started is unexecuted.

    Missing jobs/run_started_at fields are unknown, not unexecuted. The Actions
    list payload usually omits jobs; only an explicit empty job list or a
    present-but-empty start time can call a hosted failure unexecuted.
    """
    if not isinstance(run, dict):
        return True
    if "jobs" in run:
        jobs = run.get("jobs")
        if not isinstance(jobs, list) or not jobs:
            return True
        for job in jobs:
            if not isinstance(job, dict):
                continue
            runner = job.get("runner_name") or job.get("runner_id")
            steps = job.get("steps")
            if runner or (isinstance(steps, list) and steps):
                return False
        return True
    if "run_started_at" in run or "runStartedAt" in run:
        started = run.get("run_started_at") or run.get("runStartedAt")
        concl = str(run.get("conclusion") or "").strip().lower()
        if concl in CI_FAIL_CONCLUSIONS and not started:
            return True
    return False


def public_high_level_ci(ci: Any) -> dict[str, str]:
    """Private / high-level lanes never publish a hosted failure as public CI."""
    if not isinstance(ci, dict):
        return {}
    concl = str(ci.get("conclusion") or "").strip().lower()
    if not concl or concl in CI_FAIL_CONCLUSIONS:
        return {}
    return {"conclusion": concl}


def is_ci_noise(run: Any) -> bool:
    """True for Pages / docs deploy / this board's refresh publisher runs."""
    if not isinstance(run, dict):
        return True
    name = str(run.get("name") or "").strip().lower()
    path = str(run.get("path") or "").strip().lower()
    text = name + " " + path
    if any(marker in text for marker in CI_NOISE_MARKERS):
        return True
    base = path.rsplit("/", 1)[-1]
    return base in CI_NOISE_FILENAMES


def _run_sha7(run: Any) -> str:
    if not isinstance(run, dict):
        return ""
    return str(run.get("head_sha") or "").strip().lower()[:7]


def sha_matches_tip(run: Any, tip_sha: Any) -> bool:
    """True when the run is for this tip. Empty tip matches any run."""
    want = str(tip_sha or "").strip().lower()
    if not want:
        return True
    got = str((run or {}).get("head_sha") if isinstance(run, dict) else "").strip().lower()
    if not got:
        return False
    return got.startswith(want) or want.startswith(got[:7])


def _normalize_run(run: dict[str, Any], branch: str) -> dict[str, Any]:
    status = str(run.get("status") or "").strip().lower()
    concl = str(run.get("conclusion") or "").strip().lower()
    if status and status != "completed":
        concl = status
    return {
        "name": run.get("name"),
        "conclusion": concl or None,
        "branch": branch,
        "sha": (run.get("head_sha") or "")[:7] or None,
        "created": run.get("created_at"),
        "html_url": safe_actions_url(run.get("html_url")),
    }


def _conclusion_rank(concl: Any) -> int:
    """Lower is worse for a phone scan. Fail beats running beats success beats skip."""
    c = str(concl or "").strip().lower()
    if c in CI_FAIL_CONCLUSIONS:
        return 0
    if c in CI_ACTIVE_CONCLUSIONS:
        return 1
    if c in CI_SKIP_CONCLUSIONS:
        return 4
    if c in CI_OK_CONCLUSIONS:
        return 3
    return 2


def _latest_per_workflow(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest-first list → one row per workflow path/name."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for run in runs:
        key = str(run.get("path") or run.get("name") or run.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(run)
    return out


def _worst_normalized(runs: list[dict[str, Any]], branch: str) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_rank = 99
    for run in runs:
        row = _normalize_run(run, branch)
        rank = _conclusion_rank(row.get("conclusion"))
        if rank < best_rank:
            best = row
            best_rank = rank
    return best


def pick_tip_ci(runs: Any, branch: Any, tip_sha: Any = None) -> dict[str, Any] | None:
    """Real test CI on the current tip SHA.

    Pages / docs deploys and this board's scheduled refresh publisher are
    noise. A skipped bump-version helper must not hide a failing ``CI``
    workflow on the same SHA. When the repo has test CI but none yet for
    this tip, return ``pending`` so a release tag cannot claim the new
    commit is green. A tip that already ran only noise / unexecuted jobs
    is publisher-only: missing CI, not invented pending.
    """
    want = str(branch or "")
    if not want:
        return None
    branch_runs: list[dict[str, Any]] = []
    for run in runs or []:
        if not isinstance(run, dict):
            continue
        if str(run.get("head_branch") or "") != want:
            continue
        branch_runs.append(run)
    real = [
        run
        for run in branch_runs
        if not is_ci_noise(run) and not is_unexecuted_run(run)
    ]
    if not real:
        return None
    tip = str(tip_sha or "").strip()
    if tip:
        scoped = [run for run in real if sha_matches_tip(run, tip)]
        if not scoped:
            tip_runs = [run for run in branch_runs if sha_matches_tip(run, tip)]
            if tip_runs:
                # This tip already published Pages / refresh / empty-runner
                # noise. That is unexecuted, not a queued product test.
                return None
            return {
                "name": real[0].get("name") or "CI",
                "conclusion": "pending",
                "branch": want,
                "sha": tip[:7],
                "created": None,
                "html_url": None,
            }
        pool = _latest_per_workflow(scoped)
    else:
        newest = _run_sha7(real[0])
        same_sha = [run for run in real if _run_sha7(run) == newest] if newest else real
        pool = _latest_per_workflow(same_sha or real)
    return _worst_normalized(pool, want)


def status_from_fetch(
    repo: Any,
    *,
    override: str | None = None,
    jeff_gate: bool = False,
    high_level: bool = False,
) -> str:
    """Lane status from live gh. Missing CI is OK (green). Inaccessible is parked.

    Parked override still wins (ignored PRs stay parked). Live CI fail,
    in-flight, or pending-for-this-tip beat jeff_gate so WebJam cannot hide
    Red behind Jeff-gate + a release tag. Empty ``ci: {}`` stays green
    (Show Night). Cisco high-level notes keep an explicit yellow override
    when inaccessible.

    Private / high-level lanes never paint hosted CI as Red. The public board
    cannot inspect a private job, so a hosted conclusion cannot diagnose a
    code failure or confirm the external cause.
    """
    if override == "parked":
        return "parked"
    accessible = isinstance(repo, dict) and bool(repo.get("accessible"))
    if high_level:
        if override:
            return override
        if not accessible:
            return "parked"
        if jeff_gate:
            return "jeff-gate"
        return "yellow"
    concl = ci_conclusion(repo) if accessible else ""
    if accessible and concl in CI_FAIL_CONCLUSIONS:
        return "red"
    if accessible and concl in CI_ACTIVE_CONCLUSIONS:
        return "yellow"
    if accessible and repo.get("pr_listing_complete") is False:
        return "yellow"
    if jeff_gate:
        return "jeff-gate"
    if override:
        return override
    if not accessible:
        return "parked"
    try:
        n = int(repo.get("open_prs") or 0)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return "yellow"
    if concl in CI_OK_CONCLUSIONS:
        return "green"
    return "yellow"




def _coord_now():
    return datetime.now(ZoneInfo("America/Chicago"))


def parse_coord_issue(issue: Any, *, now: datetime | None = None) -> dict[str, Any] | None:
    """Parse one Bob-the-Bot coordination issue. Missing/invalid title is None."""
    if not isinstance(issue, dict):
        return None
    title = str(issue.get("title") or "").strip()
    match = COORD_TITLE_RE.match(title)
    if not match:
        return None
    owner = (match.group("owner") or "rupret007").strip()
    repo = (match.group("repo") or "").strip()
    if not repo:
        return None
    fields: dict[str, str] = {}
    for line in str(issue.get("body") or "").splitlines():
        field = COORD_FIELD_RE.match(line.strip())
        if not field:
            continue
        fields[field.group(1).strip().lower()] = field.group(2).strip()
    agent = str(fields.get("agent") or "none").strip().lower()
    if agent not in COORD_AGENTS:
        agent = "none"
    lease_until_raw = str(fields.get("lease_until") or "").strip()
    lease_until = None
    if lease_until_raw:
        try:
            lease_until = datetime.fromisoformat(lease_until_raw.replace("Z", "+00:00"))
        except ValueError:
            lease_until = None
    clock = now or _coord_now()
    if lease_until and lease_until.tzinfo is None:
        lease_until = lease_until.replace(tzinfo=clock.tzinfo)
    if agent == "none" or not lease_until:
        lease_state = "none"
    elif lease_until <= clock:
        lease_state = "expired"
    else:
        lease_state = "active"
    sha = str(fields.get("sha") or "").strip()
    return {
        "owner": owner,
        "repo": repo,
        "full_name": f"{owner}/{repo}",
        "agent": agent if lease_state == "active" else "none",
        "claimed_agent": agent,
        "sha": sha,
        "branch": str(fields.get("branch") or "").strip(),
        "pr": str(fields.get("pr") or "").strip(),
        "claimed_scope": str(fields.get("claimed_scope") or "").strip(),
        "holds": str(fields.get("holds") or "").strip(),
        "evidence": str(fields.get("evidence") or "").strip(),
        "next_action": str(fields.get("next_action") or "").strip(),
        "updated": str(fields.get("updated") or "").strip(),
        "lease_until": lease_until.isoformat() if lease_until else "",
        "lease_state": lease_state,
        "issue": issue.get("number"),
        "url": str(issue.get("url") or issue.get("html_url") or "").strip(),
    }


def public_coord(parsed: Any, *, private_lane: bool = False) -> dict[str, Any]:
    """Public-safe coordination chip. Private lanes never publish SHA/PR/paths."""
    if not isinstance(parsed, dict):
        return {}
    agent = str(parsed.get("agent") or "none").strip().lower()
    if agent not in COORD_AGENTS:
        agent = "none"
    state = str(parsed.get("lease_state") or "none").strip().lower()
    if state not in {"none", "active", "expired"}:
        state = "none"
    out: dict[str, Any] = {
        "repo": str(parsed.get("repo") or "").strip(),
        "agent": agent,
        "lease_state": state,
        "next": short_note(parsed.get("next_action"), 72),
    }
    if private_lane:
        return out
    sha = str(parsed.get("sha") or "").strip()
    if sha:
        out["sha"] = sha[:7]
    pr = str(parsed.get("pr") or "").strip().lstrip("#")
    if pr.isdigit():
        out["pr"] = int(pr)
    return out


def coord_signal(project: Any) -> str | None:
    """Active public-lane lease only. Never invent a worker on a quiet row."""
    if not isinstance(project, dict) or project.get("private"):
        return None
    coord = project.get("coord")
    if not isinstance(coord, dict):
        return None
    if str(coord.get("lease_state") or "") != "active":
        return None
    agent = str(coord.get("agent") or "").strip().lower()
    names = {"codex": "Codex", "grok": "Grok", "claude": "Claude"}
    label = names.get(agent)
    if not label:
        return None
    return label + " lease"


def compact_signal(project: Any) -> str | None:
    """One scan signal. Live CI, then review work, beat Latest vs source.

    Private / high-level lanes publish no CI or review signal. A hosted
    conclusion on those rows is not a public diagnosis.
    """
    if not isinstance(project, dict):
        return None
    if project.get("private"):
        return None
    concl = ci_conclusion(project)
    if concl in CI_FAIL_CONCLUSIONS:
        return "CI fail"
    if concl in CI_RUNNING_CONCLUSIONS:
        return "CI running"
    if concl in CI_PENDING_CONCLUSIONS:
        return "CI pending"
    lease = coord_signal(project)
    if lease:
        return lease
    stack = _stack_signal(project)
    if stack:
        return stack
    n = project.get("open_prs")
    try:
        count = int(n) if n is not None else 0
    except (TypeError, ValueError):
        count = 0
    if count > 0:
        return str(count) + (" open PR" if count == 1 else " open PRs")
    rel = str(project.get("release") or "").strip()
    if rel:
        if release_matches_tip(project) is False:
            return LATEST_VS_SOURCE_SIGNAL
        return rel
    if concl and concl not in CI_OK_CONCLUSIONS:
        return concl
    return None


def decision_body(pid: str, title: Any, verb: str) -> str:
    """Stable BOB-* issue body. No clock so first-paint hrefs match JS."""
    return "\n".join(
        [
            "Dashboard control decision",
            "",
            "id: " + pid,
            "title: " + str(title or pid)[:160],
            "decision: " + verb.lower(),
            "from: public board",
            "",
            "Submit this issue while logged in as rupret007. That GitHub login is the real yes.",
            "Bob: treat this as a one-shot inbox item. High-risk still needs the draft shown in chat before acting.",
        ]
    )


def decision_href(verb: Any, pid: Any, title: Any = "") -> str:
    """GitHub new-issue URL for Approve / Hold / Deny. Empty if unsafe."""
    v = str(verb or "").strip().upper()
    ident = str(pid or "").strip()
    if v not in DECISION_VERBS or not PENDING_ID_RE.match(ident):
        return ""
    query = urlencode(
        {"title": "BOB-" + v + ": " + ident, "body": decision_body(ident, title, v)}
    )
    return DECISION_ISSUE_NEW + "?" + query


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
    url = agent_url_from_fields(raw) if aid in AGENT_IDS else ""
    # Mac pills may carry a real agent/PR URL. Extra ids are not invented here.
    if aid not in AGENT_IDS:
        url = ""
    return {
        "id": aid,
        "name": name,
        "state": state,
        "detail": detail,
        "checked_at": raw.get("checked_at"),
        "url": url or None,
        "pr_url": safe_pr_url(raw.get("pr_url") or raw.get("pr")) or None,
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
    def safe_source_class(label: str) -> str:
        source = str(label or "").strip().lower()
        if source.startswith("file"):
            return "file"
        if source.startswith("env"):
            return "env"
        if source == "previous":
            return "previous"
        return "unknown"

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
        safe_src = safe_source_class(src)
        gated = [age_gate_agent(a, now=now) for a in parsed]
        if any(agent_is_fresh(a, now=now) for a in parsed):
            return gated, safe_src
        return gated, safe_src + ":stale->unknown"
    return default_agents(), "default:unknown"


def board_content_fingerprint(data: Any) -> str:
    """Stable paint key. Timestamps / agents_source / decisions are ignored."""
    if not isinstance(data, dict):
        return ""

    def agent_key(a: Any) -> list[Any]:
        if not isinstance(a, dict):
            return []
        return [a.get("id"), a.get("state"), a.get("detail"), a.get("url"), a.get("pr_url")]

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
            p.get("open_pr_url"),
            p.get("open_pr_stack"),
            p.get("release"),
            p.get("release_sha"),
            p.get("tip_sha"),
            p.get("agent_url"),
            p.get("live_game_url"),
            ci.get("conclusion") if ci else None,
            ci.get("sha") if ci else None,
            ci.get("name") if ci else None,
            ci.get("html_url") if ci else None,
            (p.get("coord") or {}).get("agent")
            if isinstance(p.get("coord"), dict)
            else None,
            (p.get("coord") or {}).get("lease_state")
            if isinstance(p.get("coord"), dict)
            else None,
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
        "cloud": [agent_key(a) for a in (data.get("cloud_agents") or [])],
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
                    "Private media boundary",
                    "Private content stays high-level. Upload and publishing require an explicit owner decision.",
                    status="jeff-gate",
                    chip="Owner-only",
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
                    "Music stack",
                    "Vault, StoryBoard, Show Night, and WebJam work together. Latest != source.",
                    chip="Feature",
                ),
                _card(
                    "Type tabs",
                    "Each GitHub type is its own tab. First screen is status plus tabs, not the wall.",
                    chip="Feature",
                ),
                _card(
                    "Soft-paint poll",
                    "Client fetches status.json every 30s (pauses when the tab is hidden). Immediate poll on pageshow / visible. Fetch aborts after 8s. Hide / iOS-return abort is not a failed poll. A stale cached status.json cannot rewind the board. Repaints when board content changes -- not on every 15m Actions timestamp. Tip CI is the current SHA; Pages / skipped helpers / this board's refresh publisher cannot hide a fail. A skipped or cancelled helper cannot beat a success or become Open CI. Lanes prefer the open PR; CI fail/running taps the Actions run when a run URL is known. A complete same-repo stack shows safe base-to-tip PR order and taps the pulls list; ambiguous chains fall back to the honest open-PR count. Vault, StoryBoard, Show Night, and WebJam work together as one music stack. WebJam Latest is the published test candidate and is not unpublished source; a proven Latest != source signal taps /releases/latest -- not dead text.",
                    chip="Feature",
                ),
                _card(
                    "Agents strip",
                    "Codex / Cursor / Claude from a Mac probe. Stale or untimestamped probes paint Unknown. Never invent Running. When the box has no live Mac probe, the three pills collapse to one Agents unknown line. Work links require the exact real cursor.com/agents/bc- UUID to be advertised by a currently open same-repository PR in an allowlisted public repository; probe-only and fork-PR links are dropped. Tap Open agent / Open PR (real target=_blank plus openBlank fallback). Token-like words redacted.",
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
                    "pollSeq / pendingSeq drop stale fetches. stopPolling bumps pollSeq before abort so tab-hide is not a fail. Approve / Hold / Deny are real GitHub links so iOS cannot swallow a popup. Open agent / Open PR / Open repo / Open CI / Play game use the same real target=_blank plus openBlank fallback. decideBusy still debounce double-taps.",
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
