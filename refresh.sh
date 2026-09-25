#!/usr/bin/env bash
# Rebuild Bob ops dashboard from live gh data, then optionally push to Pages.
# Noninteractive: safe for GitHub Actions (gh uses GH_TOKEN / GITHUB_TOKEN).
# Usage:
#   ./refresh.sh              # write index.html + status.json in this dir
#   ./refresh.sh --push       # also commit+push to rupret007/bob-ops-dashboard main
set -euo pipefail
if [[ -n "${GH_TOKEN:-}" || -n "${GITHUB_TOKEN:-}" ]]; then
  export GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN}}"
fi

ROOT="$(cd "$(dirname "$0")" && pwd)"
if [[ -z "${REFRESH_STARTED_MS:-}" ]]; then
  REFRESH_STARTED_MS="$(python3 -c 'import time; print(int(time.time()*1000))')"
fi
export REFRESH_STARTED_MS
OWNER="${OWNER:-rupret007}"
REPOS=(
  webjam StoryLiner StoryBoard Rad-Dad-Merch RadDadSite Turdanoid
  AdoptIQ TACTrack AI-Music-Vault rad-dad-show-night Andrea_NanoBot Bob-the-Bot
  StoryOps-AI ballbeacon CSS_Conductor barker bob-ops-dashboard
  Cursor-OpenClaw-Integration StoryDesk
)
PUSH=0
[[ "${1:-}" == "--push" ]] && PUSH=1
APPLY_DECISIONS="${BOB_DASHBOARD_APPLY_DECISIONS:-0}"
[[ "$APPLY_DECISIONS" == "0" || "$APPLY_DECISIONS" == "1" ]] || {
  echo "BOB_DASHBOARD_APPLY_DECISIONS must be 0 or 1" >&2
  exit 2
}
export BOB_DASHBOARD_APPLY_DECISIONS="$APPLY_DECISIONS"

need_gh() {
  command -v gh >/dev/null || { echo "gh required"; exit 1; }
  command -v python3 >/dev/null || { echo "python3 required"; exit 1; }
}

fetch_repo() {
  local spec="$1"
  local repo_owner="$OWNER"
  local repo_name="$spec"
  if [[ "$spec" == */* ]]; then
    repo_owner="${spec%%/*}"
    repo_name="${spec#*/}"
  fi
  python3 - "$repo_owner" "$repo_name" "$ROOT" <<'PY'
import json, subprocess, sys
owner, repo, root = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)
from board_meta import (
    detect_linear_pr_stack,
    extract_cloud_agents_from_prs,
    is_draft_pr,
    pick_open_pr,
    pick_tip_ci,
    safe_pr_url,
    safe_release_tag,
)
full = f"{owner}/{repo}"

def api(path, default=None):
    try:
        out = subprocess.check_output(["gh", "api", path], text=True, stderr=subprocess.DEVNULL)
        return json.loads(out)
    except Exception:
        return default

def api_all(path):
    """Fetch every REST list page; return no rows when completeness is unknown."""
    rows = []
    separator = "&" if "?" in path else "?"
    for page in range(1, 101):
        batch = api(f"{path}{separator}page={page}", None)
        if not isinstance(batch, list):
            return [], False
        rows.extend(batch)
        if len(batch) < 100:
            return rows, True
    return [], False

meta = api(f"repos/{full}", None)
if not isinstance(meta, dict):
    print(json.dumps({
        "name": repo,
        "full_name": full,
        "accessible": False,
        "collection_complete": False,
        "error": "metadata unavailable",
    }))
    sys.exit(0)

branch = meta.get("default_branch") or "main"
private = bool(meta.get("private"))
commit = api(f"repos/{full}/commits/{branch}", None)
commit_complete = isinstance(commit, dict) and bool(commit.get("sha"))
commit = commit if isinstance(commit, dict) else {}
sha = (commit.get("sha") or "")[:7]
c = (commit.get("commit") or {})
date = ((c.get("committer") or {}).get("date")) or ((c.get("author") or {}).get("date"))
msg = (c.get("message") or "").split("\n", 1)[0]

prs, prs_complete = api_all(f"repos/{full}/pulls?state=open&per_page=100")
open_pr_urls = [
    url
    for p in prs
    if isinstance(p, dict)
    if (url := safe_pr_url(p.get("html_url") or p.get("url")))
]
open_pr_refs = []
if prs_complete:
    for p in prs:
        if not isinstance(p, dict):
            continue
        number = p.get("number")
        url = safe_pr_url(p.get("html_url") or p.get("url"))
        expected = f"https://github.com/{full}/pull/{number}"
        if (
            isinstance(number, int)
            and not isinstance(number, bool)
            and number > 0
            and url
            and url.lower() == expected.lower()
        ):
            open_pr_refs.append({"number": number, "url": url, "draft": is_draft_pr(p)})
ready_prs = [p for p in prs if isinstance(p, dict) and not is_draft_pr(p)]
open_pr = pick_open_pr(ready_prs) if prs_complete else None
open_pr_stack = detect_linear_pr_stack(ready_prs, branch) if prs_complete else []
# A Cursor agent URL can grant access beyond high-level repository status.
# Never materialize one from a private PR onto this public dashboard.
clouds = [] if private or not prs_complete else extract_cloud_agents_from_prs(prs, limit=1)
# Default-branch only so PR runs cannot push tip CI out of the window.
runs = api(f"repos/{full}/actions/runs?per_page=20&branch={branch}", None)
runs_complete = isinstance(runs, dict) and isinstance(runs.get("workflow_runs"), list)
runs = runs if isinstance(runs, dict) else {}
ci = pick_tip_ci(runs.get("workflow_runs") or [], branch, sha)

rels = api(f"repos/{full}/releases?per_page=1", None)
releases_complete = isinstance(rels, list)
rels = rels if isinstance(rels, list) else []
release = None
release_sha = None
release_url = None
tagged_complete = True
if isinstance(rels, list) and rels and isinstance(rels[0], dict):
    tag = safe_release_tag(rels[0].get("tag_name"))
    if tag:
        release = tag
        release_url = rels[0].get("html_url")
        tagged = api(f"repos/{full}/commits/{tag}", None)
        tagged_complete = isinstance(tagged, dict) and bool(tagged.get("sha"))
        if isinstance(tagged, dict):
            release_sha = (tagged.get("sha") or "")[:7] or None

print(json.dumps({
    "accessible": True,
    "collection_complete": bool(
        commit_complete
        and prs_complete
        and runs_complete
        and releases_complete
        and tagged_complete
    ),
    "name": meta.get("name"),
    "full_name": full,
    "private": private,
    "html_url": meta.get("html_url"),
    "default_branch": branch,
    "tip_sha": sha or None,
    "tip_date": date,
    "tip_msg": msg,
    "open_prs": len(ready_prs) if prs_complete else None,
    "pr_listing_complete": prs_complete,
    "open_pr_urls": open_pr_urls,
    "open_pr_refs": open_pr_refs,
    "open_pr": open_pr,
    "open_pr_stack": open_pr_stack,
    "agent_url": (clouds[0]["url"] if clouds else None),
    "cloud_agents": clouds,
    "ci": ci,
    "release": release,
    "release_sha": release_sha,
    "release_url": release_url,
}, indent=None))
PY
}

need_gh
echo "Fetching ${#REPOS[@]} portfolio repositories ..."
TMP="$(mktemp)"
echo '[' > "$TMP"
first=1
for r in "${REPOS[@]}"; do
  echo "  - $r"
  row="$(fetch_repo "$r")"
  if [[ $first -eq 1 ]]; then first=0; else echo ',' >> "$TMP"; fi
  echo "$row" >> "$TMP"
done
echo ']' >> "$TMP"

python3 - "$ROOT" "$TMP" <<'PY'
import json, sys, re, time, subprocess, html, os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from board_meta import (
    AGENT_IDS,
    MAC_PROBE_AGENT_IDS,
    AGENT_STATE_CHIP,
    CONTROL_ACTIONS,
    attention_rank,
    compact_signal,
    compact_unknown_mac_probes,
    decision_href,
    incomplete_public_collections,
    parse_coord_issue,
    public_coord,
    status_with_coord_review,
    drop_leftover_verify,
    extract_cloud_agents_from_prs,
    focus_key,
    glance_html,
    public_high_level_ci,
    is_quiet_lane,
    is_type_tab,
    lane_hrefs,
    merge_cloud_agents,
    signal_href,
    merge_first_class,
    presentation,
    prune_closed_parked_prs,
    resolve_agents,
    type_tabs_html,
    latest_release_url_from_repo,
    safe_actions_url,
    safe_agent_url,
    safe_game_url,
    safe_pr_url,
    safe_release_url,
    safe_repo_url,
    short_note,
    split_pending,
    status_from_fetch,
    unknown_mac_probes_html,
    visible_chip,
)
refresh_started_ms = int(os.environ.get("REFRESH_STARTED_MS") or 0) or int(time.time() * 1000)
apply_decisions = os.environ.get("BOB_DASHBOARD_APPLY_DECISIONS") == "1"
fetched = json.loads(Path(sys.argv[2]).read_text())
incomplete_public = incomplete_public_collections(fetched)
if incomplete_public:
    names = ", ".join(incomplete_public)
    raise SystemExit(
        "Refusing to replace the last truthful dashboard: "
        f"required public collection incomplete for {names}"
    )
by = {x.get("name") or x.get("full_name","").split("/")[-1]: x for x in fetched}
now = datetime.now(ZoneInfo("America/Chicago"))
updated_ct = now.strftime("%a %b %-d, %Y · %-I:%M %p %Z")
updated_iso = now.isoformat()

def g(name):
    return by.get(name) or {"accessible": False, "name": name}

CHIP = {
    "green": "Green", "yellow": "Yellow", "red": "Red",
    "parked": "Parked", "jeff-gate": "Jeff-gate",
}

coord_by = {}
try:
    raw_coord = subprocess.check_output(
        [
            "gh", "issue", "list", "-R", "rupret007/Bob-the-Bot",
            "--label", "coord", "--state", "open", "--limit", "50",
            "--json", "number,title,body,url,updatedAt",
        ],
        text=True,
        stderr=subprocess.DEVNULL,
    )
    for iss in json.loads(raw_coord or "[]"):
        parsed = parse_coord_issue(iss, now=now)
        if parsed:
            coord_by[str(parsed.get("repo") or "").lower()] = parsed
except Exception:
    coord_by = {}

def project(
    name,
    *,
    status=None,
    notes="",
    product_sha=None,
    jeff_gate=False,
    live_game_url=None,
    high_level_only=False,
    extra=None,
):
    r = g(name)
    st = status_from_fetch(
        r,
        override=status,
        jeff_gate=jeff_gate,
        high_level=bool(r.get("private") or high_level_only),
    )
    p = {
        "name": name if not r.get("name") else r["name"],
        "repo": r.get("full_name"),
        "url": r.get("html_url"),
        "repo_url": r.get("html_url"),
        "private": r.get("private"),
        "status": st,
        "chip": CHIP.get(st, st),
        "default_branch": r.get("default_branch"),
        "tip_sha": r.get("tip_sha"),
        "tip_date": r.get("tip_date"),
        "open_prs": r.get("open_prs"),
        "open_pr_url": (r.get("open_pr") or {}).get("url") if isinstance(r.get("open_pr"), dict) else None,
        "open_pr_number": (r.get("open_pr") or {}).get("number") if isinstance(r.get("open_pr"), dict) else None,
        "open_pr_draft": bool((r.get("open_pr") or {}).get("draft")) if isinstance(r.get("open_pr"), dict) else False,
        "open_pr_stack": r.get("open_pr_stack") if isinstance(r.get("open_pr_stack"), list) else [],
        "pr_listing_complete": r.get("pr_listing_complete"),
        "agent_url": r.get("agent_url"),
        "ci": r.get("ci"),
        "release": r.get("release"),
        "release_sha": r.get("release_sha") if r.get("release") else None,
        "release_url": (
            (safe_release_url(r.get("release_url")) or latest_release_url_from_repo(r.get("html_url")))
            if r.get("release")
            else None
        ),
        "notes": notes,
        "accessible": r.get("accessible", False),
    }
    if product_sha:
        p["product_sha"] = product_sha
    if live_game_url:
        p["live_game_url"] = safe_game_url(live_game_url)
    if extra:
        p.update(extra)
    parsed_coord = coord_by.get(str(name or "").lower())
    if parsed_coord:
        p["coord"] = public_coord(
            parsed_coord,
            private_lane=bool(p.get("private") or high_level_only),
            open_pr_refs=(r.get("open_pr_refs") if r.get("pr_listing_complete") else []),
        )
        p["status"] = status_with_coord_review(p.get("status"), p)
        p["chip"] = CHIP.get(p["status"], p["status"])
    if p.get("private") or high_level_only:
        raw_ci = p.get("ci") if isinstance(p.get("ci"), dict) else {}
        keep_coord = p.get("coord") if isinstance(p.get("coord"), dict) else None
        # The board itself is public. A private lane may expose its product
        # name, high-level color/accessibility, and a non-fail CI conclusion
        # only. Hosted failure / empty-runner is unexecuted or undiagnosable,
        # never a public product-test fail. Build a new allowlisted object so
        # future repository fields fail closed.
        p = {
            "name": p.get("name"),
            "private": True,
            "status": p.get("status"),
            "chip": p.get("chip"),
            "notes": p.get("notes"),
            "accessible": bool(p.get("accessible")),
            "ci": public_high_level_ci(raw_ci),
        }
        if keep_coord:
            p["coord"] = keep_coord
    # Friendly display names
    rename = {
        "webjam": "WebJam",
        "ballbeacon": "Ball Beacon",
        "barker": "Barker",
        "bob-ops-dashboard": "Bob Ops Dashboard",
        "CSS_Conductor": "CSS Conductor",
        "Cursor-OpenClaw-Integration": "Cursor-OpenClaw Integration",
        "Rad-Dad-Merch": "Rad Dad Merch",
        "rad-dad-show-night": "Show Night",
        "AI-Music-Vault": "AI Music Vault",
        "Andrea_NanoBot": "Andrea NanoBot",
        "Bob-the-Bot": "Bob the Bot",
        "StoryOps-AI": "WashOps",
    }
    p["name"] = rename.get(name, p["name"])
    return p

# Curated narrative notes (safe / no secrets) -- live SHAs/CI come from gh
status = {
  "generated_at": updated_iso,
  "generated_at_display": updated_ct,
  "timezone": "America/Chicago",
  "owner": "rupret007",
  "dashboard": "bob-ops-dashboard",
  "sections": [
    {
      "id": "live-shipping",
      "title": "Live shipping",
      "projects": [
        project("webjam",
                notes="Making room. Latest is the published test candidate; source can be ahead."),
        project("StoryLiner", notes="Story workflow app. Current review stack and default-branch CI come from the live refresh."),
        project("StoryBoard", notes="Band-business engine. Consumes Vault; not a second catalog."),
        project("Rad-Dad-Merch", notes="Merch lane; watch Release integrity."),
        project("RadDadSite",
                notes="Tip green typical; deploy work comes only from live open PR data."),
        project("rad-dad-show-night", notes="Live run sheet. GitHub is source; live Latest is Sites. Green CI is not Latest."),
        project("AI-Music-Vault", high_level_only=True,
                notes="Private catalog spine for StoryBoard / Show Night. Do not publish catalog content."),
        project("Turdanoid",
                live_game_url="https://rupret007.github.io/Turdanoid/hub.html",
                notes="Public game hub. Fun/replayability pass remains open; green CI is not completion."),
      ],
    },
    {
      "id": "apps-utilities",
      "title": "Apps & utilities",
      "projects": [
        project("StoryOps-AI",
                notes="Exterior wash/services OS (WashOps). High-level only; no customer data on this board.",
                extra={"name": "WashOps"}),
        project("ballbeacon",
                notes="Private iOS utility. Software validation only; device and signing steps stay owner-only."),
        project("CSS_Conductor", high_level_only=True,
                notes="Private developer utility. High-level only; hosted-job cause stays unconfirmed."),
        project("barker",
                notes="Private app. No live sends, production changes, or credential operations from this board."),
        project("Cursor-OpenClaw-Integration",
                notes="Integration utility. Never restart gateways or alter credentials automatically."),
        project("bob-ops-dashboard",
                notes="Source-only feature PRs; the scheduled refresh owns generated index.html and status.json."),
        project("StoryDesk", high_level_only=True,
                notes="Private macOS virtual-display / Cast prototype. Software validation only; Screen Recording and device steps stay owner-only."),
        {"name": "OpenClaw Runtime", "status": "parked", "chip": "Local-only",
         "notes": "Local runtime lane. No gateway restart, credential change, or live operation from this board."},
      ],
    },
    {
      "id": "cisco",
      "title": "Cisco work",
      "projects": [
        project("AdoptIQ", high_level_only=True,
                notes="Private, draft, and offline-only; ready_for_live_cisco=false. No secrets or customer data on this page."),
        project("TACTrack", high_level_only=True,
                notes="Private. High-level only. No live-repo, CI, or PR taps on this board."),
        {
          "name": "AdoptIQ notes", "status": "parked", "chip": "Parked",
          "notes": "High-level only: manager-decision UX and offline candidate path. No CSOne paths, customer rows, or tokens.",
        },
      ],
    },
    {
      "id": "messaging",
      "title": "Messaging / Bob infra",
      "projects": [
        project("Andrea_NanoBot",
                notes="BB AppleScript send preferred. Private API OFF. Approval-fenced sends only."),
        project("Bob-the-Bot", high_level_only=True,
                notes=(
                    "Bob application — private bootstrap. Reuses the Andrea messaging engine and "
                    "guarded OpenClaw delegation; no live sends, restarts, credentials, or production actions."
                )),
        {"name": "Telegram Bot the Bot", "status": "green", "chip": "Green",
         "notes": "Whisper + Chrome mop shipped. Bob front-door via Telegram."},
        {"name": "Codex local goals", "status": "green", "chip": "Green",
         "notes": "goals=true fixed. Local Codex loop for AdoptIQ/ops lanes."},
      ],
    },
    {
      "id": "private-media",
      "title": "Private media",
      "projects": [
        {"name": "Private media", "status": "jeff-gate", "chip": "Owner-only",
         "notes": "Private-content boundary. Upload and publishing stay owner-only."},
      ],
    },
    {
      "id": "parked",
      "title": "Parked",
      "projects": [
        {"name": "Parked private catalog", "status": "parked", "chip": "Parked",
         "notes": "Private-content boundary. Parked catalog work stays private and owner-only."},
      ],
    },
    {
      "id": "active-agents",
      "title": "LLM work now",
      "projects": [],
    },
  ],
  "fetched_repos": [x.get("name") for x in fetched if x.get("accessible")],
  "inaccessible": [x.get("name") for x in fetched if not x.get("accessible")],
  "publish_notes": "Public board -- no secrets or customer paths. Private repositories stay high-level; AI Music Vault stays private-content-boundary only.",
  "refresh_started_ms": refresh_started_ms,
}
status["sections"] = prune_closed_parked_prs(
    status["sections"],
    [url for row in fetched for url in (row.get("open_pr_urls") or [])],
)
status["sections"] = merge_first_class(status["sections"])

# --- LLM resources strip — safe public fields only ---
prev_early = {}
try:
    prev_early = json.loads((root / "status.json").read_text())
except Exception:
    prev_early = {}

file_texts = []
for cand in (root / "agents-status.json", Path("/workspace/bob-ops-dashboard/agents-status.json")):
    if cand.is_file():
        try:
            file_texts.append((f"file:{cand}", cand.read_text()))
            break
        except Exception:
            continue
prev_agents = prev_early.get("agents") if isinstance(prev_early, dict) else None
agents, src = resolve_agents(
    env_blob=os.environ.get("AGENTS_STATUS_JSON"),
    file_texts=file_texts,
    previous=prev_agents,
)
status["agents_source"] = src

dash_prs = []
try:
    dash_raw = subprocess.check_output(
        ["gh", "api", "repos/rupret007/bob-ops-dashboard/pulls?state=open&per_page=20"],
        text=True,
        stderr=subprocess.DEVNULL,
    )
    dash_parsed = json.loads(dash_raw or "[]")
    if isinstance(dash_parsed, list):
        dash_prs = dash_parsed
except Exception:
    dash_prs = []
repo_cloud = []
for row in fetched:
    if isinstance(row, dict):
        repo_cloud.extend(row.get("cloud_agents") or [])
# extract_cloud_agents_from_prs skips draft / parked leftover PRs, so a
# leftover WebJam draft cannot become a live cloud chip or agent_url.
trusted_cloud = merge_cloud_agents(
    extract_cloud_agents_from_prs(dash_prs),
    repo_cloud,
)
trusted_by_url = {row.get("url"): row for row in trusted_cloud if row.get("url")}
# Keep high-level Mac state, but publish a work link only when the same agent
# URL is advertised by a currently open, same-repository PR fetched above.
for agent in agents:
    trusted = trusted_by_url.get(safe_agent_url(agent.get("url")))
    if trusted:
        agent["url"] = trusted["url"]
        agent["pr_url"] = trusted.get("pr_url")
    else:
        agent.pop("url", None)
        agent.pop("pr_url", None)
status["agents"] = agents
status["cloud_agents"] = trusted_cloud

LANE_ORDER = (
    ("codex", "Codex (ChatGPT)"),
    ("claude", "Claude"),
    ("gemini", "Gemini"),
    ("minimax", "MiniMax"),
    ("grok", "Grok"),
    ("cursor-cloud", "Cursor Cloud"),
)

def _clean_line(value, limit=240):
    text = " ".join(str(value or "").split()).strip()
    return text[:limit]

def _safe_branch(value):
    branch = str(value or "").strip()
    if not branch:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}", branch):
        return ""
    if ".." in branch or "@{" in branch or "//" in branch or branch.endswith(("/", ".")):
        return ""
    return branch

def _repo_from_pr_url(url):
    safe = safe_pr_url(url)
    if not safe:
        return ""
    m = re.match(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/[1-9][0-9]*$", safe, re.I)
    return m.group(1) if m else ""

def _pr_number(url):
    safe = safe_pr_url(url)
    if not safe:
        return ""
    m = re.search(r"/pull/([1-9][0-9]*)$", safe)
    return m.group(1) if m else ""

def _lane_id(raw_id, raw_name):
    value = str(raw_id or "").strip().lower().replace("_", "-")
    aliases = {
        "chatgpt": "codex",
        "codex-chatgpt": "codex",
        "cursor": "cursor-cloud",
        "cursor-cloud": "cursor-cloud",
        "cloud": "cursor-cloud",
    }
    if value in {"codex", "claude", "gemini", "minimax", "grok", "cursor-cloud"}:
        return value
    if value in aliases:
        return aliases[value]
    text = str(raw_name or "").strip().lower()
    if "cursor cloud" in text or text == "cloud":
        return "cursor-cloud"
    if "codex" in text or "chatgpt" in text:
        return "codex"
    if "claude" in text:
        return "claude"
    if "gemini" in text:
        return "gemini"
    if "minimax" in text:
        return "minimax"
    if "grok" in text:
        return "grok"
    return ""

def _normalize_work_row(raw):
    if not isinstance(raw, dict):
        return None
    lane = _lane_id(raw.get("id"), raw.get("name"))
    if not lane:
        return None
    pr_url = safe_pr_url(raw.get("pr_url") or raw.get("pr"))
    repo = _clean_line(raw.get("repo") or _repo_from_pr_url(pr_url), 96)
    status_name = str(raw.get("status") or "").strip().lower()
    if status_name not in {"running", "finished", "blocked", "idle"}:
        status_name = "idle"
    return {
        "id": lane,
        "name": next((n for i, n in LANE_ORDER if i == lane), lane.title()),
        "task_title": _clean_line(raw.get("task_title") or raw.get("title"), 120),
        "repo": repo,
        "pr_url": pr_url,
        "pr_number": str(raw.get("pr_number") or _pr_number(pr_url)),
        "branch": _safe_branch(raw.get("branch")),
        "goal": _clean_line(raw.get("goal") or raw.get("notes"), 220),
        "status": status_name,
        "agent_url": safe_agent_url(raw.get("agent_url") or raw.get("url")),
        "why": _clean_line(raw.get("why") or raw.get("lane_why"), 120),
        "note": _clean_line(raw.get("note") or raw.get("detail"), 160),
        "last_task": _clean_line(raw.get("last_task"), 120),
        "source": _clean_line(raw.get("source"), 48),
    }

work_blob = None
for work_path in (root / "llm-work-now.json", Path("/workspace/bob-ops-dashboard/llm-work-now.json")):
    if work_path.is_file():
        try:
            work_blob = json.loads(work_path.read_text())
            break
        except Exception:
            continue
if work_blob is None:
    env_work = os.environ.get("LLM_WORK_NOW_JSON")
    if env_work:
        try:
            work_blob = json.loads(env_work)
        except Exception:
            work_blob = None
work_rows = []
if isinstance(work_blob, dict):
    work_rows = work_blob.get("work") or work_blob.get("lanes") or []
elif isinstance(work_blob, list):
    work_rows = work_blob

by_lane = {}
for raw in work_rows or []:
    row = _normalize_work_row(raw)
    if row:
        by_lane[row["id"]] = row

for cloud in trusted_cloud:
    lane = _lane_id("", cloud.get("name"))
    if not lane:
        continue
    if lane in by_lane and by_lane[lane].get("task_title"):
        continue
    pr_url = safe_pr_url(cloud.get("pr_url"))
    by_lane[lane] = {
        "id": lane,
        "name": next((n for i, n in LANE_ORDER if i == lane), lane.title()),
        "task_title": _clean_line(cloud.get("detail") or cloud.get("name") or "Cloud assignment", 120),
        "repo": _repo_from_pr_url(pr_url),
        "pr_url": pr_url,
        "pr_number": _pr_number(pr_url),
        "branch": "",
        "goal": "Cloud Agent assignment sourced from open PR attribution.",
        "status": "running",
        "agent_url": safe_agent_url(cloud.get("url")),
        "why": "Cloud Agent",
        "note": _clean_line(cloud.get("detail"), 160),
        "last_task": "",
        "source": "cloud_agents",
    }

prev_work = prev_early.get("llm_work") if isinstance(prev_early, dict) else []
prev_by_lane = {}
for raw in prev_work or []:
    row = _normalize_work_row(raw)
    if row:
        prev_by_lane[row["id"]] = row

llm_work = []
for lane_id, lane_name in LANE_ORDER:
    row = dict(by_lane.get(lane_id) or {})
    previous = prev_by_lane.get(lane_id) or {}
    row["id"] = lane_id
    row["name"] = lane_name
    if not row.get("task_title"):
        row["task_title"] = "idle - needs assignment"
        row["status"] = "idle"
        if previous.get("task_title") and previous.get("task_title") != "idle - needs assignment":
            row["last_task"] = previous.get("task_title")
    if not row.get("status"):
        row["status"] = "idle"
    if not row.get("source"):
        row["source"] = "idle"
    llm_work.append(row)

status["llm_work"] = llm_work

# Pulse data only -- this section now reflects current work attribution.
agent_projects = []
for row in llm_work:
    lane_status = str(row.get("status") or "idle")
    chip_map = {
        "running": ("green", "Running"),
        "finished": ("parked", "Finished"),
        "blocked": ("red", "Blocked"),
        "idle": ("parked", "Idle"),
    }
    st, label = chip_map.get(lane_status, chip_map["idle"])
    task = row.get("task_title") or "idle - needs assignment"
    note = row.get("goal") or row.get("why") or row.get("note") or ""
    notes = task + ((" - " + note) if note else "")
    agent_projects.append({
        "name": row.get("name") or row.get("id"),
        "status": st,
        "chip": label,
        "notes": notes,
        "agent_id": row.get("id"),
        "agent_state": lane_status,
        "url": row.get("agent_url"),
        "pr_url": row.get("pr_url"),
    })
for sec in status["sections"]:
    if sec.get("id") == "active-agents":
        sec["projects"] = agent_projects
        sec["title"] = "LLM work now"
        break

# Decisions inbox (needed before first paint -- Jeff should see pending immediately)
prev = prev_early if isinstance(prev_early, dict) else {}
if drop_leftover_verify(prev):
    print("ignored previous verify challenge (public board, no OTP)")
if drop_leftover_verify(status):
    print("drop leftover verify: public board has no OTP gate")

private_media_decision_id = "private-media-upload"
private_media_legacy_decision_id = bytes.fromhex(
    "6f7068656c69612d75706c6f6164"
).decode("ascii")

def public_decision_id(value):
    decision_id = str(value or "").lower()
    if decision_id == private_media_legacy_decision_id:
        return private_media_decision_id
    return decision_id

decisions = []
if isinstance(prev.get("decisions"), list):
    for previous_decision in prev["decisions"]:
        if not isinstance(previous_decision, dict):
            continue
        sanitized_decision = dict(previous_decision)
        sanitized_decision["id"] = public_decision_id(sanitized_decision.get("id"))
        decisions.append(sanitized_decision)

resolved = set()
for d in decisions:
    if d.get("id") and d.get("decision") in ("approve", "deny"):
        resolved.add(str(d["id"]).lower())

try:
    raw = subprocess.check_output(
        [
            "gh", "issue", "list", "-R", "rupret007/bob-ops-dashboard",
            "--state", "open", "--limit", "40",
            "--json", "number,title,author,createdAt,url",
        ],
        text=True,
        stderr=subprocess.DEVNULL,
    )
    issues = json.loads(raw or "[]")
except Exception:
    issues = []

for iss in issues:
    title = (iss.get("title") or "").strip()
    author = ((iss.get("author") or {}).get("login") or "").lower()
    if author != "rupret007":
        continue
    m = re.match(r"^BOB-(APPROVE|DENY|HOLD):\s*([a-z0-9._-]+)\s*$", title, re.I)
    if not m:
        continue
    verb = m.group(1).lower()
    pid = public_decision_id(m.group(2))
    decision = "approve" if verb == "approve" else ("deny" if verb == "deny" else "hold")
    entry = {
        "id": pid,
        "decision": decision,
        "issue": iss.get("number"),
        "url": iss.get("url"),
        "at": iss.get("createdAt"),
        "author": author,
    }
    decisions = [d for d in decisions if str(d.get("id", "")).lower() != pid]
    decisions.append(entry)
    if decision in ("approve", "deny"):
        resolved.add(pid)
    if apply_decisions:
        try:
            subprocess.check_call(
                [
                    "gh", "issue", "close", str(iss["number"]),
                    "-R", "rupret007/bob-ops-dashboard",
                    "--comment", f"Recorded as {decision} for Bob.",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

standing = [
    {
        "id": "che-live-pull",
        "title": "Che live pull",
        "kind": "jeff-gate",
        "detail": "GitHub Friends look is ahead of live raddadband.com until Che pulls.",
        "risk": "low",
    },
    {
        "id": "logic-keys-wavs",
        "title": "Logic keys and WAVs",
        "kind": "jeff-gate",
        "detail": "Mac field-fill only. Do not invent catalog keys.",
        "risk": "low",
    },
    {
        "id": "adoptiq-live-cisco",
        "title": "AdoptIQ live Cisco readiness",
        "kind": "owner-live-gate",
        "detail": "Offline candidate only. Keep ready_for_live_cisco=false until an explicit owner decision.",
        "risk": "high",
    },
]

pending_out = [s for s in standing if s["id"] not in resolved]

status["pending"] = pending_out
status["decisions"] = decisions[-40:]
status["control"] = {
    "mode": "github-issue-inbox",
    "jeff_github": "rupret007",
    "prefixes": ["BOB-APPROVE:", "BOB-DENY:", "BOB-HOLD:"],
    "note": "Public board. Real authority is a GitHub issue from rupret007.",
}

# HTML render -- pulse strip + compact lanes (status-page feel, not a card wall)
CHIP_COLORS = {
  "green": ("#16a34a", "#052e16", "#bbf7d0"),
  "yellow": ("#ca8a04", "#422006", "#fef08a"),
  "red": ("#dc2626", "#450a0a", "#fecaca"),
  "parked": ("#525252", "#171717", "#d4d4d4"),
  "jeff-gate": ("#d97757", "#2a1510", "#f5c4b3"),
}

def h(s):
    return html.escape("" if s is None else str(s), quote=True)

def safe_href(u):
    if not u:
        return ""
    s = str(u).strip()
    low = s.lower()
    if (
        s.startswith("./")
        and ":" not in s
        and "\\" not in s
        and not any(c in s for c in (" ", "\n", "\r", "\t", "<", ">", '"', "'"))
    ):
        return s
    if not (low.startswith("https://") or low.startswith("http://")):
        return ""
    if any(c in s for c in (" ", "\n", "\r", "\t", "<", ">", '"', "'")):
        return ""
    return s

def control_btn(p):
    act = str(p.get("control_action") or "")
    if act not in CONTROL_ACTIONS:
        return ""
    label = h(p.get("action_label") or p.get("name") or "Do")
    return f'<button type="button" data-action="{h(act)}">{label}</button>'

def chip_html(st, label):
    border, bg, fg = CHIP_COLORS.get(st, CHIP_COLORS["parked"])
    return f'<span class="chip" style="--c:{border};--bg:{bg};--fg:{fg}">{h(label)}</span>'

def pending_dec_link(verb, pid, title, extra_class=""):
    href = safe_href(decision_href(verb, pid, title))
    if not href:
        return ""
    cls = "dec" + ((" " + extra_class) if extra_class else "")
    label = {"APPROVE": "Approve", "HOLD": "Hold", "DENY": "Deny"}[verb]
    return (
        f'<a class="{cls}" data-dec="{h(verb)}" href="{h(href)}" '
        f'target="_blank" rel="noopener noreferrer">{label}</a>'
    )

def pending_item_html(it):
    if not isinstance(it, dict):
        return ""
    pid = str(it.get("id") or "")
    if not re.match(r"^[a-zA-Z0-9._-]+$", pid):
        return ""
    title = it.get("title") or pid
    risk = str(it.get("risk") or "low").lower()
    if risk not in ("high", "medium", "low"):
        risk = "low"
    kind = str(it.get("kind") or "ops")
    detail = short_note(it.get("detail") or "", 72)
    focus = focus_key("decision", pid)
    focus_attr = f' data-focus-key="{h(focus)}" tabindex="-1"' if focus else ""
    return (
        f'<div class="pending-item" data-id="{h(pid)}" data-title="{h(title)}"{focus_attr}>'
        f'<div class="pending-head"><div class="ptitle">{h(title)}</div>'
        f'<span class="prisk {h(risk)}">{h(risk)}</span></div>'
        f'<div class="pdetail">{h(detail)}</div>'
        f'<div class="prow">'
        f'{pending_dec_link("APPROVE", pid, title)}'
        f'{pending_dec_link("HOLD", pid, title, "warn")}'
        f'{pending_dec_link("DENY", pid, title, "danger")}'
        f'</div></div>'
    )

def pending_shell(items):
    attn, low = split_pending(items)
    attn_html = [r for r in (pending_item_html(it) for it in attn) if r]
    low_html = [r for r in (pending_item_html(it) for it in low) if r]
    more = ""
    if low_html:
        n = len(low_html)
        label = str(n) + (" lower-risk item" if n == 1 else " lower-risk items")
        more = (
            f'<details class="pending-more"><summary>{h(label)}</summary>'
            + "".join(low_html)
            + "</details>"
        )
    hidden = "" if (attn_html or low_html) else " hidden"
    return (
        f'<div id="pending-box" class="pending-box"{hidden}>'
        f'<p class="pending-help">Public board -- Approve opens a GitHub issue as <code>rupret007</code>.</p>'
        f'<div id="pending-list">{"".join(attn_html)}{more}</div></div>'
    )

def tools_row(projects):
    btns = []
    for p in projects or []:
        if not isinstance(p, dict):
            continue
        html_btn = control_btn(p)
        if html_btn:
            btns.append(html_btn)
    if not btns:
        return ""
    return '<div class="tools">' + "".join(btns) + "</div>"

def tap_link(href, label, extra=""):
    if not href:
        return ""
    cls = "dec" + ((" " + extra) if extra else "")
    return (
        f'<a class="{cls}" data-open="work" href="{h(href)}" '
        f'target="_blank" rel="noopener noreferrer">{h(label)}</a>'
    )

def compact_agent_detail(agent):
    """Short public detail for pill face; full safe detail remains in title."""
    if not isinstance(agent, dict):
        return ""
    aid = str(agent.get("id") or "").strip().lower()
    state = str(agent.get("state") or "unknown").strip().lower()
    detail = str(agent.get("detail") or "").strip()
    if aid == "codex":
        if state == "running":
            return "running · ChatGPT app"
        if state == "installed":
            return "installed · ChatGPT tools"
    if aid == "cursor":
        return "Cursor.app desktop"
    if aid == "claude":
        return "Anthropic desktop/CLI"
    if aid == "gemini":
        return "BYOK API key" if state in {"ready", "installed"} else "BYOK key needed"
    if aid == "minimax":
        return "BYOK API key" if state in {"ready", "installed"} else "BYOK key needed"
    if aid == "grok":
        return "conductor + Cloud Agents"
    if detail:
        return short_note(detail, 38)
    return "status unknown"

def lane_html(p):
    chip_label = visible_chip(p)
    chip = chip_html(p.get("status") or "parked", chip_label) if chip_label else ""
    title = h(p.get("name") or "project")
    hrefs = lane_hrefs(p)
    title_url = hrefs.get("title") or ""
    title_html = (
        f'<a data-open="work" href="{h(title_url)}" target="_blank" rel="noopener noreferrer">{title}</a>'
        if title_url else title
    )
    signal = compact_signal(p)
    href = signal_href(p)
    if signal and href:
        signal_html = (
            f'<a class="signal" data-open="work" href="{h(href)}" '
            f'target="_blank" rel="noopener noreferrer">{h(signal)}</a>'
        )
    else:
        signal_html = f'<span class="signal">{h(signal)}</span>' if signal else ""
    note = short_note(p.get("notes") or "", 88)
    notes_html = f'<p class="notes">{h(note)}</p>' if note else ""
    quiet = " is-quiet" if is_quiet_lane(p) else ""
    links = []
    if hrefs.get("agent"):
        links.append(tap_link(hrefs["agent"], "Open agent"))
    if hrefs.get("pr"):
        links.append(tap_link(hrefs["pr"], "Open PR"))
    if hrefs.get("repo"):
        links.append(tap_link(hrefs["repo"], "Open repo"))
    if hrefs.get("ci"):
        links.append(tap_link(hrefs["ci"], "Open CI"))
    if hrefs.get("game"):
        links.append(tap_link(hrefs["game"], "Play game"))
    links_html = ('<div class="lane-links">' + "".join(links) + "</div>") if links else ""
    focus = focus_key("project", p.get("name"))
    focus_attr = f' data-focus-key="{h(focus)}" tabindex="-1"' if focus else ""
    detail_attr = f' data-detail-kind="project" data-detail-key="{h(focus)}"' if focus else ""
    return (
        f'<article class="lane{quiet}"{focus_attr}{detail_attr}>'
        f'<h3>{title_html}</h3>'
        f'<div class="lane-end">{chip}{signal_html}</div>'
        f'{links_html}{notes_html}</article>'
    )

def lanes_html(projects, *, sort_attention=False):
    rows = [p for p in (projects or []) if isinstance(p, dict)]
    if sort_attention:
        rows = sorted(rows, key=attention_rank)
    return '<div class="lanes">' + "".join(lane_html(p) for p in rows) + "</div>"

sections_html = []
control_projects = []

def _work_chip(status):
    st = str(status or "idle").strip().lower()
    mapping = {
        "running": ("green", "Running"),
        "finished": ("parked", "Finished"),
        "blocked": ("red", "Blocked"),
        "idle": ("parked", "Idle"),
    }
    color, label = mapping.get(st, mapping["idle"])
    return chip_html(color, label)

def _work_meta_html(row):
    """Return the meta line as safe HTML with linked repo and PR number."""
    pr = safe_pr_url(row.get("pr_url"))
    repo_display = _clean_line(row.get("repo"), 96)
    repo_url = safe_repo_url("https://github.com/" + repo_display) if repo_display else ""
    pr_number = str(row.get("pr_number") or "").strip()
    branch = _safe_branch(row.get("branch"))
    why = _clean_line(row.get("why"), 120)
    bits = []
    if repo_url:
        bits.append(f'<a href="{h(repo_url)}" class="meta-link" target="_blank" rel="noopener">{h(repo_display)}</a>')
    elif repo_display:
        bits.append(h(repo_display))
    if pr and pr_number:
        bits.append(f'<a href="{h(pr)}" class="meta-link" target="_blank" rel="noopener">PR #{h(pr_number)}</a>')
    elif pr_number:
        bits.append(f'PR #{h(pr_number)}')
    if branch:
        bits.append(h(branch))
    if why:
        bits.append(h(why))
    return " - ".join(bits)

def work_row_html(row):
    lane = row if isinstance(row, dict) else {}
    lane_id_raw = str(lane.get("id") or "lane").strip().lower()
    if not re.match(r"^[a-z0-9-]{1,64}$", lane_id_raw):
        lane_id_raw = "lane"
    lane_id = h(lane_id_raw)
    detail_key = "work:" + lane_id_raw
    name = h(lane.get("name") or lane.get("id") or "LLM")
    task = h(lane.get("task_title") or "idle - needs assignment")
    meta_html = _work_meta_html(lane)
    goal = h(_clean_line(lane.get("goal"), 260))
    note = h(_clean_line(lane.get("note"), 200))
    last_task = h(_clean_line(lane.get("last_task"), 160))
    pr = safe_pr_url(lane.get("pr_url"))
    agent = safe_agent_url(lane.get("agent_url"))
    repo_display = _clean_line(lane.get("repo"), 96)
    repo_url = safe_repo_url("https://github.com/" + repo_display) if repo_display else ""
    links = []
    if agent:
        links.append(tap_link(agent, "Open agent"))
    if pr:
        links.append(tap_link(pr, "Open PR"))
    if repo_url:
        links.append(tap_link(repo_url, "Open repo"))
    links_html = ('<div class="agent-links">' + "".join(links) + "</div>") if links else ""
    goal_html = f'<p class="goal">{goal}</p>' if goal else ""
    note_html = f'<p class="note">{note}</p>' if note else ""
    last_html = f'<p class="last-task">Last: {last_task}</p>' if last_task else ""
    return (
        f'<article class="agent-row" data-lane-id="{lane_id}" data-lane-status="{h(lane.get("status") or "idle")}" '
        f'data-detail-kind="work" data-detail-key="{h(detail_key)}">'
        f'<div class="agent-head"><h3>{name}</h3>{_work_chip(lane.get("status"))}</div>'
        f'<p class="task">{task}</p>'
        f'<p class="meta">{meta_html}</p>'
        f'{goal_html}{note_html}{last_html}{links_html}</article>'
    )

def agents_strip_html(work_rows):
    rows = [row for row in (work_rows or []) if isinstance(row, dict)]
    return (
        '<section class="agents-strip" id="agents-strip" aria-label="LLM work now">'
        '<h2 class="llm-work-title">Work now</h2>'
        + "".join(work_row_html(row) for row in rows)
        + "</section>"
    )

def fetched_line_html(repos):
    names = [str(x).strip() for x in (repos or []) if str(x).strip()]
    return (
        '<p id="fetched-line">Live CI via <code>gh</code>: '
        + h(", ".join(names))
        + ".</p>"
    )

for sec in status["sections"]:
    sid_raw = str(sec.get("id") or "")
    kind = presentation(sid_raw)
    if kind == "pulse":
        continue
    if sid_raw == "controls":
        control_projects = list(sec.get("projects") or [])
        items = status.get("pending") or []
        heading = f'<h2>{h(sec.get("title") or "Decisions")}</h2>'
        body = pending_shell(items)
        sections_html.append(
            f'<section id="{h(sid_raw)}" class="block pending" data-tab-panel="{h(sid_raw)}" '
            f'hidden role="tabpanel" aria-label="Decisions">'
            f'{heading}{body}</section>'
        )
        continue
    projects = [p for p in (sec.get("projects") or []) if isinstance(p, dict)]
    if sid_raw == "features":
        body = (
            '<details class="how-board"><summary>How this board works</summary>'
            '<p class="pending-help">Engineer notes -- not the daily ops list.</p>'
            '<p class="pending-help">Public board -- Approve opens a GitHub issue; submit while logged in as <code>rupret007</code>.</p>'
            + tools_row(control_projects)
            + lanes_html(projects)
            + fetched_line_html(status.get("fetched_repos"))
            + "</details>"
        )
        sections_html.append(
            f'<section id="{h(sid_raw)}" class="block foot">{body}</section>'
        )
        continue
    if sid_raw == "abilities":
        body = (
            '<details class="abilities-foot"><summary>What Bob can do</summary>'
            '<p class="pending-help">Texts / food after Jeff yes. No send button. Honest: there is no order button on this board.</p>'
            + lanes_html(projects)
            + "</details>"
        )
        sections_html.append(
            f'<section id="{h(sid_raw)}" class="block foot">{body}</section>'
        )
        continue
    heading = f'<h2>{h(sec.get("title") or "")}</h2>'
    sort_attn = kind == "primary"
    body = lanes_html(projects, sort_attention=sort_attn)
    cls = "primary" if kind == "primary" else "secondary"
    panel = ""
    if is_type_tab(sid_raw):
        panel = (
            f' data-tab-panel="{h(sid_raw)}" hidden role="tabpanel" '
            f'aria-labelledby="tab-{h(sid_raw)}"'
        )
    sections_html.append(
        f'<section id="{h(sid_raw)}" class="block {cls}"{panel}>{heading}{body}</section>'
    )


html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
<meta name="color-scheme" content="dark"/>
<title>Bob Ops Dashboard -- Jeff Story</title>
<style>
  :root {{
    --bg:#0a0a0a; --panel:#141414; --panel2:#1c1c1c; --text:#f5f5f5; --muted:#8a8a8a;
    --border:#262626; --accent:#d97757; --link:#e8a080; --orange:#d97757;
    --orange-dim:#2a1510; --ok:#16a34a; --warn:#ca8a04; --hair:rgba(255,255,255,.07);
  }}
  * {{ box-sizing:border-box; }}
  html,body {{ margin:0; padding:0; background:var(--bg); color:var(--text);
    font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
    font-size:16px; line-height:1.4; }}
  a {{ color:var(--link); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
  .wrap {{ max-width:40rem; margin:0 auto; padding:calc(1rem + env(safe-area-inset-top, 0px)) 1rem calc(3.25rem + env(safe-area-inset-bottom, 0px)); }}
  header.pulse {{ padding:0 0 .65rem; margin:0 0 1rem; border:0; background:transparent; }}
  header.pulse h1 {{ margin:0; font-size:.92rem; font-weight:700; letter-spacing:-.01em; }}
  header.pulse h1 .mark {{ color:var(--orange); }}
  .pulse-row {{ display:flex; flex-direction:column; gap:.4rem; margin-top:.45rem; }}
  .board-glance {{
    display:flex; align-items:center; margin:0 0 .55rem; padding:.1rem 0 .2rem; border:0;
    background:transparent; color:#fff; font:inherit; font-size:1.55rem;
    font-weight:800; letter-spacing:-.03em; line-height:1.15; min-height:52px;
    text-align:left; width:100%; cursor:pointer; touch-action:manipulation;
  }}
  .board-glance:not([data-tab]) {{ cursor:default; }}
  .type-tabs {{
    display:flex; gap:.28rem; overflow-x:auto; -webkit-overflow-scrolling:touch;
    margin:0 0 1rem; padding:.1rem 0 .35rem; scrollbar-width:none;
  }}
  .type-tabs::-webkit-scrollbar {{ display:none; }}
  .type-tabs button {{
    flex:1 1 0; min-width:0; min-height:44px; padding:.4rem .2rem; border:1px solid var(--border);
    border-radius:999px; background:transparent; color:var(--muted);
    font-size:.72rem; font-weight:600; cursor:pointer; touch-action:manipulation;
  }}
  .type-tabs button[aria-selected="true"] {{ border-color:var(--orange); color:var(--orange); }}
  .chip {{ display:inline-flex; align-items:center; color:var(--c);
    background:transparent; border:0; padding:0; font-size:.68rem; font-weight:700;
    text-transform:uppercase; letter-spacing:.04em; white-space:nowrap; }}
  section.block {{ margin:0 0 1.55rem; padding:0; border:0; }}
  section.block[hidden] {{ display:none !important; }}
  section.block.pending {{ margin-bottom:2rem; }}
  section.block.primary {{ margin-bottom:2.1rem; }}
  section.block.secondary {{ margin-bottom:1.35rem; }}
  section.block.foot {{ margin:2rem 0 0; }}
  section.pending h2, section.primary h2 {{
    margin:0 0 .65rem; padding:0; border:0;
    font-size:1.7rem; font-weight:800; letter-spacing:-.03em; line-height:1.1; color:#fff;
  }}
  section.secondary h2 {{
    margin:0 0 .35rem; padding:0; border:0;
    font-size:.7rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--muted);
  }}
  .lanes {{ display:flex; flex-direction:column; gap:.05rem; }}
  .lane {{
    display:grid; grid-template-columns:minmax(0,1fr) auto; column-gap:.75rem; row-gap:.15rem;
    padding:.78rem 0; border:0; border-bottom:1px solid var(--hair); background:transparent; border-radius:0;
  }}
  .lane[data-detail-key], .agent-row[data-detail-key] {{ cursor:pointer; touch-action:manipulation; }}
  .lane:last-child {{ border-bottom:0; }}
  .lane h3 {{ margin:0; font-size:.95rem; font-weight:600; letter-spacing:-.01em; }}
  .lane-end {{ display:flex; align-items:center; gap:.45rem; justify-self:end; }}
  .lane .signal {{ color:var(--muted); font-size:.75rem; font-variant-numeric:tabular-nums; }}
  .lane a.signal {{ color:var(--link); text-decoration:none; }}
  .lane a.signal:hover {{ text-decoration:underline; }}
  .lane-links {{
    grid-column:1 / -1; display:flex; flex-wrap:wrap; gap:.35rem; margin:.2rem 0 0;
  }}
  .lane .notes {{
    grid-column:1 / -1; margin:0; color:var(--muted); font-size:.78rem; line-height:1.35;
    display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:1; overflow:hidden;
  }}
  .lane.is-quiet .notes {{ display:none; }}
  code {{ background:var(--panel2); padding:.05rem .35rem; border-radius:6px; font-size:.78rem; }}
  footer {{ margin-top:1.25rem; padding-top:1rem; border-top:1px solid var(--hair); color:var(--muted); font-size:.75rem; }}
  .status {{ font-size:.78rem; min-height:1.1em; margin-top:.4rem; color:var(--muted); }}
  #panel-status:empty {{ display:none; min-height:0; margin:0; }}
  .status.ok {{ color:var(--ok); }}
  .status.bad {{ color:#f87171; }}
  .status.warn {{ color:var(--warn); }}
  .status.hint {{ color:var(--muted); }}
  .pending-box {{ margin:0; }}
  .pending-box[hidden] {{ display:none !important; }}
  .pending-help {{ margin:0 0 .7rem; color:var(--muted); font-size:.8rem; line-height:1.4; }}
  .pending-item {{
    border:0; border-bottom:1px solid var(--hair); border-radius:0;
    padding:.72rem 0; margin:0; background:transparent;
  }}
  .lane:focus, .pending-item:focus {{ outline:2px solid var(--orange); outline-offset:3px; }}
  .lane.is-glance-target, .pending-item.is-glance-target {{
    background:linear-gradient(90deg, rgba(217,119,87,.16), rgba(217,119,87,0));
    box-shadow:inset 3px 0 0 var(--orange);
  }}
  @media (prefers-reduced-motion:no-preference) {{
    .lane.is-glance-target, .pending-item.is-glance-target {{
      animation:glance-target 700ms ease-out 2;
    }}
  }}
  @keyframes glance-target {{
    0% {{ background-color:rgba(217,119,87,.24); }}
    100% {{ background-color:transparent; }}
  }}
  .pending-item:last-child {{ border-bottom:0; }}
  .pending-item .ptitle {{ font-weight:600; font-size:.95rem; margin:0; }}
  .pending-head {{ display:flex; align-items:baseline; justify-content:space-between; gap:.6rem; }}
  .pending-item .pdetail {{ display:none; }}
  .pending-item .prisk {{
    display:inline-block; font-size:.65rem; text-transform:uppercase; letter-spacing:.04em;
    color:var(--muted); padding:0; margin:0; flex:0 0 auto;
  }}
  .pending-item .prisk.high {{ color:#fca5a5; }}
  .pending-item .prisk.medium {{ color:#fde68a; }}
  .pending-item .prisk.low {{ color:#86efac; }}
  .pending-item .prow {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:.4rem; margin-top:.45rem; }}
  .pending-item button, .pending-item a.dec, .tools button, .lane-links a.dec, .agent-links a.dec {{
    background:transparent; color:var(--text); border:1px solid var(--border);
    border-radius:8px; padding:.4rem .35rem; font-size:.78rem; cursor:pointer;
    min-height:44px; touch-action:manipulation; width:100%;
    display:inline-flex; align-items:center; justify-content:center;
    text-align:center; text-decoration:none; box-sizing:border-box;
  }}
  .lane-links a.dec, .agent-links a.dec {{ width:auto; min-width:4.4rem; padding:.35rem .55rem; font-size:.75rem; }}
  .pending-item button:hover, .pending-item a.dec:hover, .tools button:hover, .lane-links a.dec:hover, .agent-links a.dec:hover {{
    border-color:var(--orange); color:var(--orange); text-decoration:none;
  }}
  .pending-item button.warn, .pending-item a.dec.warn {{ border-color:#ca8a04; }}
  .pending-item button.danger, .pending-item a.dec.danger {{ border-color:#dc2626; color:#fecaca; }}
  .live-stamp {{ display:flex; flex-wrap:wrap; align-items:center; gap:.4rem; color:var(--muted); font-size:.8rem; }}
  .live-stamp .when {{ color:var(--muted); font-weight:500; }}
  .live-dot {{ width:.5rem; height:.5rem; border-radius:50%; background:var(--orange);
    box-shadow:0 0 0 0 rgba(217,119,87,.55); animation:pulse 2s infinite; }}
  .live-dot.stale {{ background:#64748b; animation:none; }}
  .live-dot.poll {{ background:var(--ok); }}
  @keyframes pulse {{ 0% {{ box-shadow:0 0 0 0 rgba(217,119,87,.55); }}
    70% {{ box-shadow:0 0 0 8px rgba(217,119,87,0); }} 100% {{ box-shadow:0 0 0 0 rgba(217,119,87,0); }} }}
  #freshness {{ color:var(--orange); font-weight:700; font-size:.8rem; }}
  #freshness.stale {{ color:var(--muted); font-weight:600; }}
  #silence-banner {{
    display:none; margin-bottom:1rem; padding:.75rem .9rem;
    background:#2a0a0a; border:1px solid #dc2626; border-radius:8px;
    color:#fecaca; font-size:.88rem; line-height:1.35;
  }}
  #silence-banner.show {{ display:flex; gap:.75rem; align-items:center; justify-content:space-between; }}
  .silence-copy {{ display:grid; gap:.15rem; min-width:0; }}
  #silence-title {{ font-weight:800; }}
  #silence-detail {{ color:#fca5a5; }}
  #retry-status {{
    flex:0 0 auto; min-height:44px; padding:.5rem .75rem;
    border:1px solid #f87171; border-radius:8px; background:#450a0a;
    color:#fee2e2; font:inherit; font-weight:800; cursor:pointer;
    touch-action:manipulation;
  }}
  #retry-status:hover {{ border-color:#fecaca; background:#601111; }}
  #retry-status:disabled {{ cursor:wait; opacity:.7; }}
  body.snapshot-unverified #board {{ opacity:.78; }}
  @media (max-width:520px) {{
    #silence-banner.show {{ align-items:stretch; flex-direction:column; }}
    #retry-status {{ width:100%; }}
  }}
  #board {{ min-height:2rem; }}
  #active-agents {{ margin:0; }}
  .agents-strip {{
    display:grid; grid-template-columns:1fr; gap:.52rem; align-items:stretch;
    margin:0; padding:0; border:0; background:transparent;
  }}
  .llm-work-title {{
    margin:0 0 .15rem; font-size:.86rem; font-weight:800; letter-spacing:.02em;
    text-transform:uppercase; color:var(--muted);
  }}
  .agent-row {{
    border:1px solid var(--border); border-radius:10px;
    padding:.58rem .62rem; background:rgba(255,255,255,.02);
    display:grid; gap:.22rem;
  }}
  .agent-row .agent-head {{
    display:flex; align-items:center; justify-content:space-between; gap:.4rem;
  }}
  .agent-row h3 {{ margin:0; font-size:.9rem; font-weight:700; letter-spacing:-.01em; }}
  .agent-row p {{ margin:0; }}
  .agent-row .task {{ font-size:.82rem; font-weight:650; color:#fff; }}
  .agent-row .meta {{ font-size:.74rem; color:var(--muted); line-height:1.3; }}
  .agent-row .meta .meta-link {{ color:var(--link); text-decoration:none; }}
  .agent-row .meta .meta-link:hover {{ text-decoration:underline; }}
  .agent-row .goal {{ font-size:.75rem; color:var(--muted); line-height:1.32; }}
  .agent-row .note, .agent-row .last-task {{
    font-size:.75rem; color:var(--muted); line-height:1.32;
  }}
  body.tab-home section.block.foot {{ display:none; }}
  body.tab-home footer {{ display:none; }}
  body.tab-home .live-stamp .when {{ display:none; }}
  body.tab-home .agent-links {{ display:none; }}
  body.tab-home .agent-row .note,
  body.tab-home .agent-row .last-task {{ display:none; }}
  .agent-links {{ display:flex; flex-wrap:wrap; gap:.35rem; }}
  .tools {{ display:flex; flex-wrap:wrap; gap:.45rem; margin:0 0 .85rem; }}
  .how-board, .abilities-foot {{ margin:0; }}
  .how-board summary, .abilities-foot summary {{
    cursor:pointer; font-size:.85rem; font-weight:600; color:var(--muted);
    padding:.2rem 0; list-style:outside disclosure-closed;
  }}
  .how-board[open] summary, .abilities-foot[open] summary {{ color:var(--orange); margin-bottom:.55rem; }}
  .pending-more {{ margin:.1rem 0 0; border:0; background:transparent; }}
  .pending-more > summary {{
    cursor:pointer; color:var(--muted); font-size:.8rem; font-weight:600;
    min-height:44px; display:flex; align-items:center;
    list-style:outside disclosure-closed;
  }}
  .pending-more[open] > summary {{ color:var(--orange); }}
  .detail-scrim {{
    position:fixed; inset:0; background:rgba(0,0,0,.68); z-index:40;
    display:none; opacity:0; transition:opacity .15s ease-out;
  }}
  .detail-scrim.show {{ display:block; opacity:1; }}
  .detail-sheet {{
    position:fixed; left:0; right:0; bottom:0; max-height:85vh; overflow:auto;
    border-top:1px solid var(--border); border-radius:14px 14px 0 0;
    background:var(--panel); z-index:41; padding:.9rem 1rem calc(1rem + env(safe-area-inset-bottom, 0px));
    transform:translateY(104%); transition:transform .17s ease-out;
    box-shadow:0 -14px 32px rgba(0,0,0,.42);
  }}
  .detail-sheet.show {{ transform:translateY(0); }}
  .detail-head {{
    display:flex; align-items:flex-start; justify-content:space-between; gap:.6rem; margin:0 0 .65rem;
  }}
  .detail-head h2 {{ margin:0; font-size:1.05rem; line-height:1.25; }}
  .detail-close {{
    min-height:44px; min-width:44px; border:1px solid var(--border); border-radius:8px;
    background:transparent; color:var(--text); font-size:1rem; font-weight:700; cursor:pointer;
  }}
  .detail-close:hover {{ border-color:var(--orange); color:var(--orange); }}
  .detail-meta, .detail-note, .detail-time {{ margin:0 0 .55rem; color:var(--muted); font-size:.82rem; line-height:1.4; }}
  .detail-note {{ color:var(--text); white-space:pre-wrap; }}
  .detail-task, .detail-goal {{ margin:0 0 .55rem; font-size:.83rem; line-height:1.4; color:#fff; white-space:pre-wrap; }}
  .detail-facts {{
    margin:0 0 .7rem; padding:0; display:grid; grid-template-columns:auto 1fr; gap:.26rem .6rem;
  }}
  .detail-facts dt {{ margin:0; color:var(--muted); font-size:.74rem; }}
  .detail-facts dd {{ margin:0; color:#fff; font-size:.78rem; overflow-wrap:anywhere; }}
  .detail-links {{ display:flex; flex-wrap:wrap; gap:.42rem; margin:0 0 .72rem; }}
  .detail-links a {{
    min-height:44px; display:inline-flex; align-items:center; justify-content:center;
    border:1px solid var(--border); border-radius:8px; padding:.35rem .6rem; font-size:.78rem;
    text-decoration:none;
  }}
  .detail-links a:hover {{ border-color:var(--orange); color:var(--orange); }}
  .detail-events-title {{ margin:0 0 .35rem; font-size:.76rem; letter-spacing:.05em; text-transform:uppercase; color:var(--muted); }}
  .detail-events {{ margin:0; padding:0 0 0 1rem; display:grid; gap:.36rem; }}
  .detail-events li {{ color:var(--muted); font-size:.78rem; line-height:1.35; }}
  .detail-events a {{ color:var(--link); }}
  @media (prefers-reduced-motion: reduce) {{
    .live-dot {{ animation:none; box-shadow:none; }}
    .detail-scrim, .detail-sheet {{ transition:none; }}
  }}
  @media (min-width:720px) {{
    .wrap {{ padding:1.5rem 1.25rem 3.75rem; }}
    .lane .notes {{ -webkit-line-clamp:2; }}
    .pending-item .pdetail {{
      display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; overflow:hidden;
      color:var(--muted); font-size:.78rem; margin:.15rem 0 0;
    }}
    .lane.is-quiet .notes {{ display:-webkit-box; }}
    section.pending h2, section.primary h2 {{ font-size:1.85rem; }}
    .type-tabs {{ flex-wrap:wrap; overflow:visible; }}
    .type-tabs button {{ flex:0 0 auto; padding:.4rem .85rem; font-size:.8rem; }}
    .agents-strip {{ grid-template-columns:repeat(auto-fit,minmax(17.5rem,1fr)); }}
    body.tab-home .agent-row .note,
    body.tab-home .agent-row .last-task {{ display:block; }}
  }}
</style>
</head>
<body class="tab-home">
<div class="wrap">
  <header class="pulse">
    <h1><span class="mark">Bob</span> Ops</h1>
    <div class="pulse-row">
      <div class="live-stamp" id="live-stamp" data-generated-at="{h(updated_iso)}" data-display="{h(updated_ct)}"><span class="live-dot" id="live-dot" aria-hidden="true"></span><span id="freshness">Live - starting</span><span class="when"> · <strong id="updated-display">{h(updated_ct)}</strong></span></div>
      <div id="active-agents">{agents_strip_html(status.get("llm_work"))}</div>
    </div>
    <div class="status hint" id="panel-status"></div>
  </header>
  <div id="silence-banner" role="alert" aria-live="assertive" hidden>
    <span class="silence-copy"><strong id="silence-title"></strong><span id="silence-detail"></span></span>
    <button id="retry-status" type="button">Retry now</button>
  </div>
  <div id="board" data-snapshot-trust="current">
  {glance_html(status.get("pending"), status.get("sections"))}{type_tabs_html(status.get("sections"), status.get("pending"))}
  {''.join(sections_html)}
  </div>
  <div id="detail-scrim" class="detail-scrim" hidden></div>
  <aside id="detail-sheet" class="detail-sheet" hidden role="dialog" aria-modal="true" aria-labelledby="detail-title">
    <div class="detail-head">
      <h2 id="detail-title">Details</h2>
      <button id="detail-close" class="detail-close" type="button" aria-label="Close details">Close</button>
    </div>
    <p id="detail-meta" class="detail-meta"></p>
    <p id="detail-time" class="detail-time"></p>
    <p id="detail-task" class="detail-task"></p>
    <p id="detail-goal" class="detail-goal"></p>
    <dl id="detail-facts" class="detail-facts"></dl>
    <p id="detail-note" class="detail-note"></p>
    <div id="detail-links" class="detail-links"></div>
    <h3 id="detail-events-title" class="detail-events-title" hidden>Recent events</h3>
    <ul id="detail-events" class="detail-events"></ul>
  </aside>
  <footer>
    <p><a href="https://github.com/rupret007/bob-ops-dashboard">rupret007/bob-ops-dashboard</a>
    · <a href="./status.json">status.json</a></p>
  </footer>
</div>
<script id="status-bootstrap" type="application/json">{json.dumps(status, ensure_ascii=True).replace("<", "\\u003c")}</script>
<script>
function focusKey(kind, raw) {{
  var prefix = String(kind || "").replace(/^\s+|\s+$/g, "").toLowerCase();
  var value = String(raw || "").replace(/^\s+|\s+$/g, "");
  var token = "";
  if (prefix === "decision") {{
    if (value.length > 64 || !/^[a-zA-Z0-9._-]+$/.test(value)) return "";
    token = value.toLowerCase();
  }} else if (prefix === "project") {{
    token = value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64).replace(/-+$/g, "");
    if (!token) return "";
  }} else {{
    return "";
  }}
  return prefix + ":" + token;
}}
(function () {{
  // Public board: URL is enough. Real yes is a GitHub issue from rupret007.
  var statusEl = document.getElementById("panel-status");
  var decideBusy = {{}};
  var pendingSeq = 0;

  function pendingEls() {{
    return {{
      box: document.getElementById("pending-box"),
      list: document.getElementById("pending-list")
    }};
  }}

  // Drop the retired device flag. Not a login and never consulted.
  try {{ localStorage.removeItem("bobOpsJeffAuth_v1"); }} catch (e) {{}}

  function setStatus(msg, kind) {{
    if (!statusEl) return;
    statusEl.textContent = msg || "";
    statusEl.className = "status " + (kind || "hint");
  }}

  function riskClass(r) {{
    r = (r || "low").toLowerCase();
    if (r === "high") return "high";
    if (r === "medium") return "medium";
    return "low";
  }}

  function decisionHref(verb, id, title) {{
    if (verb !== "APPROVE" && verb !== "DENY" && verb !== "HOLD") return "";
    var pid = String(id || "").trim();
    if (!/^[a-zA-Z0-9._-]+$/.test(pid)) return "";
    var t = "BOB-" + verb + ": " + pid;
    var body = [
      "Dashboard control decision",
      "",
      "id: " + pid,
      "title: " + String(title || pid).slice(0, 160),
      "decision: " + verb.toLowerCase(),
      "from: public board",
      "",
      "Submit this issue while logged in as rupret007. That GitHub login is the real yes.",
      "Bob: treat this as a one-shot inbox item. High-risk still needs the draft shown in chat before acting."
    ].join(String.fromCharCode(10));
    return "https://github.com/rupret007/bob-ops-dashboard/issues/new?title=" +
      encodeURIComponent(t) + "&body=" + encodeURIComponent(body);
  }}
  window.decisionHref = decisionHref;

  function openBlank(url) {{
    if (!url) return false;
    var w = null;
    try {{ w = window.open(url, "_blank", "noopener,noreferrer"); }} catch (e) {{ w = null; }}
    if (w) return true;
    var a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    if (a.remove) a.remove();
    else if (a.parentNode) a.parentNode.removeChild(a);
    return true;
  }}
  window.openBlank = openBlank;

  function openDecisionIssue(verb, id, title) {{
    var url = decisionHref(verb, id, title);
    if (!url) {{
      setStatus("Bad pending id", "bad");
      return;
    }}
    var key = verb + ":" + String(id || "").trim();
    if (decideBusy[key]) return;
    decideBusy[key] = 1;
    setTimeout(function () {{ delete decideBusy[key]; }}, 2000);
    openBlank(url);
    setStatus(verb + " draft opened on GitHub. Submit the issue while logged in as rupret007.", "warn");
  }}

  function renderPending(items) {{
    var els = pendingEls();
    if (!els.box || !els.list) return;
    var rank = {{ high: 0, medium: 1, low: 2 }};
    var rows = (items || []).filter(function (it) {{
      return it && /^[a-zA-Z0-9._-]+$/.test(String(it.id || ""));
    }}).slice().sort(function (a, b) {{
      var ra = rank.hasOwnProperty(String(a.risk || "").toLowerCase()) ? rank[String(a.risk).toLowerCase()] : 5;
      var rb = rank.hasOwnProperty(String(b.risk || "").toLowerCase()) ? rank[String(b.risk).toLowerCase()] : 5;
      return ra - rb;
    }});
    els.list.innerHTML = "";
    els.box.hidden = rows.length === 0;
    var sec = document.getElementById("controls");
    if (sec) sec.hidden = rows.length === 0;
    if (!rows.length) return;
    var attn = [];
    var low = [];
    rows.forEach(function (it) {{
      if (String(it.risk || "").toLowerCase() === "low") low.push(it);
      else attn.push(it);
    }});
    function pendingNode(it) {{
      var div = document.createElement("div");
      div.className = "pending-item";
      div.setAttribute("data-id", String(it.id));
      div.setAttribute("data-title", String(it.title || it.id));
      var focus = focusKey("decision", it.id);
      if (focus) {{
        div.setAttribute("data-focus-key", focus);
        div.setAttribute("tabindex", "-1");
      }}
      div.innerHTML =
        '<div class="pending-head"><div class="ptitle"></div><span class="prisk"></span></div>' +
        '<div class="pdetail"></div>' +
        '<div class="prow"></div>';
      var prow = div.querySelector(".prow");
      function decLink(verb, extra) {{
        var href = decisionHref(verb, it.id, it.title || it.id);
        var a = document.createElement("a");
        a.className = "dec" + (extra ? " " + extra : "");
        a.setAttribute("data-dec", verb);
        if (href) a.setAttribute("href", href);
        a.setAttribute("target", "_blank");
        a.setAttribute("rel", "noopener noreferrer");
        a.textContent = verb === "APPROVE" ? "Approve" : verb === "HOLD" ? "Hold" : "Deny";
        return a;
      }}
      prow.appendChild(decLink("APPROVE", ""));
      prow.appendChild(decLink("HOLD", "warn"));
      prow.appendChild(decLink("DENY", "danger"));
      div.querySelector(".ptitle").textContent = it.title || it.id;
      var detail = String(it.detail || "");
      if (detail.length > 72) {{
        var cut = detail.slice(0, 71);
        var sp = cut.lastIndexOf(" ");
        detail = (sp > 24 ? cut.slice(0, sp) : cut).replace(/[.,;:]$/, "") + "...";
      }}
      div.querySelector(".pdetail").textContent = detail;
      var rk = div.querySelector(".prisk");
      rk.textContent = it.risk || "low";
      rk.classList.add(riskClass(it.risk));
      return div;
    }}
    attn.forEach(function (it) {{ els.list.appendChild(pendingNode(it)); }});
    if (low.length) {{
      var more = document.createElement("details");
      more.className = "pending-more";
      var sum = document.createElement("summary");
      sum.textContent = low.length + (low.length === 1 ? " lower-risk item" : " lower-risk items");
      more.appendChild(sum);
      low.forEach(function (it) {{ more.appendChild(pendingNode(it)); }});
      els.list.appendChild(more);
    }}
  }}

  function loadPending() {{
    var list = document.getElementById("pending-list");
    // First paint already has the inbox. Do not wipe/rebuild it (flash).
    if (list && list.querySelector(".pending-item")) return;
    var seq = ++pendingSeq;
    fetch("./status.json?ts=" + Date.now(), {{ cache: "no-store" }})
      .then(function (r) {{
        if (!r.ok) throw new Error("status " + r.status);
        return r.json();
      }})
      .then(function (data) {{
        if (seq !== pendingSeq) return;
        renderPending((data && data.pending) || []);
      }})
      .catch(function () {{
        if (seq !== pendingSeq) return;
        // Keep first-paint pending. Do not claim the inbox is empty.
      }});
  }}

  document.addEventListener("click", function (ev) {{
    var decBtn = ev.target.closest("[data-dec]");
    if (!decBtn) return;
    var item = decBtn.closest(".pending-item");
    if (!item) return;
    var verb = decBtn.getAttribute("data-dec");
    var pid = item.getAttribute("data-id");
    var title = item.getAttribute("data-title");
    var key = verb + ":" + String(pid || "").trim();
    if (decideBusy[key]) {{
      ev.preventDefault();
      return;
    }}
    var href = decBtn.getAttribute("href") || "";
    if (decBtn.tagName === "A" && href.indexOf("https://github.com/rupret007/bob-ops-dashboard/issues/new?") === 0) {{
      decideBusy[key] = 1;
      setTimeout(function () {{ delete decideBusy[key]; }}, 2000);
      setStatus(verb + " draft opened on GitHub. Submit the issue while logged in as rupret007.", "warn");
      return;
    }}
    ev.preventDefault();
    openDecisionIssue(verb, pid, title);
  }});

  var CONTROL_ACTIONS = {{ "refresh-hint": 1, "open-repo": 1, "mark-reviewed": 1 }};
  function handleJeffAction(act) {{
    if (!CONTROL_ACTIONS[act]) return;
    if (act === "refresh-hint") {{
      var cmd = "./refresh.sh --push";
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(cmd).then(function () {{
          setStatus("Copied: " + cmd, "ok");
        }}).catch(function () {{
          setStatus(cmd, "ok");
        }});
      }} else {{
        setStatus(cmd, "ok");
      }}
    }} else if (act === "open-repo") {{
      openBlank("https://github.com/rupret007/bob-ops-dashboard");
    }} else if (act === "mark-reviewed") {{
      localStorage.setItem("bobOpsLastReviewed", new Date().toISOString());
      setStatus("Board marked reviewed locally at " + new Date().toLocaleString(), "ok");
    }}
  }}
  document.addEventListener("click", function (ev) {{
    var btn = ev.target.closest("button[data-action]");
    if (!btn) return;
    var act = btn.getAttribute("data-action");
    if (!CONTROL_ACTIONS[act]) return;
    ev.preventDefault();
    handleJeffAction(act);
  }});

  loadPending();
}})();

(function () {{
  // Near-realtime: poll status.json every 30s; paint board when content changes (no full reload).
  // Hide / iOS-return abort is not a fail. Stale cached JSON cannot rewind the board.
  var POLL_MS = 30000;
  var stamp = document.getElementById("live-stamp");
  var freshness = document.getElementById("freshness");
  var dot = document.getElementById("live-dot");
  var displayEl = document.getElementById("updated-display");
  if (!stamp || !freshness) return;

  var known = stamp.getAttribute("data-generated-at") || "";
  var knownMs = Date.parse(known) || Date.now();
  var lastPollOk = Date.now();
  var lastAgents = [];
  var lastCloud = [];
  var lastFp = null;

  function fmtAge(ms) {{
    // Actions cadence is ~15m. "Live" only while we are still inside that window.
    var s = Math.max(0, Math.floor(ms / 1000));
    if (s < 8) return "Updated just now";
    if (s < 60) return "Updated " + s + "s ago";
    var m = Math.floor(s / 60);
    if (m < 16) return "Live - updated " + m + "m ago";
    if (m < 60) return "Updated " + m + "m ago";
    var h = Math.floor(m / 60);
    return "Updated " + h + "h ago";
  }}

  function paint() {{
    var age = Date.now() - knownMs;
    var label = fmtAge(age);
    if (pollFailStreak === 0) freshness.textContent = label;
    var stale = age > 20 * 60 * 1000;
    freshness.classList.toggle("stale", stale || pollFailStreak > 0);
    if (dot) {{
      dot.classList.toggle("stale", stale || pollFailStreak > 0);
    }}
    if (lastAgents && lastAgents.length) paintAgents(lastAgents);
    if (typeof updateSilence === "function") updateSilence();
  }}

  // Actions cadence ~15m; silence = max(45m, 3x cadence) - uptime-pulse pattern.
  var EXPECTED_REFRESH_MS = 15 * 60 * 1000;
  var SILENCE_LIMIT_MS = Math.max(45 * 60 * 1000, 3 * EXPECTED_REFRESH_MS);
  var silenceEl = document.getElementById("silence-banner");
  var silenceTitleEl = document.getElementById("silence-title");
  var silenceDetailEl = document.getElementById("silence-detail");
  var retryStatus = document.getElementById("retry-status");
  var boardEl = document.getElementById("board");
  var detailScrim = document.getElementById("detail-scrim");
  var detailSheet = document.getElementById("detail-sheet");
  var detailTitleEl = document.getElementById("detail-title");
  var detailMetaEl = document.getElementById("detail-meta");
  var detailTimeEl = document.getElementById("detail-time");
  var detailTaskEl = document.getElementById("detail-task");
  var detailGoalEl = document.getElementById("detail-goal");
  var detailFactsEl = document.getElementById("detail-facts");
  var detailNoteEl = document.getElementById("detail-note");
  var detailLinksEl = document.getElementById("detail-links");
  var detailEventsEl = document.getElementById("detail-events");
  var detailEventsTitleEl = document.getElementById("detail-events-title");
  var detailCloseEl = document.getElementById("detail-close");
  var pollFailStreak = 0;
  var detailOpen = false;
  var detailToken = "";
  var detailHistoryDepth = 0;
  var lastStatusData = null;

  function bootstrapStatus() {{
    var el = document.getElementById("status-bootstrap");
    if (!el) return null;
    var raw = el.textContent || "";
    if (!raw) return null;
    try {{
      var parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : null;
    }} catch (e) {{
      return null;
    }}
  }}

  var CHIP_COLORS = {{
    "green": ["#16a34a", "#052e16", "#bbf7d0"],
    "yellow": ["#ca8a04", "#422006", "#fef08a"],
    "red": ["#dc2626", "#450a0a", "#fecaca"],
    "parked": ["#525252", "#171717", "#d4d4d4"],
    "jeff-gate": ["#d97757", "#2a1510", "#f5c4b3"]
  }};

  function esc(s) {{
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {{
      return ({{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }})[c];
    }});
  }}

  function safeHref(u) {{
    var s = String(u == null ? "" : u).trim();
    if (s.indexOf("./") === 0 && s.indexOf(":") === -1 && s.indexOf("\\\\") === -1 && !/[\\s<>"']/.test(s)) return s;
    var low = s.toLowerCase();
    if (low.indexOf("https://") !== 0 && low.indexOf("http://") !== 0) return "";
    if (/[\\s<>"']/.test(s)) return "";
    return s;
  }}
  function cleanPublicUrl(u) {{
    var s = String(u == null ? "" : u).trim();
    if (!s || /[\\s<>"']/.test(s)) return "";
    return s.split("?")[0].split("#")[0].replace(/\\/$/, "");
  }}
  function isBcId(id) {{
    var s = String(id || "").toLowerCase();
    if (s.indexOf("bc-") !== 0) return false;
    var parts = s.split("-");
    if (parts.length !== 6 || parts[0] !== "bc") return false;
    if (parts[1].length !== 8 || parts[2].length !== 4 || parts[3].length !== 4 || parts[4].length !== 4 || parts[5].length !== 12) return false;
    var hex = parts.slice(1).join("");
    for (var i = 0; i < hex.length; i++) {{
      var c = hex.charAt(i);
      if (!((c >= "0" && c <= "9") || (c >= "a" && c <= "f"))) return false;
    }}
    return true;
  }}
  function safeAgentUrl(u) {{
    var s = cleanPublicUrl(u);
    var prefix = "https://cursor.com/agents/";
    if (s.toLowerCase().indexOf(prefix) !== 0) return "";
    var bc = s.slice(prefix.length).toLowerCase();
    return isBcId(bc) ? prefix + bc : "";
  }}
  function safePrUrl(u) {{
    var s = cleanPublicUrl(u);
    return /^https:\\/\\/github\\.com\\/rupret007\\/[A-Za-z0-9._-]+\\/pull\\/[1-9][0-9]*$/i.test(s) ? s : "";
  }}
  function safeActionsUrl(u) {{
    var s = cleanPublicUrl(u);
    return /^https:\\/\\/github\\.com\\/rupret007\\/[A-Za-z0-9._-]+\\/actions\\/runs\\/[1-9][0-9]*$/i.test(s) ? s : "";
  }}
  function safeRepoUrl(u) {{
    var s = cleanPublicUrl(u);
    return /^https:\\/\\/github\\.com\\/rupret007\\/[A-Za-z0-9._-]+$/i.test(s) ? s : "";
  }}
  function safeGameUrl(u) {{
    var s = cleanPublicUrl(u);
    return /^https:\\/\\/rupret007\\.github\\.io\\/Turdanoid\\/hub\\.html$/i.test(s)
      ? "https://rupret007.github.io/Turdanoid/hub.html" : "";
  }}
  function safePullsUrl(u) {{
    var s = cleanPublicUrl(u);
    return /^https:\\/\\/github\\.com\\/rupret007\\/[A-Za-z0-9._-]+\\/pulls$/i.test(s) ? s : "";
  }}
  function safeReleaseUrl(u) {{
    var s = cleanPublicUrl(u);
    return /^https:\\/\\/github\\.com\\/rupret007\\/[A-Za-z0-9._-]+\\/releases\\/(?:latest|tag\\/[A-Za-z0-9][A-Za-z0-9._-]{{0,63}})$/i.test(s) ? s : "";
  }}
  function pullsUrlFromRepo(u) {{
    var repo = safeRepoUrl(u);
    return repo ? repo + "/pulls" : "";
  }}
  function latestReleaseUrlFromRepo(u) {{
    var repo = safeRepoUrl(u);
    return repo ? repo + "/releases/latest" : "";
  }}
  function releaseMatchesTip(p) {{
    if (!p) return null;
    var tip = String(p.tip_sha || "").trim().toLowerCase();
    var rel = String(p.release_sha || "").trim().toLowerCase();
    if (!tip || !rel) return null;
    return rel.indexOf(tip) === 0 || tip.indexOf(rel.slice(0, 7)) === 0;
  }}
  function coordPrUrl(p) {{
    if (!p || p.private) return "";
    var repo = safeRepoUrl(p.repo_url || p.html_url) || safeRepoUrl(p.url);
    var coord = p.coord && typeof p.coord === "object" ? p.coord : {{}};
    var number = coord.pr;
    if (!repo || typeof number !== "number" || !isFinite(number) || number <= 0 || Math.floor(number) !== number) return "";
    if (typeof coord.pr_draft !== "boolean" || coord.pr_draft) return "";
    var url = safePrUrl(coord.pr_url);
    return url && url.toLowerCase() === (repo + "/pull/" + number).toLowerCase() ? url : "";
  }}
  function laneHrefs(p) {{
    if (!p) return {{}};
    var ci = p.ci && typeof p.ci === "object" ? p.ci : {{}};
    var repo = safeRepoUrl(p.repo_url || p.html_url) || safeRepoUrl(p.url);
    var pr = safePrUrl(p.open_pr_url) || coordPrUrl(p) || safePrUrl(p.url);
    var agent = safeAgentUrl(p.agent_url) || safeAgentUrl(p.url);
    var game = safeGameUrl(p.live_game_url);
    var concl = String(ci.conclusion || "").toLowerCase();
    var actions = (concl === "skipped" || concl === "cancelled") ? "" : safeActionsUrl(ci.html_url || p.ci_url);
    var out = {{ title: pr || repo || "" }};
    if (agent) out.agent = agent;
    if (pr) out.pr = pr;
    if (repo) out.repo = repo;
    if (actions) out.ci = actions;
    if (game) out.game = game;
    return out;
  }}
  function detailStatusLabel(raw) {{
    var status = String(raw || "").trim().toLowerCase();
    if (status === "red") return "Red";
    if (status === "yellow") return "Yellow";
    if (status === "green") return "Green";
    if (status === "parked") return "Parked";
    if (status === "running") return "Running";
    if (status === "blocked") return "Blocked";
    if (status === "finished") return "Finished";
    if (status === "idle") return "Idle";
    if (status === "jeff-gate") return "After yes";
    return status ? status.charAt(0).toUpperCase() + status.slice(1) : "Unknown";
  }}
  function parseCheckedAt(value) {{
    var ms = Date.parse(String(value || ""));
    return isFinite(ms) ? ms : 0;
  }}
  function sinceLabel(iso) {{
    var ms = parseCheckedAt(iso);
    if (!ms) return "";
    var diff = Date.now() - ms;
    if (diff < 0) return "";
    var s = Math.floor(diff / 1000);
    if (s < 60) return s + "s ago";
    var m = Math.floor(s / 60);
    if (m < 60) return m + "m ago";
    var h = Math.floor(m / 60);
    return h + "h ago";
  }}
  function shortSha(raw) {{
    var sha = String(raw || "").trim().toLowerCase();
    return /^[0-9a-f]{7,40}$/.test(sha) ? sha.slice(0, 7) : "";
  }}
  function safeSnippetText(value) {{
    var text = String(value || "").replace(/\\s+/g, " ").trim();
    if (!text) return "";
    if (text.length > 220) text = text.slice(0, 220);
    var low = text.toLowerCase();
    if (low.indexOf("/users/") !== -1 || low.indexOf("/home/") !== -1 || low.indexOf("c:\\\\") !== -1) return "";
    if (low.indexOf("token") !== -1 || low.indexOf("apikey") !== -1 || low.indexOf("secret") !== -1) return "";
    return text;
  }}
  function repoFromAny(raw) {{
    var text = String(raw || "").trim();
    if (!text) return "";
    if (/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(text)) return text.toLowerCase();
    var safe = safeRepoUrl(text);
    if (!safe) return "";
    var m = safe.match(/^https:\/\/github\.com\/([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)$/i);
    return m ? m[1].toLowerCase() : "";
  }}
  function matchProjectForWork(row, data) {{
    var repo = repoFromAny(row && row.repo);
    var pr = safePrUrl(row && row.pr_url || "");
    if (!repo && !pr) return null;
    var best = null;
    (data && data.sections || []).forEach(function (sec) {{
      (sec && sec.projects || []).forEach(function (p) {{
        if (!p || typeof p !== "object") return;
        var prep = repoFromAny(p.repo || p.repo_url || p.url || "");
        if (repo && prep && prep === repo) {{
          if (pr && safePrUrl(p.open_pr_url || "") === pr) best = p;
          else if (!best) best = p;
        }}
      }});
    }});
    return best;
  }}
  function noteText(item) {{
    if (!item || typeof item !== "object") return "";
    if (item.notes) return String(item.notes).trim();
    if (item.goal) return String(item.goal).trim();
    return "";
  }}
  function statusSections(data) {{
    var out = [];
    (data && data.sections || []).forEach(function (sec) {{
      if (!sec || typeof sec !== "object") return;
      var sid = String(sec.id || "");
      (sec.projects || []).forEach(function (p) {{
        if (!p || typeof p !== "object") return;
        var key = focusKey("project", p.name);
        if (!key) return;
        out.push({{
          token: key,
          section: sid,
          item: p
        }});
      }});
    }});
    return out;
  }}
  function eventRow(text, href) {{
    var msg = String(text || "").trim();
    if (!msg) return null;
    var safe = workHref(href || "");
    return safe ? {{ text: msg, href: safe }} : {{ text: msg, href: "" }};
  }}
  function projectEvents(p) {{
    if (!p || typeof p !== "object") return [];
    var out = [];
    var ci = p.ci && typeof p.ci === "object" ? p.ci : {{}};
    var ciState = String(ci.conclusion || "").trim().toLowerCase();
    if (ciState) out.push(eventRow("CI: " + ciState, ci.html_url || p.ci_url));
    var prs = (typeof p.open_prs === "number" && isFinite(p.open_prs)) ? p.open_prs : 0;
    if (prs > 0) {{
      var label = prs === 1 ? "1 open PR" : prs + " open PRs";
      out.push(eventRow("Review: " + label, prs === 1 ? p.open_pr_url : pullsUrlFromRepo(p.repo_url || p.url || "")));
    }}
    var lease = coordSignal(p);
    if (lease) out.push(eventRow("Coordination: " + lease, ""));
    var rel = String(p.release || "").trim();
    if (rel) {{
      var relHref = latestReleaseUrlFromRepo(p.repo_url || p.url || "") || safeReleaseUrl(p.release_url || "");
      out.push(eventRow("Latest release: " + rel, relHref));
    }}
    if (p.agent_url) out.push(eventRow("Agent attached", p.agent_url));
    return out.filter(Boolean).slice(0, 4);
  }}
  function workEvents(row, data) {{
    if (!row || typeof row !== "object") return [];
    var out = [];
    if (row.repo) {{
      var repoHref = safeRepoUrl("https://github.com/" + row.repo);
      out.push(eventRow("Repo: " + row.repo, repoHref));
    }}
    if (row.pr_url) out.push(eventRow("Tracking PR", row.pr_url));
    if (row.branch) out.push(eventRow("Branch: " + row.branch, ""));
    var probes = (data && data.agents) || [];
    for (var i = 0; i < probes.length; i += 1) {{
      var probe = probes[i];
      if (!probe || String(probe.id || "") !== String(row.id || "")) continue;
      var state = detailStatusLabel(probe.state || "unknown");
      out.push(eventRow("Probe state: " + state, ""));
      break;
    }}
    var clouds = (data && data.cloud_agents) || [];
    for (var j = 0; j < clouds.length; j += 1) {{
      var cloud = clouds[j];
      if (!cloud || !safeAgentUrl(cloud.url)) continue;
      if (row.pr_url && safePrUrl(cloud.pr_url || "") === safePrUrl(row.pr_url || "")) {{
        out.push(eventRow("Cloud handoff: " + String(cloud.name || "Agent"), cloud.url));
        break;
      }}
    }}
    return out.filter(Boolean).slice(0, 4);
  }}
  function workHistory(row, project, cloud) {{
    var out = [];
    function add(text) {{
      var clean = safeSnippetText(text);
      if (!clean) return;
      out.push({{ text: clean, href: "" }});
    }}
    add(row && row.why);
    add(row && row.note);
    add(row && row.last_task);
    add(project && project.coord && project.coord.next);
    add(cloud && cloud.detail);
    return out.slice(0, 5);
  }}
  function detailLinks(item, kind) {{
    var links = [];
    if (kind === "project") {{
      var hrefs = laneHrefs(item);
      if (hrefs.pr) links.push({{ label: "Open PR", href: hrefs.pr }});
      if (hrefs.ci) links.push({{ label: "Open CI", href: hrefs.ci }});
      if (hrefs.agent) links.push({{ label: "Open agent", href: hrefs.agent }});
      if (hrefs.repo) links.push({{ label: "Open repo", href: hrefs.repo }});
      if (hrefs.game) links.push({{ label: "Play game", href: hrefs.game }});
    }} else {{
      var agent = safeAgentUrl(item.agent_url || "");
      var pr = safePrUrl(item.pr_url || "");
      var repo = item.repo ? safeRepoUrl("https://github.com/" + item.repo) : "";
      if (agent) links.push({{ label: "Open agent", href: agent }});
      if (pr) links.push({{ label: "Open PR", href: pr }});
      if (repo) links.push({{ label: "Open repo", href: repo }});
    }}
    return links;
  }}
  function addFact(meta, label, value, href) {{
    var text = String(value || "").trim();
    if (!text) return;
    var row = {{ label: String(label || "").trim(), value: text, href: workHref(href || "") }};
    if (!row.label) return;
    meta.facts.push(row);
  }}
  function detailLookup(token, data) {{
    if (!token) return null;
    if (token.indexOf("work:") === 0) {{
      var workId = token.slice(5);
      var rows = normalizeLlmWork((data && data.llm_work) || []);
      for (var i = 0; i < rows.length; i += 1) {{
        if (String(rows[i].id || "") === workId) {{
          var row = rows[i];
          var project = matchProjectForWork(row, data);
          var cloud = null;
          var clouds = (data && data.cloud_agents) || [];
          for (var c = 0; c < clouds.length; c += 1) {{
            var candidate = clouds[c];
            if (!candidate) continue;
            var cUrl = safeAgentUrl(candidate.url || "");
            if (!cUrl) continue;
            if (row.agent_url && cUrl === safeAgentUrl(row.agent_url || "")) {{ cloud = candidate; break; }}
            if (row.pr_url && safePrUrl(candidate.pr_url || "") === safePrUrl(row.pr_url || "")) {{ cloud = candidate; }}
          }}
          var meta = {{
            token: token,
            kind: "work",
            title: row.name || laneName(workId),
            status: detailStatusLabel(row.status || "idle"),
            generated: (data && data.generated_at_display) || "",
            task: String(row.task_full || row.task_title || "").trim(),
            goal: String(row.goal_full || row.goal || "").trim(),
            note: safeSnippetText(row.note_full || row.note || row.why || row.last_task_full || row.last_task) || "",
            links: detailLinks(row, "work"),
            events: workEvents(row, data),
            history: workHistory(row, project, cloud),
            facts: []
          }};
          if (project) {{
            var ci = project.ci && typeof project.ci === "object" ? project.ci : {{}};
            var ciConcl = String(ci.conclusion || "").trim().toLowerCase();
            if (ciConcl && ciConcl !== "skipped" && ciConcl !== "cancelled") {{
              var ciHref = safeActionsUrl(ci.html_url || "");
              meta.links.push({{ label: "Open CI", href: ciHref }});
              var ciLabel = (safePrUrl(project.open_pr_url || "") && safePrUrl(project.open_pr_url || "") === safePrUrl(row.pr_url || ""))
                ? "CI (linked PR)" : "CI (repo)";
              addFact(meta, ciLabel, ciConcl + (ci.sha ? " (" + shortSha(ci.sha) + ")" : ""), ciHref);
            }}
            addFact(meta, "Tip SHA", shortSha(project.tip_sha), "");
            var coord = project.coord && typeof project.coord === "object" ? project.coord : {{}};
            if (coord.lease_state) addFact(meta, "Lease", String(coord.lease_state), "");
            if (coord.next) addFact(meta, "Next action", safeSnippetText(coord.next), "");
          }}
          addFact(meta, "Repo", row.repo || "", row.repo ? "https://github.com/" + row.repo : "");
          addFact(meta, "Branch", row.branch || "", "");
          if (row.pr_number || row.pr_url) addFact(meta, "PR", row.pr_number ? ("#" + row.pr_number) : "Open PR", row.pr_url || "");
          var agentUrl = safeAgentUrl(row.agent_url || (cloud && cloud.url) || "");
          var agentId = agentUrl ? agentUrl.split("/").pop() : "";
          if (agentUrl && meta.links.filter(function (entry) {{ return entry && entry.label === "Open agent"; }}).length === 0) {{
            meta.links.push({{ label: "Open agent", href: agentUrl }});
          }}
          addFact(meta, "Cloud agent", agentId, agentUrl);
          var checked = (cloud && cloud.checked_at) || "";
          if (checked) {{
            var age = sinceLabel(checked);
            addFact(meta, "Last update", age ? (checked + " (" + age + ")") : checked, "");
          }} else if (meta.generated) {{
            addFact(meta, "Last update", meta.generated, "");
          }}
          if (!meta.note && row.last_task) meta.note = safeSnippetText(row.last_task);
          return meta;
        }}
      }}
      return null;
    }}
    var sections = statusSections(data);
    for (var j = 0; j < sections.length; j += 1) {{
      if (sections[j].token !== token) continue;
      var project = sections[j].item || {{}};
      return {{
        token: token,
        kind: "project",
        title: String(project.name || "Project"),
        status: detailStatusLabel(project.chip || project.status || ""),
        task: "",
        goal: "",
        note: noteText(project),
        links: detailLinks(project, "project"),
        events: projectEvents(project),
        history: [],
        facts: [],
        generated: (data && data.generated_at_display) || ""
      }};
    }}
    return null;
  }}
  function paintDetail(meta) {{
    if (!meta || !detailSheet) return;
    detailTitleEl.textContent = meta.title || "Details";
    detailMetaEl.textContent = "Status: " + (meta.status || "Unknown");
    detailTimeEl.textContent = meta.generated ? ("Snapshot: " + meta.generated) : "";
    detailTaskEl.textContent = meta.task ? ("Task: " + meta.task) : "";
    detailGoalEl.textContent = meta.goal ? ("Goal: " + meta.goal) : "";
    detailFactsEl.innerHTML = "";
    (meta.facts || []).forEach(function (fact) {{
      if (!fact || !fact.label || !fact.value) return;
      var dt = document.createElement("dt");
      dt.textContent = fact.label;
      var dd = document.createElement("dd");
      if (fact.href) {{
        var a = document.createElement("a");
        a.href = fact.href;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.setAttribute("data-open", "work");
        a.textContent = fact.value;
        dd.appendChild(a);
      }} else {{
        dd.textContent = fact.value;
      }}
      detailFactsEl.appendChild(dt);
      detailFactsEl.appendChild(dd);
    }});
    detailNoteEl.textContent = meta.note || "No additional notes.";
    detailLinksEl.innerHTML = "";
    (meta.links || []).forEach(function (entry) {{
      var href = workHref(entry && entry.href || "");
      if (!href) return;
      var a = document.createElement("a");
      a.href = href;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.setAttribute("data-open", "work");
      a.textContent = String(entry.label || "Open");
      detailLinksEl.appendChild(a);
    }});
    detailEventsEl.innerHTML = "";
    var events = (meta.events || []).concat(meta.history || []);
    detailEventsTitleEl.hidden = events.length === 0;
    events.forEach(function (entry) {{
      if (!entry || !entry.text) return;
      var li = document.createElement("li");
      if (entry.href) {{
        var a = document.createElement("a");
        a.href = entry.href;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.setAttribute("data-open", "work");
        a.textContent = entry.text;
        li.appendChild(a);
      }} else {{
        li.textContent = entry.text;
      }}
      detailEventsEl.appendChild(li);
    }});
  }}
  function setDetailVisibility(open) {{
    if (!detailSheet || !detailScrim) return;
    detailOpen = !!open;
    if (detailOpen) {{
      detailScrim.hidden = false;
      detailSheet.hidden = false;
      detailScrim.classList.add("show");
      detailSheet.classList.add("show");
      try {{ document.body.classList.add("detail-open"); }} catch (e) {{}}
      return;
    }}
    detailScrim.classList.remove("show");
    detailSheet.classList.remove("show");
    detailScrim.hidden = true;
    detailSheet.hidden = true;
    try {{ document.body.classList.remove("detail-open"); }} catch (e2) {{}}
  }}
  function closeDetail(fromHistory) {{
    if (!detailOpen) return false;
    if (fromHistory && detailHistoryDepth > 0) {{
      history.back();
      return true;
    }}
    detailToken = "";
    setDetailVisibility(false);
    return true;
  }}
  function openDetail(token, pushHistory) {{
    var data = lastStatusData || bootstrapStatus();
    var meta = detailLookup(token, data);
    if (!meta) return false;
    detailToken = token;
    paintDetail(meta);
    setDetailVisibility(true);
    if (pushHistory) {{
      try {{
        history.pushState({{ detailSheet: token }}, "", location.href);
        detailHistoryDepth += 1;
      }} catch (e) {{}}
    }}
    return true;
  }}
  function refreshOpenDetail() {{
    if (!detailOpen || !detailToken) return;
    var meta = detailLookup(detailToken, lastStatusData || bootstrapStatus());
    if (!meta) return;
    paintDetail(meta);
  }}
  function tapLink(href, label) {{
    if (!href) return "";
    return '<a class="dec" data-open="work" href="' + esc(href) +
      '" target="_blank" rel="noopener noreferrer">' + esc(label) + "</a>";
  }}
  var PAINT_CONTROL_ACTIONS = {{ "refresh-hint": 1, "open-repo": 1, "mark-reviewed": 1 }};
  function controlBtnHtml(p) {{
    var act = String((p && p.control_action) || "");
    if (!PAINT_CONTROL_ACTIONS[act]) return "";
    var label = esc(p.action_label || p.name || "Do");
    return '<button type="button" data-action="' + esc(act) + '">' + label + "</button>";
  }}

  function chipHtml(st, label) {{
    var c = CHIP_COLORS[st] || CHIP_COLORS.parked;
    return '<span class="chip" style="--c:' + c[0] + ';--bg:' + c[1] + ';--fg:' + c[2] + '">' + esc(label) + '</span>';
  }}

  var SECTION_TYPE_CHIPS = {{ Ability: 1, Control: 1, Feature: 1 }};
  function visibleChipLabel(p) {{
    var label = String((p && p.chip) || "").trim();
    if (!label || SECTION_TYPE_CHIPS[label]) return "";
    return label;
  }}
  function coordSignal(p) {{
    if (!p || p.private) return "";
    var coord = p.coord;
    if (!coord || typeof coord !== "object") return "";
    if (String(coord.lease_state || "").trim().toLowerCase() !== "active") return "";
    var agent = String(coord.agent || "").trim().toLowerCase();
    if (agent === "codex") return "Codex lease";
    if (agent === "grok") return "Grok lease";
    if (agent === "claude") return "Claude lease";
    return "";
  }}
  function coordReviewSignal(p) {{
    if (!coordPrUrl(p)) return "";
    return "PR #" + p.coord.pr;
  }}
  function compactSignal(p) {{
    if (!p) return "";
    if (p.private) return "";
    var ci = p.ci;
    var concl = (ci && typeof ci === "object") ? String(ci.conclusion || "").toLowerCase() : "";
    if (concl === "failure" || concl === "timed_out" || concl === "action_required" || concl === "startup_failure") {{
      return "CI fail";
    }}
    if (concl === "in_progress" || concl === "waiting") {{
      return "CI running";
    }}
    if (concl === "queued" || concl === "pending" || concl === "requested") {{
      return "CI pending";
    }}
    var lease = coordSignal(p);
    if (lease) return lease;
    var review = coordReviewSignal(p);
    if (review) return review;
    var stack = p.open_pr_stack;
    if (Array.isArray(stack) && stack.length >= 2 && p.open_prs === stack.length) {{
      var numbers = [];
      var seen = {{}};
      var valid = true;
      for (var i = 0; i < stack.length; i++) {{
        var row = stack[i];
        var number = row && row.number;
        var url = row && String(row.url || "");
        if (typeof number !== "number" || !isFinite(number) || number <= 0 || Math.floor(number) !== number || seen[number]) {{
          valid = false;
          break;
        }}
        var match = url.match(/^https:\/\/github\.com\/rupret007\/[A-Za-z0-9._-]+\/pull\/([1-9][0-9]*)$/i);
        if (!match || Number(match[1]) !== number) {{
          valid = false;
          break;
        }}
        seen[number] = true;
        numbers.push(number);
      }}
      if (valid) {{
        if (numbers.length <= 4) return "Stack " + numbers.map(function (n) {{ return "#" + n; }}).join(" -> ");
        return numbers.length + "-PR stack";
      }}
    }}
    if (typeof p.open_prs === "number" && isFinite(p.open_prs) && p.open_prs > 0) {{
      return p.open_prs + (p.open_prs === 1 ? " open PR" : " open PRs");
    }}
    var rel = String(p.release || "").trim();
    if (rel) {{
      if (releaseMatchesTip(p) === false) return "Latest != source";
      return rel;
    }}
    if (concl && concl !== "success" && concl !== "skipped" && concl !== "cancelled") return concl;
    return "";
  }}
  function shortNote(notes, limit) {{
    var text = String(notes || "").replace(/\\s+/g, " ").trim();
    var n = limit || 88;
    if (text.length <= n) return text;
    var cut = text.slice(0, n - 1);
    var sp = cut.lastIndexOf(" ");
    if (sp > 24) cut = cut.slice(0, sp);
    return cut.replace(/[.,;:]$/, "") + "...";
  }}
  function attentionRank(p) {{
    var map = {{ red: 0, "jeff-gate": 1, yellow: 2, green: 3, parked: 4 }};
    var st = p && p.status;
    return map.hasOwnProperty(st) ? map[st] : 5;
  }}
  function isQuietLane(p) {{
    return !!(p && p.status === "green");
  }}
  function sectionKind(id) {{
    if (id === "controls") return "pending";
    if (id === "active-agents") return "pulse";
    if (id === "live-shipping") return "primary";
    if (id === "abilities" || id === "features") return "footer";
    return "secondary";
  }}
  var TYPE_TAB_IDS = ["controls", "live-shipping", "apps-utilities", "cisco", "messaging", "private-media", "parked"];
  var TYPE_TAB_LABELS = {{
    "controls": "Decisions",
    "live-shipping": "Live",
    "apps-utilities": "Apps",
    "cisco": "Cisco",
    "messaging": "Bots",
    "private-media": "Media",
    "parked": "Parked"
  }};
  var currentTypeTab = "";
  function tabId(raw) {{
    var s = String(raw || "");
    return TYPE_TAB_LABELS.hasOwnProperty(s) ? s : "";
  }}
  function tabLabel(id) {{
    var sid = tabId(id);
    return sid ? TYPE_TAB_LABELS[sid] : "";
  }}
  function typeTabIdsFor(sections, pending) {{
    var present = {{}};
    (sections || []).forEach(function (sec) {{
      if (sec && sec.id) present[String(sec.id)] = 1;
    }});
    var out = [];
    TYPE_TAB_IDS.forEach(function (sid) {{
      if (sid === "controls") return;
      if (present[sid]) out.push(sid);
    }});
    return out;
  }}
  function glanceStatus(pending, sections) {{
    var rank = {{ high: 0, medium: 1, low: 2 }};
    var rows = [];
    (pending || []).forEach(function (it) {{
      if (it && typeof it === "object" && focusKey("decision", it.id)) rows.push(it);
    }});
    rows.sort(function (a, b) {{
      var ra = rank.hasOwnProperty(String(a.risk || "").toLowerCase()) ? rank[String(a.risk).toLowerCase()] : 5;
      var rb = rank.hasOwnProperty(String(b.risk || "").toLowerCase()) ? rank[String(b.risk).toLowerCase()] : 5;
      return ra - rb;
    }});
    if (rows.length) {{
      var title = rows[0] && rows[0].title != null ? String(rows[0].title).replace(/^\s+|\s+$/g, "") : "";
      if (title.length > 28) title = title.slice(0, 28).replace(/\s+$/g, "");
      if (!title) title = "Pending";
      var budget = 22;
      if (title.length > budget) {{
        var cut = title.slice(0, budget).replace(/\s+$/g, "");
        if (cut.indexOf(" ") !== -1) cut = cut.slice(0, cut.lastIndexOf(" ")).replace(/\s+$/g, "");
        title = cut || title.slice(0, budget).replace(/\s+$/g, "");
      }}
      return {{ text: "Decide: " + (title || "Pending"), tab: "controls", focus: focusKey("decision", rows[0].id) }};
    }}
    var worstRank = 99;
    var worstId = "";
    var worstName = "";
    var worstFocus = "";
    (sections || []).forEach(function (sec) {{
      var sid = tabId(sec && sec.id);
      if (!sid || sid === "controls") return;
      (sec.projects || []).forEach(function (p) {{
        var r = attentionRank(p);
        var target = focusKey("project", p && p.name);
        if ((r !== 0 && r !== 2) || !target) return;
        if (r < worstRank) {{
          worstRank = r;
          worstId = sid;
          worstName = String(p.name || "").replace(/^\s+|\s+$/g, "");
          if (worstName.length > 28) worstName = worstName.slice(0, 28).replace(/\s+$/g, "");
          worstFocus = target;
        }}
      }});
    }});
    if (worstId) {{
      var label = worstName || tabLabel(worstId);
      if (worstRank === 0) return {{ text: label + " is red", tab: worstId, focus: worstFocus }};
      if (worstRank === 2) return {{ text: label + " needs a look", tab: worstId, focus: worstFocus }};
    }}
    return {{ text: "Quiet", tab: "" }};
  }}
  function glanceHtml(pending, sections) {{
    var g = glanceStatus(pending, sections);
    var extra = g.tab ? ' data-tab="' + esc(g.tab) + '"' : "";
    var target = g.focus ? ' data-focus-target="' + esc(g.focus) + '"' : "";
    var controls = g.tab ? ' aria-controls="' + esc(g.tab) + '"' : "";
    return '<button type="button" class="board-glance" id="board-glance" aria-label="Next action"' + extra + target + controls + ">" +
      esc(g.text || "Quiet") + "</button>";
  }}
  function typeTabsHtml(sections, pending, selected) {{
    var want = tabId(selected);
    var buttons = "";
    typeTabIdsFor(sections, pending).forEach(function (sid) {{
      buttons += '<button type="button" role="tab" id="tab-' + esc(sid) + '" data-tab="' + esc(sid) +
        '" aria-controls="' + esc(sid) + '" aria-selected="' + (sid === want ? "true" : "false") + '">' +
        esc(tabLabel(sid)) + "</button>";
    }});
    return '<nav class="type-tabs" id="type-tabs" role="tablist" aria-label="Project type">' +
      buttons + "</nav>";
  }}
  function applyTypeTab(id) {{
    var want = tabId(id);
    currentTypeTab = want;
    var tabs = document.querySelectorAll("#type-tabs [data-tab]");
    Array.prototype.forEach.call(tabs, function (btn) {{
      btn.setAttribute("aria-selected", tabId(btn.getAttribute("data-tab")) === want ? "true" : "false");
    }});
    var panels = document.querySelectorAll("#board [data-tab-panel]");
    Array.prototype.forEach.call(panels, function (panel) {{
      var pid = tabId(panel.getAttribute("data-tab-panel"));
      if (!pid) return;
      if (pid === want) panel.removeAttribute("hidden");
      else panel.setAttribute("hidden", "");
    }});
    try {{
      document.body.classList.toggle("tab-home", !want);
    }} catch (e2) {{}}
    try {{
      if (want) history.replaceState(null, "", "#" + want);
      else if ((location.hash || "").length > 1) history.replaceState(null, "", location.pathname + location.search);
    }} catch (e) {{}}
    if (detailOpen) closeDetail(false);
  }}
  var glanceTargetTimer = null;
  function validFocusKey(raw) {{
    var key = String(raw || "");
    var split = key.indexOf(":");
    if (split < 1) return false;
    var kind = key.slice(0, split);
    var value = key.slice(split + 1);
    return focusKey(kind, value) === key;
  }}
  function findFocusTarget(panel, raw) {{
    var key = String(raw || "");
    if (!panel || !validFocusKey(key)) return null;
    var targets = panel.querySelectorAll("[data-focus-key]");
    var found = null;
    for (var i = 0; i < targets.length; i += 1) {{
      if (targets[i].getAttribute("data-focus-key") !== key) continue;
      if (found) return null;
      found = targets[i];
    }}
    return found;
  }}
  function revealGlanceTarget(id, raw) {{
    var sid = tabId(id);
    var panel = sid ? document.getElementById(sid) : null;
    if (!panel || tabId(panel.getAttribute("data-tab-panel")) !== sid) return false;
    var target = findFocusTarget(panel, raw);
    if (!target) return false;

    var ancestor = target.parentElement;
    while (ancestor && ancestor !== panel) {{
      if (String(ancestor.tagName || "").toLowerCase() === "details") ancestor.open = true;
      ancestor = ancestor.parentElement;
    }}

    var previous = document.querySelectorAll(".is-glance-target");
    Array.prototype.forEach.call(previous, function (row) {{
      row.classList.remove("is-glance-target");
    }});
    if (glanceTargetTimer) clearTimeout(glanceTargetTimer);
    target.classList.add("is-glance-target");
    try {{ target.focus({{ preventScroll: true }}); }} catch (e) {{
      try {{ target.focus(); }} catch (e2) {{}}
    }}
    var smooth = false;
    try {{ smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches; }} catch (e3) {{}}
    try {{ target.scrollIntoView({{ behavior: smooth ? "smooth" : "auto", block: "center" }}); }} catch (e4) {{}}
    glanceTargetTimer = setTimeout(function () {{
      target.classList.remove("is-glance-target");
      glanceTargetTimer = null;
    }}, 2200);
    return true;
  }}
  function tabFromHash() {{
    return tabId(String(location.hash || "").replace(/^#/, ""));
  }}
  function isTypeTab(id) {{
    return !!tabId(id);
  }}
  document.addEventListener("click", function (ev) {{
    var btn = ev.target.closest("[data-tab]");
    if (!btn) return;
    if (btn.getAttribute("data-dec")) return;
    var id = tabId(btn.getAttribute("data-tab"));
    if (!id) return;
    ev.preventDefault();
    var fromGlance = btn.id === "board-glance";
    if (fromGlance) {{
      applyTypeTab(id);
      var focus = btn.getAttribute("data-focus-target") || "";
      var reveal = function () {{ revealGlanceTarget(id, focus); }};
      if (window.requestAnimationFrame) window.requestAnimationFrame(reveal);
      else setTimeout(reveal, 0);
      return;
    }}
    applyTypeTab(currentTypeTab === id ? "" : id);
  }});
  window.addEventListener("hashchange", function () {{
    applyTypeTab(tabFromHash());
  }});
  window.addEventListener("popstate", function () {{
    if (detailOpen) {{
      if (detailHistoryDepth > 0) detailHistoryDepth -= 1;
      closeDetail(false);
      return;
    }}
    applyTypeTab(tabFromHash());
  }});
  function pendingShell(items) {{
    var rank = {{ high: 0, medium: 1, low: 2 }};
    var rows = (items || []).filter(function (it) {{
      return it && /^[a-zA-Z0-9._-]+$/.test(String(it.id || ""));
    }}).slice().sort(function (a, b) {{
      var ra = rank.hasOwnProperty(String(a.risk || "").toLowerCase()) ? rank[String(a.risk).toLowerCase()] : 5;
      var rb = rank.hasOwnProperty(String(b.risk || "").toLowerCase()) ? rank[String(b.risk).toLowerCase()] : 5;
      return ra - rb;
    }});
    var attn = "";
    var low = "";
    var lowCount = 0;
    rows.forEach(function (it) {{
      var rawRisk = String(it.risk || "").toLowerCase();
      var risk = (rawRisk === "high" || rawRisk === "medium") ? rawRisk : "low";
      function decA(verb, extra) {{
        var href = (window.decisionHref && window.decisionHref(verb, it.id, it.title || it.id)) || "";
        if (!href) return "";
        var label = verb === "APPROVE" ? "Approve" : verb === "HOLD" ? "Hold" : "Deny";
        return '<a class="dec' + (extra ? " " + extra : "") + '" data-dec="' + esc(verb) +
          '" href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' + label + "</a>";
      }}
      var focus = focusKey("decision", it.id);
      var focusAttr = focus ? ' data-focus-key="' + esc(focus) + '" tabindex="-1"' : "";
      var row = '<div class="pending-item" data-id="' + esc(it.id) + '" data-title="' + esc(it.title || it.id) + '"' + focusAttr + '>' +
        '<div class="pending-head"><div class="ptitle">' + esc(it.title || it.id) + "</div>" +
        '<span class="prisk ' + esc(risk) + '">' + esc(risk) + "</span></div>" +
        '<div class="pdetail">' + esc(shortNote(it.detail || "", 72)) + "</div>" +
        '<div class="prow">' + decA("APPROVE", "") + decA("HOLD", "warn") + decA("DENY", "danger") + "</div></div>";
      if (rawRisk === "low") {{ low += row; lowCount += 1; }}
      else attn += row;
    }});
    if (lowCount) {{
      low = '<details class="pending-more"><summary>' +
        esc(String(lowCount) + (lowCount === 1 ? " lower-risk item" : " lower-risk items")) +
        "</summary>" + low + "</details>";
    }}
    return '<div id="pending-box" class="pending-box"' + (rows.length ? "" : " hidden") + ">" +
      '<p class="pending-help">Public board -- Approve opens a GitHub issue as <code>rupret007</code>.</p>' +
      '<div id="pending-list">' + attn + low + "</div></div>";
  }}
  function toolsRow(projects) {{
    var btns = "";
    (projects || []).forEach(function (p) {{ btns += controlBtnHtml(p); }});
    return btns ? '<div class="tools">' + btns + "</div>" : "";
  }}
  function laneHtml(p) {{
    var chipLabel = visibleChipLabel(p);
    var chip = chipLabel ? chipHtml(p.status || "parked", chipLabel) : "";
    var title = p.name || "project";
    var hrefs = laneHrefs(p);
    var titleHtml = hrefs.title
      ? '<a data-open="work" href="' + esc(hrefs.title) + '" target="_blank" rel="noopener noreferrer">' + esc(title) + "</a>"
      : esc(title);
    var signal = compactSignal(p);
    var href = signalHref(p);
    var signalHtml = "";
    if (signal && href) {{
      signalHtml = '<a class="signal" data-open="work" href="' + esc(href) +
        '" target="_blank" rel="noopener noreferrer">' + esc(signal) + "</a>";
    }} else if (signal) {{
      signalHtml = '<span class="signal">' + esc(signal) + "</span>";
    }}
    var note = shortNote(p.notes || "", 88);
    var notesHtml = note ? '<p class="notes">' + esc(note) + "</p>" : "";
    var quiet = isQuietLane(p) ? " is-quiet" : "";
    var links = "";
    if (hrefs.agent) links += tapLink(hrefs.agent, "Open agent");
    if (hrefs.pr) links += tapLink(hrefs.pr, "Open PR");
    if (hrefs.repo) links += tapLink(hrefs.repo, "Open repo");
    if (hrefs.ci) links += tapLink(hrefs.ci, "Open CI");
    if (hrefs.game) links += tapLink(hrefs.game, "Play game");
    var linksHtml = links ? '<div class="lane-links">' + links + "</div>" : "";
    var focus = focusKey("project", p.name);
    var focusAttr = focus ? ' data-focus-key="' + esc(focus) + '" tabindex="-1"' : "";
    var detailAttr = focus ? ' data-detail-kind="project" data-detail-key="' + esc(focus) + '"' : "";
    return '<article class="lane' + quiet + '"' + focusAttr + detailAttr + '><h3>' + titleHtml + '</h3><div class="lane-end">' +
      chip + signalHtml + "</div>" + linksHtml + notesHtml + "</article>";
  }}
  function lanesHtml(projects, sortAttn) {{
    var rows = (projects || []).filter(function (p) {{ return p && typeof p === "object"; }});
    if (sortAttn) rows = rows.slice().sort(function (a, b) {{ return attentionRank(a) - attentionRank(b); }});
    var html = "";
    rows.forEach(function (p) {{ html += laneHtml(p); }});
    return '<div class="lanes">' + html + "</div>";
  }}

  function snapshotTrustState(ageMs, failures, silenceLimitMs) {{
    var age = Number(ageMs);
    var failCount = Number(failures);
    var limit = Number(silenceLimitMs);
    if (isFinite(failCount) && failCount > 0) return "poll-failed";
    if (isFinite(age) && isFinite(limit) && age > limit) return "refresh-overdue";
    return "current";
  }}

  function setSnapshotTrust(state) {{
    var unverified = state !== "current";
    if (document.body) document.body.classList.toggle("snapshot-unverified", unverified);
    if (boardEl) {{
      boardEl.setAttribute("data-snapshot-trust", unverified ? "last-verified" : "current");
      if (unverified) boardEl.setAttribute("aria-describedby", "silence-detail");
      else boardEl.removeAttribute("aria-describedby");
    }}
    if (silenceEl) silenceEl.setAttribute("data-state", state);
  }}

  function showSilence(state, title, detail) {{
    if (!silenceEl) return;
    if (silenceTitleEl) silenceTitleEl.textContent = title;
    if (silenceDetailEl) silenceDetailEl.textContent = detail;
    silenceEl.classList.add("show");
    silenceEl.hidden = false;
    setSnapshotTrust(state);
  }}

  function hideSilence() {{
    if (!silenceEl) return;
    if (silenceTitleEl) silenceTitleEl.textContent = "";
    if (silenceDetailEl) silenceDetailEl.textContent = "";
    silenceEl.classList.remove("show");
    silenceEl.hidden = true;
    setSnapshotTrust("current");
  }}

  function fmtSilenceAge(ms) {{
    var m = Math.round(ms / 60000);
    if (m < 120) return "~" + m + " min";
    return "~" + (m / 60).toFixed(1) + " h";
  }}

  function updateSilence() {{
    var age = Date.now() - knownMs;
    var state = snapshotTrustState(age, pollFailStreak, SILENCE_LIMIT_MS);
    var when = (displayEl && displayEl.textContent) || known || "unknown time";
    if (state === "poll-failed") {{
      freshness.textContent = "Last verified " + fmtSilenceAge(age) + " ago";
      showSilence(
        state,
        "Live check unavailable",
        "Showing the last verified snapshot from " + when + ". It may be outdated; retry now or open a project before acting."
      );
      return;
    }}
    if (state === "refresh-overdue") {{
      freshness.textContent = "Last verified " + fmtSilenceAge(age) + " ago";
      showSilence(
        state,
        "Dashboard refresh overdue",
        "Showing the last verified snapshot from " + when + ". Project states may have changed; retry now before acting."
      );
      return;
    }}
    hideSilence();
  }}

  function workChip(status) {{
    var map = {{
      running: ["green", "Running"],
      finished: ["parked", "Finished"],
      blocked: ["red", "Blocked"],
      idle: ["parked", "Idle"]
    }};
    var m = map[String(status || "idle").toLowerCase()] || map.idle;
    return chipHtml(m[0], m[1]);
  }}
  function laneName(id) {{
    var map = {{
      codex: "Codex (ChatGPT)",
      claude: "Claude",
      gemini: "Gemini",
      minimax: "MiniMax",
      grok: "Grok",
      "cursor-cloud": "Cursor Cloud"
    }};
    return map[id] || id;
  }}
  function laneId(rawId, rawName) {{
    var id = String(rawId || "").trim().toLowerCase().replace(/_/g, "-");
    if (id === "codex" || id === "claude" || id === "gemini" || id === "minimax" || id === "grok" || id === "cursor-cloud") return id;
    if (id === "cursor" || id === "cloud") return "cursor-cloud";
    var name = String(rawName || "").toLowerCase();
    if (name.indexOf("cursor cloud") !== -1 || name === "cloud") return "cursor-cloud";
    if (name.indexOf("codex") !== -1 || name.indexOf("chatgpt") !== -1) return "codex";
    if (name.indexOf("claude") !== -1) return "claude";
    if (name.indexOf("gemini") !== -1) return "gemini";
    if (name.indexOf("minimax") !== -1) return "minimax";
    if (name.indexOf("grok") !== -1) return "grok";
    return "";
  }}
  function compactText(value, limit) {{
    var text = String(value || "").replace(/\s+/g, " ").trim();
    if (!limit || text.length <= limit) return text;
    return text.slice(0, limit);
  }}
  function safeBranch(value) {{
    var branch = String(value || "").trim();
    if (!branch) return "";
    if (!/^[A-Za-z0-9][A-Za-z0-9._/-]{{0,254}}$/.test(branch)) return "";
    if (branch.indexOf("@{{") !== -1 || branch.indexOf("..") !== -1 || branch.indexOf("//") !== -1) return "";
    if (/[/.]$/.test(branch)) return "";
    return branch;
  }}
  function repoFromPrUrl(url) {{
    var safe = safePrUrl(url);
    if (!safe) return "";
    var m = safe.match(/^https:\/\/github\.com\/([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)\/pull\/[1-9][0-9]*$/i);
    return m ? m[1] : "";
  }}
  function prNumber(url) {{
    var safe = safePrUrl(url);
    if (!safe) return "";
    var m = safe.match(/\/pull\/([1-9][0-9]*)$/);
    return m ? m[1] : "";
  }}
  function normalizeWorkRow(raw) {{
    if (!raw || typeof raw !== "object") return null;
    var id = laneId(raw.id, raw.name);
    if (!id) return null;
    var pr = safePrUrl(raw.pr_url || raw.pr);
    var status = String(raw.status || "").toLowerCase();
    if (status !== "running" && status !== "finished" && status !== "blocked" && status !== "idle") status = "idle";
    return {{
      id: id,
      name: laneName(id),
      task_title: compactText(raw.task_title || raw.title, 120),
      task_full: compactText(raw.task_title || raw.title, 600),
      repo: compactText(raw.repo || repoFromPrUrl(pr), 96),
      pr_url: pr,
      pr_number: String(raw.pr_number || prNumber(pr)),
      branch: safeBranch(raw.branch),
      goal: compactText(raw.goal || raw.notes, 220),
      goal_full: compactText(raw.goal || raw.notes, 1200),
      status: status,
      agent_url: safeAgentUrl(raw.agent_url || raw.url),
      why: compactText(raw.why || raw.lane_why, 120),
      note: compactText(raw.note || raw.detail, 160),
      note_full: compactText(raw.note || raw.detail, 900),
      last_task: compactText(raw.last_task, 120),
      last_task_full: compactText(raw.last_task, 900),
      source: compactText(raw.source, 48)
    }};
  }}
  function normalizeLlmWork(rows) {{
    var order = ["codex", "claude", "gemini", "minimax", "grok", "cursor-cloud"];
    var by = {{}};
    (rows || []).forEach(function (raw) {{
      var row = normalizeWorkRow(raw);
      if (row) by[row.id] = row;
    }});
    return order.map(function (id) {{
      var row = by[id] || {{}};
      return {{
        id: id,
        name: laneName(id),
        task_title: row.task_title || "idle - needs assignment",
        task_full: row.task_full || row.task_title || "idle - needs assignment",
        repo: row.repo || "",
        pr_url: row.pr_url || "",
        pr_number: row.pr_number || "",
        branch: row.branch || "",
        goal: row.goal || "",
        goal_full: row.goal_full || row.goal || "",
        status: row.status || "idle",
        agent_url: row.agent_url || "",
        why: row.why || "",
        note: row.note || "",
        note_full: row.note_full || row.note || "",
        last_task: row.last_task || "",
        last_task_full: row.last_task_full || row.last_task || "",
        source: row.source || "idle"
      }};
    }});
  }}
  function workMetaHtml(row) {{
    var pr = safePrUrl(row.pr_url || "");
    var repoDisplay = row.repo || "";
    var repoUrl = repoDisplay ? safeRepoUrl("https://github.com/" + repoDisplay) : "";
    var prNumber = String(row.pr_number || "").trim();
    var branch = row.branch || "";
    var why = row.why || "";
    var bits = [];
    if (repoUrl) {{
      bits.push('<a href="' + esc(repoUrl) + '" class="meta-link" target="_blank" rel="noopener">' + esc(repoDisplay) + "</a>");
    }} else if (repoDisplay) {{
      bits.push(esc(repoDisplay));
    }}
    if (pr && prNumber) {{
      bits.push('<a href="' + esc(pr) + '" class="meta-link" target="_blank" rel="noopener">PR #' + esc(prNumber) + "</a>");
    }} else if (prNumber) {{
      bits.push("PR #" + esc(prNumber));
    }}
    if (branch) bits.push(esc(branch));
    if (why) bits.push(esc(why));
    return bits.join(" - ");
  }}
  function workRowHtml(row) {{
    var lane = row || {{}};
    var name = lane.name || laneName(lane.id || "");
    var task = lane.task_title || "idle - needs assignment";
    var metaHtml = workMetaHtml(lane);
    var links = "";
    if (lane.agent_url) links += tapLink(lane.agent_url, "Open agent");
    if (lane.pr_url) links += tapLink(lane.pr_url, "Open PR");
    if (lane.repo) {{
      var repo = safeRepoUrl("https://github.com/" + lane.repo);
      if (repo) links += tapLink(repo, "Open repo");
    }}
    var goal = lane.goal ? '<p class="goal">' + esc(lane.goal) + "</p>" : "";
    var note = lane.note ? '<p class="note">' + esc(lane.note) + "</p>" : "";
    var lastTask = lane.last_task ? '<p class="last-task">Last: ' + esc(lane.last_task) + "</p>" : "";
    var detailKey = lane.id ? "work:" + lane.id : "";
    var detailAttr = detailKey ? ' data-detail-kind="work" data-detail-key="' + esc(detailKey) + '"' : "";
    return '<article class="agent-row" data-lane-id="' + esc(lane.id || "") + '" data-lane-status="' + esc(lane.status || "idle") + '"' + detailAttr + '>' +
      '<div class="agent-head"><h3>' + esc(name) + '</h3>' + workChip(lane.status) + "</div>" +
      '<p class="task">' + esc(task) + "</p>" +
      '<p class="meta">' + metaHtml + "</p>" +
      goal + note + lastTask +
      (links ? '<div class="agent-links">' + links + "</div>" : "") +
      "</article>";
  }}
  function agentsStripHtml(workRows) {{
    var rows = normalizeLlmWork(workRows);
    var html = rows.map(workRowHtml).join("");
    return '<section class="agents-strip" id="agents-strip" aria-label="LLM work now"><h2 class="llm-work-title">Work now</h2>' + html + "</section>";
  }}
  function fetchedLineHtml(repos) {{
    var names = [];
    (repos || []).forEach(function (x) {{
      var s = String(x || "").trim();
      if (s) names.push(s);
    }});
    return '<p id="fetched-line">Live CI via <code>gh</code>: ' + esc(names.join(", ")) + ".</p>";
  }}
  function sanitizeCloudAgents(rows) {{
    var out = [];
    var seen = {{}};
    (rows || []).forEach(function (a) {{
      if (!a) return;
      var url = safeAgentUrl(a.url);
      if (!url || seen[url]) return;
      seen[url] = 1;
      out.push({{
        id: url.split("/").pop(),
        name: a.name || "Cloud",
        state: "unknown",
        detail: a.detail || "Cloud Agent",
        url: url,
        pr_url: safePrUrl(a.pr_url),
        checked_at: a.checked_at || ""
      }});
    }});
    return out.slice(0, 3);
  }}

  function boardFingerprint(data) {{
    if (!data || typeof data !== "object") return "";
    function agentKey(a) {{ return a ? [a.id, a.state, a.detail, a.url || "", a.pr_url || ""] : []; }}
    function pendingKey(it) {{ return it ? [it.id, it.title, it.risk, it.detail] : []; }}
    function projectKey(p) {{
      if (!p) return [];
      var ci = p.ci && typeof p.ci === "object" ? p.ci : {{}};
      var coord = p.coord && typeof p.coord === "object" ? p.coord : {{}};
      return [p.name, p.status, p.chip, p.notes, p.open_prs, p.open_pr_url || "", p.open_pr_stack || [], p.release, p.release_sha || "", p.tip_sha, p.agent_url || "", p.live_game_url || "", ci.conclusion || "", ci.sha || "", ci.name || "", ci.html_url || "", coord.agent || "", coord.lease_state || "", coord.pr || "", coord.pr_url || "", typeof coord.pr_draft === "boolean" ? coord.pr_draft : ""];
    }}
    var sections = (data.sections || []).map(function (sec) {{
      if (!sec) return [];
      return [sec.id, sec.title, (sec.projects || []).map(projectKey)];
    }});
    return JSON.stringify({{
      pending: (data.pending || []).map(pendingKey),
      agents: (data.agents || []).map(agentKey),
      cloud: (data.cloud_agents || []).map(agentKey),
      sections: sections,
      fetched: data.fetched_repos || []
    }});
  }}
  function snapshotOpen() {{
    function isOpen(sel) {{
      var el = document.querySelector(sel);
      return !!(el && el.open);
    }}
    return {{
      how: isOpen("details.how-board"),
      ab: isOpen("details.abilities-foot"),
      more: isOpen("details.pending-more"),
      tab: currentTypeTab || tabFromHash()
    }};
  }}
  function restoreOpen(s) {{
    function setOpen(sel, on) {{
      var el = document.querySelector(sel);
      if (el && on) el.open = true;
    }}
    if (!s) return;
    setOpen("details.how-board", s.how);
    setOpen("details.abilities-foot", s.ab);
    setOpen("details.pending-more", s.more);
    applyTypeTab(s.tab || "");
  }}
  function paintAgents(workRows) {{
    var host = document.getElementById("active-agents");
    if (!host) return;
    var html = agentsStripHtml(workRows || []);
    if (host.innerHTML === html) return;
    host.innerHTML = html;
  }}
  function readDomLlmWork() {{
    var rows = document.querySelectorAll("#agents-strip .agent-row");
    return Array.prototype.map.call(rows, function (el) {{
      return {{
        id: el.getAttribute("data-lane-id") || "",
        status: el.getAttribute("data-lane-status") || "idle",
        name: ((el.querySelector("h3") || {{}}).textContent) || "",
        task_title: ((el.querySelector(".task") || {{}}).textContent) || "",
        goal: ((el.querySelector(".goal") || {{}}).textContent) || "",
        note: ((el.querySelector(".note") || {{}}).textContent) || "",
        last_task: (((el.querySelector(".last-task") || {{}}).textContent) || "").replace(/^Last:\s*/i, "")
      }};
    }});
  }}

  function renderBoard(data) {{
    if (!boardEl || !data || !Array.isArray(data.sections)) return;
    lastStatusData = data;
    lastCloud = sanitizeCloudAgents((data && data.cloud_agents) || lastCloud);
    paintAgents((data && data.llm_work) || []);
    var controlProjects = [];
    var html = glanceHtml(data.pending, data.sections) + typeTabsHtml(data.sections, data.pending, "");
    data.sections.forEach(function (sec) {{
      var kind = sectionKind(sec.id);
      if (kind === "pulse") return;
      if (sec.id === "controls") {{
        controlProjects = sec.projects || [];
        var items = data.pending || [];
        html += '<section id="controls" class="block pending" data-tab-panel="controls" hidden role="tabpanel" aria-label="Decisions">' +
          "<h2>" + esc(sec.title || "Decisions") + "</h2>" + pendingShell(items) + "</section>";
        return;
      }}
      if (sec.id === "features") {{
        html += '<section id="features" class="block foot">' +
          '<details class="how-board"><summary>How this board works</summary>' +
          '<p class="pending-help">Engineer notes -- not the daily ops list.</p>' +
          '<p class="pending-help">Public board -- Approve opens a GitHub issue; submit while logged in as <code>rupret007</code>.</p>' +
          toolsRow(controlProjects) + lanesHtml(sec.projects || []) + fetchedLineHtml(data.fetched_repos) + "</details></section>";
        return;
      }}
      if (sec.id === "abilities") {{
        html += '<section id="abilities" class="block foot">' +
          '<details class="abilities-foot"><summary>What Bob can do</summary>' +
          '<p class="pending-help">Texts / food after Jeff yes. No send button. Honest: there is no order button on this board.</p>' +
          lanesHtml(sec.projects || []) + "</details></section>";
        return;
      }}
      var cls = kind === "primary" ? "primary" : "secondary";
      var panel = isTypeTab(sec.id)
        ? ' data-tab-panel="' + esc(sec.id) + '" hidden role="tabpanel" aria-labelledby="tab-' + esc(sec.id) + '"'
        : "";
      html += '<section id="' + esc(sec.id || "") + '" class="block ' + cls + '"' + panel + ">" +
        "<h2>" + esc(sec.title || "") + "</h2>" +
        lanesHtml(sec.projects || [], kind === "primary") + "</section>";
    }});
    if (boardEl.getAttribute("data-fp") === html) return;
    var open = snapshotOpen();
    boardEl.innerHTML = html;
    boardEl.setAttribute("data-fp", html);
    restoreOpen(open);
    applyTypeTab(currentTypeTab);
    refreshOpenDetail();
    window.dispatchEvent(new CustomEvent("bob-ops-painted"));
  }}

  function parseStampMs(ts) {{
    var ms = Date.parse(String(ts || ""));
    return isFinite(ms) ? ms : 0;
  }}
  function pollIsNewer(nextTs, knownTs) {{
    var nextMs = parseStampMs(nextTs);
    var knownMs = parseStampMs(knownTs);
    if (!nextMs) return false;
    if (!knownMs) return true;
    return nextMs >= knownMs;
  }}
  function pollFailureCounts(seq, currentSeq) {{
    return seq === currentSeq;
  }}
  function pollPaintDecision(nextTs, knownTs, lastFp, fp) {{
    if (!pollIsNewer(nextTs, knownTs)) return "ignore";
    var nextMs = parseStampMs(nextTs);
    var knownMs = parseStampMs(knownTs);
    if (lastFp === null) return nextMs > knownMs ? "paint" : "stamp";
    return fp !== lastFp ? "paint" : "stamp";
  }}
  function applyStamp(data) {{
    var next = data && data.generated_at;
    if (next && pollIsNewer(next, known)) {{
      known = next;
      knownMs = parseStampMs(next) || knownMs;
      stamp.setAttribute("data-generated-at", known);
      if (data && data.generated_at_display && displayEl) {{
        displayEl.textContent = data.generated_at_display;
        stamp.setAttribute("data-display", data.generated_at_display);
      }}
    }}
  }}

  var pollSeq = 0;
  var POLL_TIMEOUT_MS = 8000;
  var pollAbort = null;
  var pollTimeout = null;
  function setRetryBusy(busy) {{
    if (!retryStatus) return;
    retryStatus.disabled = !!busy;
    retryStatus.textContent = busy ? "Checking..." : "Retry now";
    retryStatus.setAttribute("aria-busy", busy ? "true" : "false");
  }}
  lastAgents = readDomLlmWork();
  lastCloud = [];
  lastStatusData = bootstrapStatus();
  function signalHref(p) {{
    var signal = compactSignal(p);
    if (!signal) return "";
    var hrefs = laneHrefs(p);
    if (String(signal).indexOf("CI") === 0) return hrefs.ci || "";
    if (String(signal).indexOf("Draft #") === 0 || String(signal).indexOf("PR #") === 0) return coordPrUrl(p);
    if (String(signal).indexOf("Stack ") === 0 || String(signal).slice(-9) === "-PR stack" || String(signal).indexOf("open PR") !== -1) {{
      var n = (typeof p.open_prs === "number" && isFinite(p.open_prs)) ? p.open_prs : 0;
      var pulls = pullsUrlFromRepo(hrefs.repo || "");
      if (n > 1) return pulls;
      return hrefs.pr || pulls;
    }}
    var rel = String(p.release || "").trim();
    if (String(signal) === "Latest != source" || (rel && String(signal) === rel)) {{
      return latestReleaseUrlFromRepo(hrefs.repo || "") || safeReleaseUrl(p.release_url);
    }}
    return "";
  }}
  function workHref(href) {{
    return safeAgentUrl(href) || safePrUrl(href) || safeActionsUrl(href) || safePullsUrl(href) || safeReleaseUrl(href) || safeRepoUrl(href) || safeGameUrl(href);
  }}
  function openWorkLink(href) {{
    var url = workHref(href);
    if (!url) return false;
    if (typeof window.openBlank === "function") return !!window.openBlank(url);
    return false;
  }}
  function handleWorkClick(ev) {{
    var t = ev && ev.target;
    var work = t && t.closest ? t.closest('a[data-open="work"]') : null;
    if (!work) return false;
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) {{
      var modified = workHref(work.getAttribute("href") || "");
      if (!modified && ev.preventDefault) ev.preventDefault();
      return !!modified;
    }}
    var ok = workHref(work.getAttribute("href") || "");
    if (!ok) {{
      if (ev.preventDefault) ev.preventDefault();
      return false;
    }}
    // Primary: real <a target=_blank> (same as Approve). Fallback: openBlank
    // when native navigation is not available so iOS cannot swallow a popup.
    if (work.tagName === "A" && (work.getAttribute("target") || "") === "_blank" && !ev.defaultPrevented) {{
      return true;
    }}
    if (ev.preventDefault) ev.preventDefault();
    return openWorkLink(ok);
  }}
  window.workHref = workHref;
  window.openWorkLink = openWorkLink;
  document.addEventListener("click", handleWorkClick);
  function detailTokenForRow(row) {{
    if (!row) return "";
    var kind = String(row.getAttribute("data-detail-kind") || "");
    var token = String(row.getAttribute("data-detail-key") || "");
    if ((kind !== "project" && kind !== "work") || !token) return "";
    return token;
  }}
  function handleDetailTap(ev) {{
    if (!ev || ev.defaultPrevented) return false;
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return false;
    var target = ev.target;
    if (!target || !target.closest) return false;
    if (target.closest("a,button,summary,[data-dec],[data-action],[data-tab]")) return false;
    var row = target.closest("[data-detail-key]");
    if (!row) return false;
    var token = detailTokenForRow(row);
    if (!token) return false;
    if (ev.preventDefault) ev.preventDefault();
    return openDetail(token, true);
  }}
  document.addEventListener("click", handleDetailTap);
  document.addEventListener("keydown", function (ev) {{
    if (!ev) return;
    if (ev.key !== "Escape") return;
    if (detailOpen) closeDetail(true);
  }});
  if (detailCloseEl) {{
    detailCloseEl.addEventListener("click", function () {{
      closeDetail(true);
    }});
  }}
  if (detailScrim) {{
    detailScrim.addEventListener("click", function () {{
      closeDetail(true);
    }});
  }}
  function poll() {{
    var seq = ++pollSeq;
    setRetryBusy(true);
    if (pollAbort) {{
      try {{ pollAbort.abort(); }} catch (e) {{}}
    }}
    if (pollTimeout) clearTimeout(pollTimeout);
    pollAbort = new AbortController();
    pollTimeout = setTimeout(function () {{
      try {{ pollAbort.abort(); }} catch (e) {{}}
    }}, POLL_TIMEOUT_MS);
    var url = "./status.json?ts=" + Date.now();
    fetch(url, {{ cache: "no-store", signal: pollAbort.signal }})
      .then(function (res) {{
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      }})
      .then(function (data) {{
        if (!pollFailureCounts(seq, pollSeq)) return;
        lastStatusData = data;
        var fp = boardFingerprint(data);
        var decision = pollPaintDecision(data && data.generated_at, known, lastFp, fp);
        if (decision === "ignore") {{
          // Stale CDN/cache body. Do not rewind freshness or rewrite lanes.
          lastPollOk = Date.now();
          pollFailStreak = 0;
          paint();
          updateSilence();
          return;
        }}
        lastPollOk = Date.now();
        pollFailStreak = 0;
        if (dot) {{
          dot.classList.add("poll");
          setTimeout(function () {{ dot.classList.remove("poll"); }}, 600);
        }}
        lastAgents = (data && data.llm_work) || lastAgents;
        lastCloud = sanitizeCloudAgents((data && data.cloud_agents) || lastCloud);
        paintAgents(lastAgents);
        applyStamp(data);
        if (decision === "paint") {{
          // Soft-paint from JSON -- skip timestamp-only Actions refreshes (no flash).
          renderBoard(data);
        }}
        lastFp = fp;
        paint();
        updateSilence();
      }})
      .catch(function () {{
        if (!pollFailureCounts(seq, pollSeq)) return;
        pollFailStreak += 1;
        freshness.textContent = "poll failed -- retrying";
        freshness.classList.add("stale");
        if (dot) dot.classList.add("stale");
        updateSilence();
      }})
      .then(function () {{
        if (seq === pollSeq) {{
          if (pollTimeout) clearTimeout(pollTimeout);
          setRetryBusy(false);
        }}
      }});
  }}

  if (retryStatus) {{
    retryStatus.addEventListener("click", function () {{
      if (document.visibilityState !== "hidden") poll();
    }});
  }}

  var pollTimer = null;
  var paintTimer = null;
  function startPaintClock() {{
    paint();
    if (paintTimer) clearInterval(paintTimer);
    paintTimer = setInterval(paint, 1000);
  }}
  function stopPaintClock() {{
    if (paintTimer) {{ clearInterval(paintTimer); paintTimer = null; }}
  }}
  function startPolling() {{
    if (pollTimer) clearInterval(pollTimer);
    poll();
    pollTimer = setInterval(poll, POLL_MS);
    startPaintClock();
  }}
  function stopPolling() {{
    if (pollTimer) {{ clearInterval(pollTimer); pollTimer = null; }}
    pollSeq += 1;
    if (pollAbort) {{
      try {{ pollAbort.abort(); }} catch (e) {{}}
    }}
    if (pollTimeout) clearTimeout(pollTimeout);
    setRetryBusy(false);
    stopPaintClock();
  }}
  document.addEventListener("visibilitychange", function () {{
    if (document.visibilityState === "hidden") stopPolling();
    else startPolling();
  }});
  window.addEventListener("pageshow", function () {{
    if (document.visibilityState !== "hidden") startPolling();
  }});

  paint();
  applyTypeTab(tabFromHash());
  // Pause polls when tab hidden; resume on visible / bfcache pageshow.
  if (document.visibilityState !== "hidden") startPolling();
  else setTimeout(function () {{ if (document.visibilityState !== "hidden") startPolling(); }}, 5000);
}})();
</script>
</body>
</html>
'''

# Public board: never emit OTP verify. Fail-closed on leftover hashes.
if drop_leftover_verify(status):
    print("drop leftover verify at write (fail-closed)")

# Atomic status.json + index.html write (uptime-pulse pattern): tmp + replace.
_status_tmp = root / "status.json.tmp"
_status_tmp.write_text(json.dumps(status, indent=2) + "\n")
_status_tmp.replace(root / "status.json")
_html_tmp = root / "index.html.tmp"
_html_tmp.write_text(html)
_html_tmp.replace(root / "index.html")
print(f"Wrote {root/'index.html'} and {root/'status.json'} (atomic)")
print(f"Updated: {updated_ct}")
print(f"Fetched OK: {status['fetched_repos']}")
if status.get("inaccessible"):
    print(f"Inaccessible: {status['inaccessible']}")
print(f"Agents source: {status.get('agents_source')}")
for _a in status.get("agents") or []:
    print(f"  - {_a.get('id')}: {_a.get('state')} · {_a.get('detail')}")
PY

rm -f "$TMP"

# Keep README tip current
if ! grep -q 'refresh.sh' "$ROOT/README.md" 2>/dev/null; then
  printf '\n## refresh.sh\n\n```bash\n./refresh.sh          # rebuild locally\n./refresh.sh --push   # rebuild + push to Pages\n```\n' >> "$ROOT/README.md"
fi

if [[ $PUSH -eq 1 ]]; then
  WORK="$(mktemp -d)"
  gh repo clone "$OWNER/bob-ops-dashboard" "$WORK" -- --quiet
  mkdir -p "$WORK/.github/workflows"
  cp "$ROOT/index.html" "$ROOT/status.json" "$ROOT/README.md" "$ROOT/refresh.sh" "$WORK/"
  [[ -f "$ROOT/board_meta.py" ]] && cp "$ROOT/board_meta.py" "$WORK/"
  [[ -f "$ROOT/probe-agents-status.sh" ]] && cp "$ROOT/probe-agents-status.sh" "$WORK/"
  [[ -f "$ROOT/qa-claim-smoke.sh" ]] && cp "$ROOT/qa-claim-smoke.sh" "$WORK/"
  [[ -f "$ROOT/qa-source-only.sh" ]] && cp "$ROOT/qa-source-only.sh" "$WORK/"
  [[ -f "$ROOT/test_board_meta.py" ]] && cp "$ROOT/test_board_meta.py" "$WORK/"
  [[ -f "$ROOT/test_refresh_outage_guard.py" ]] && cp "$ROOT/test_refresh_outage_guard.py" "$WORK/"
  [[ -f "$ROOT/test_open_decision.js" ]] && cp "$ROOT/test_open_decision.js" "$WORK/"
  [[ -f "$ROOT/test_open_links.js" ]] && cp "$ROOT/test_open_links.js" "$WORK/"
  [[ -f "$ROOT/test_soft_paint.js" ]] && cp "$ROOT/test_soft_paint.js" "$WORK/"
  [[ -f "$ROOT/.gitignore" ]] && cp "$ROOT/.gitignore" "$WORK/"
  # Do not commit agents-status.json by default (Mac-local probe snapshot); refresh merges it when present.
  if [[ -f "$ROOT/.github/workflows/refresh-dashboard.yml" ]]; then
    cp "$ROOT/.github/workflows/refresh-dashboard.yml" "$WORK/.github/workflows/"
  fi
  if [[ -f "$ROOT/.github/workflows/qa-claim-smoke.yml" ]]; then
    cp "$ROOT/.github/workflows/qa-claim-smoke.yml" "$WORK/.github/workflows/"
  fi
  chmod +x "$WORK/refresh.sh"
  [[ -f "$WORK/qa-claim-smoke.sh" ]] && chmod +x "$WORK/qa-claim-smoke.sh"
  [[ -f "$WORK/qa-source-only.sh" ]] && chmod +x "$WORK/qa-source-only.sh"
  cd "$WORK"
  git add index.html status.json README.md refresh.sh
  [[ -f board_meta.py ]] && git add board_meta.py
  [[ -f probe-agents-status.sh ]] && git add probe-agents-status.sh
  [[ -f qa-claim-smoke.sh ]] && git add qa-claim-smoke.sh
  [[ -f qa-source-only.sh ]] && git add qa-source-only.sh
  [[ -f test_board_meta.py ]] && git add test_board_meta.py
  [[ -f test_refresh_outage_guard.py ]] && git add test_refresh_outage_guard.py
  [[ -f test_open_decision.js ]] && git add test_open_decision.js
  [[ -f test_open_links.js ]] && git add test_open_links.js
  [[ -f test_soft_paint.js ]] && git add test_soft_paint.js
  [[ -f .gitignore ]] && git add .gitignore
  [[ -f .github/workflows/refresh-dashboard.yml ]] && git add .github/workflows/refresh-dashboard.yml
  [[ -f .github/workflows/qa-claim-smoke.yml ]] && git add .github/workflows/qa-claim-smoke.yml
  if git diff --cached --quiet; then
    echo "No changes to push."
  else
    git -c user.email="${OWNER}@users.noreply.github.com" -c user.name="$OWNER" \
      commit -m "chore: refresh ops dashboard $(date -u +%Y-%m-%dT%H:%MZ)"
    git push origin HEAD:main
    echo "Pushed. Pages: https://${OWNER}.github.io/bob-ops-dashboard/"
  fi
  rm -rf "$WORK"
fi
