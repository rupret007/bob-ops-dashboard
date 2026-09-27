#!/usr/bin/env bash
# Rebuild Bob ops dashboard from live gh data, then optionally push to Pages.
# Noninteractive: safe for GitHub Actions (gh uses GH_TOKEN / GITHUB_TOKEN).
# Usage:
#   ./refresh.sh              # write index.html + status.json (+ llm-work-now.json) in this dir
#   ./refresh.sh --push       # also commit+push to rupret007/bob-ops-dashboard main
#
# Pages publish set (must stay aligned with .github/workflows/refresh-dashboard.yml):
#   index.html status.json llm-work-now.json [harden-window.json if present]
# R126 rewrites llm-work-now.json every rebuild — it must publish with status.json.
# Unstaged llm-work-now must never block rebase on a concurrent tip move
# (measured fail: actions run 36137598706). Do not commit agents-status.json.
# K2/K3: after write (and before --push PASS) assert dual-SoT — status
# llm_work_now_freshness.generated_at must match llm-work-now.json; never
# freshness.ok while blob age > WARN TTL. Matching stamps = the publish bar.
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
REPOS=(bob-ops-dashboard webjam AdoptIQ StoryBoard Storyland-Fantasy-Football)
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
  command -v node >/dev/null || { echo "node required for the shared page renderer"; exit 1; }
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
draft_prs = [
    ref for ref in open_pr_refs if ref["draft"]
    if any(
        p.get("number") == ref["number"]
        and p.get("state") == "open"
        and str(((p.get("base") or {}).get("repo") or {}).get("full_name") or "").lower() == full.lower()
        and str(((p.get("head") or {}).get("repo") or {}).get("full_name") or "").lower() == full.lower()
        for p in prs if isinstance(p, dict)
    )
]
ready_prs = [p for p in prs if isinstance(p, dict) and not is_draft_pr(p)]
open_pr = pick_open_pr(ready_prs) if prs_complete else None
open_pr_stack = detect_linear_pr_stack(ready_prs, branch) if prs_complete else []
# A Cursor agent URL can grant access beyond high-level repository status.
# Never materialize one from a private PR onto this public dashboard.
clouds = [] if private or repo.lower() in {"adoptiq", "storyland-fantasy-football"} or not prs_complete else extract_cloud_agents_from_prs(prs, limit=1)
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
    "draft_prs": draft_prs,
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
    incomplete_public_collections,
    parse_coord_issue,
    public_coord,
    status_with_coord_review,
    finalize_llm_work_after_live_poll,
    write_llm_work_now_json,
    build_llm_work_now_payload,
    build_llm_work_now_freshness_meta,
    assert_dual_sot_files,
    LLM_WORK_NOW_STALE_WARN_SEC,
    DUAL_SOT_MAX_STAMP_SKEW_SEC,
    LLM_WORK_PROOF_TS_KEYS,
    normalize_harden_window,
    drop_leftover_verify,
    extract_cloud_agents_from_prs,
    public_high_level_ci,
    merge_cloud_agents,
    resolve_agents,
    latest_release_url_from_repo,
    safe_agent_url,
    safe_game_url,
    safe_pr_url,
    safe_release_url,
    status_from_fetch,
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
        "draft_prs": r.get("draft_prs", []),
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

# Only these five project lanes belong on this operations board.
projects = [
    project("bob-ops-dashboard", notes="Public operations board. Draft review before any release."),
    project("webjam", notes="Musician collaboration. Latest is the published test candidate; source may be ahead."),
    project("AdoptIQ", high_level_only=True,
            notes="Offline candidate only. ready_for_live_cisco=false."),
    project("StoryBoard", notes="Band-business engine. Review current checks and draft work."),
    project("Storyland-Fantasy-Football", high_level_only=True,
            notes="Private fantasy football workspace. High-level status only."),
]
for p, ident, label in zip(projects,
        ("web-bob", "webjam", "adoptiq", "storyboard", "storyland-fantasy"),
        ("Web Bob", "WebJam", "AdoptIQ", "StoryBoard", "Storyland Fantasy")):
    p["id"], p["name"] = ident, label
    if p.get("private"):
        p["next_action"] = "Review the offline candidate." if ident == "adoptiq" else "Review progress in the private workspace."
    else:
        p["next_action"] = (p.get("coord") or {}).get("next") or "Choose the next project task."
    # Coordination text is not a source of private repository metadata.
    p.pop("coord", None)
    if ident == "adoptiq":
        p["ready_for_live_cisco"] = False
status = {
    "schema_version": 2,
    "generated_at": updated_iso,
    "generated_at_display": updated_ct,
    "timezone": "America/Chicago",
    "owner": "rupret007",
    "dashboard": "bob-ops-dashboard",
    "projects": projects,
    "fetched_repos": [x.get("name") for x in fetched if x.get("accessible")],
    "inaccessible": [x.get("name") for x in fetched if not x.get("accessible")],
    "refresh_started_ms": refresh_started_ms,
}

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
    ("codex", "Codex"),
    ("claude", "Claude"),
    ("cursor", "Cursor"),
    ("gemini", "Gemini"),
    ("minimax", "MiniMax"),
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
        "cursor": "cursor",
        "cursor-cloud": "cursor",
        "cloud": "cursor",
    }
    if value in {"codex", "claude", "cursor", "gemini", "minimax"}:
        return value
    if value in aliases:
        return aliases[value]
    text = str(raw_name or "").strip().lower()
    if "cursor" in text or text == "cloud":
        return "cursor"
    if "codex" in text or "chatgpt" in text:
        return "codex"
    if "claude" in text:
        return "claude"
    if "gemini" in text:
        return "gemini"
    if "minimax" in text:
        return "minimax"
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
    if status_name not in {"running", "finished", "blocked", "idle", "unknown"}:
        status_name = "unknown"
    row = {
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
    # Private project work is public only as a coarse assignment and state.
    repo_name = repo.split("/")[-1].lower()
    restricted = repo_name in {"adoptiq", "storyland-fantasy-football"} or bool(g(repo.split("/")[-1]).get("private"))
    if restricted:
        row.update(task_title="Private project work", pr_url="", pr_number="", branch="",
                   goal="Activity details stay in the private workspace.", agent_url="",
                   why="", note="", last_task="")
    # Preserve proof timestamps — dropping them made honest heartbeats vanish on ingest.
    # Do NOT validate/demote here: live Cloud stamp must run first (R1 stamp-before-validate).
    for key in LLM_WORK_PROOF_TS_KEYS:
        val = raw.get(key)
        if val is not None and str(val).strip():
            row[key] = str(val).strip()
    return row

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
    if row and (row["id"] not in by_lane or raw.get("id") == row["id"]):
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
        "status": "unknown",
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

# PR attribution supplies a link, not evidence that a worker is running.
# Only upstream activity timestamps may establish or renew a work heartbeat.
_live_cloud_lanes = set()
_now_iso = datetime.now(tz=ZoneInfo("UTC")).isoformat()
llm_work = finalize_llm_work_after_live_poll(
    llm_work, _live_cloud_lanes, now_iso=_now_iso
)
status["llm_work"] = llm_work

# Build paired data before rendering; no artifact changes if the renderer fails.
_ct_stamp = now.strftime("%Y-%m-%d %H:%M CT")
_publish_clock = time.time()
_pub = build_llm_work_now_payload(llm_work, generated_at_ct=_ct_stamp, now=_publish_clock)
status["llm_work"] = _pub["work"]
# Never claim ok on a stale paired artifact: use this exact publication payload.
status["llm_work_now_freshness"] = build_llm_work_now_freshness_meta(_pub)
_fresh_warns = status["llm_work_now_freshness"].get("warnings") or []
for _w in _fresh_warns:
    print(f"WARN {_w}")

# Harden stress window strip (separate from llm_work — never invents Running chips).
_hw_blob = None
for _hw_path in (
    root / "harden-window.json",
    Path("/workspace/bob-ops-dashboard/harden-window.json"),
    Path("/home/box/conductor/llm-work-now/harden-window.json"),
):
    if _hw_path.is_file():
        try:
            _hw_blob = json.loads(_hw_path.read_text(encoding="utf-8"))
            break
        except Exception:
            _hw_blob = None
            continue
if _hw_blob is None:
    _env_hw = os.environ.get("HARDEN_WINDOW_JSON")
    if _env_hw:
        try:
            _hw_blob = json.loads(_env_hw)
        except Exception:
            _hw_blob = None
status["harden_window"] = normalize_harden_window(_hw_blob)

# Decisions inbox (needed before first paint -- Jeff should see pending immediately)
prev = prev_early if isinstance(prev_early, dict) else {}
if drop_leftover_verify(prev):
    print("ignored previous verify challenge (public board, no OTP)")
if drop_leftover_verify(status):
    print("drop leftover verify: public board has no OTP gate")

def public_decision_id(value):
    # Retired project and content decisions are outside the five-project board.
    decision_id = str(value or "").lower()
    return decision_id if decision_id == "adoptiq-live-cisco" else ""

decisions = []
if isinstance(prev.get("decisions"), list):
    for previous_decision in prev["decisions"]:
        if not isinstance(previous_decision, dict):
            continue
        sanitized_decision = {key: previous_decision[key] for key in ("id", "decision", "issue", "url", "at", "author") if key in previous_decision}
        sanitized_decision["id"] = public_decision_id(sanitized_decision.get("id"))
        if sanitized_decision["id"]:
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
    if not pid:
        continue
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

# Build HTML from the same renderer used for browser soft refreshes.
from render_dashboard import render_dashboard
html = render_dashboard(status, root)

# Public board: never emit OTP verify. Fail-closed on leftover hashes.
if drop_leftover_verify(status):
    print("drop leftover verify at write (fail-closed)")

# The renderer has succeeded; publish the exact paired work payload.
write_llm_work_now_json(root / "llm-work-now.json", _pub["work"],
                        generated_at_ct=_ct_stamp, now=_publish_clock)
print(f"Wrote llm-work-now.json generated_at={_ct_stamp}")

# Atomic status.json + index.html write (uptime-pulse pattern): tmp + replace.
_status_tmp = root / "status.json.tmp"
_status_tmp.write_text(json.dumps(status, indent=2) + "\n")
_status_tmp.replace(root / "status.json")
_html_tmp = root / "index.html.tmp"
_html_tmp.write_text(html)
_html_tmp.replace(root / "index.html")
print(f"Wrote {root/'index.html'} and {root/'status.json'} (atomic)")
# K2: dual-SoT assert on the paired artifacts about to publish (working tree).
# Prefer local files over remote Pages — Pages lag is a separate concern.
assert_dual_sot_files(root / "status.json", root / "llm-work-now.json")
print(
    f"dual-SoT PASS: status.freshness.generated_at matches llm-work-now "
    f"(skew≤{DUAL_SOT_MAX_STAMP_SKEW_SEC}s; ok never with age>{LLM_WORK_NOW_STALE_WARN_SEC}s)"
)
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
  [[ -f "$ROOT/llm-work-now.json" ]] && cp "$ROOT/llm-work-now.json" "$WORK/"
  [[ -f "$ROOT/harden-window.json" ]] && cp "$ROOT/harden-window.json" "$WORK/"
  [[ -f "$ROOT/write_harden_window.py" ]] && cp "$ROOT/write_harden_window.py" "$WORK/"
  [[ -f "$ROOT/board_meta.py" ]] && cp "$ROOT/board_meta.py" "$WORK/"
  cp "$ROOT/render_dashboard.py" "$ROOT/qa-generated.py" "$ROOT/test_dashboard.js" "$ROOT/test_dashboard.py" "$ROOT/test_offline_qa.py" "$ROOT/offline_qa_fixtures.py" "$ROOT/qa-offline.py" "$WORK/"
  mkdir -p "$WORK/ui"
  cp "$ROOT/ui/dashboard.js" "$ROOT/ui/dashboard.css" "$WORK/ui/"
  [[ -f "$ROOT/probe-agents-status.sh" ]] && cp "$ROOT/probe-agents-status.sh" "$WORK/"
  [[ -f "$ROOT/qa-claim-smoke.sh" ]] && cp "$ROOT/qa-claim-smoke.sh" "$WORK/"
  [[ -f "$ROOT/qa-source-only.sh" ]] && cp "$ROOT/qa-source-only.sh" "$WORK/"
  [[ -f "$ROOT/test_board_meta.py" ]] && cp "$ROOT/test_board_meta.py" "$WORK/"
  [[ -f "$ROOT/test_refresh_outage_guard.py" ]] && cp "$ROOT/test_refresh_outage_guard.py" "$WORK/"
  [[ -f "$ROOT/test_refresh_publish_artifacts.py" ]] && cp "$ROOT/test_refresh_publish_artifacts.py" "$WORK/"
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
  # Same Pages board artifacts as the Actions commit step (plus source helpers).
  # llm-work-now.json must ship with status.json; unstaged leftovers must not
  # block rebase when a concurrent tip move rejects the first push.
  PAGES_ARTIFACTS=(index.html status.json)
  [[ -f llm-work-now.json ]] && PAGES_ARTIFACTS+=(llm-work-now.json)
  [[ -f harden-window.json ]] && PAGES_ARTIFACTS+=(harden-window.json)
  git add "${PAGES_ARTIFACTS[@]}" README.md refresh.sh render_dashboard.py ui/ qa-generated.py test_dashboard.js test_dashboard.py test_offline_qa.py offline_qa_fixtures.py qa-offline.py
  [[ -f write_harden_window.py ]] && git add write_harden_window.py
  [[ -f board_meta.py ]] && git add board_meta.py
  [[ -f probe-agents-status.sh ]] && git add probe-agents-status.sh
  [[ -f qa-claim-smoke.sh ]] && git add qa-claim-smoke.sh
  [[ -f qa-source-only.sh ]] && git add qa-source-only.sh
  [[ -f test_board_meta.py ]] && git add test_board_meta.py
  [[ -f test_refresh_outage_guard.py ]] && git add test_refresh_outage_guard.py
  [[ -f test_refresh_publish_artifacts.py ]] && git add test_refresh_publish_artifacts.py
  [[ -f .gitignore ]] && git add .gitignore
  [[ -f .github/workflows/refresh-dashboard.yml ]] && git add .github/workflows/refresh-dashboard.yml
  [[ -f .github/workflows/qa-claim-smoke.yml ]] && git add .github/workflows/qa-claim-smoke.yml
  if git diff --cached --quiet; then
    echo "No changes to push."
  else
    git -c user.email="${OWNER}@users.noreply.github.com" -c user.name="$OWNER" \
      commit -m "chore: refresh ops dashboard $(date -u +%Y-%m-%dT%H:%MZ)"
    # Race-safe vs concurrent Actions refresh / other --push (mirror workflow).
    pushed=0
    for attempt in 1 2 3 4 5; do
      if git push origin HEAD:main; then
        echo "Pushed on attempt ${attempt}."
        pushed=1
        break
      fi
      echo "Push rejected (attempt ${attempt}); stash dirt, rebase onto origin/main, retry."
      STASHED=0
      if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
        git stash push --include-untracked -m "refresh-push-race-${attempt}"
        STASHED=1
      fi
      git fetch origin main
      if ! git rebase origin/main; then
        # Prefer this rebuild's Pages artifacts (rebase --theirs = our commit).
        git checkout --theirs -- "${PAGES_ARTIFACTS[@]}"
        git add "${PAGES_ARTIFACTS[@]}"
        GIT_EDITOR=true git rebase --continue
      fi
      if [[ "${STASHED}" -eq 1 ]]; then
        git stash pop || true
        git add "${PAGES_ARTIFACTS[@]}"
        if ! git diff --cached --quiet; then
          git -c user.email="${OWNER}@users.noreply.github.com" -c user.name="$OWNER" \
            commit --amend --no-edit
        fi
      fi
    done
    if [[ "${pushed}" -ne 1 ]]; then
      echo "Exhausted push retries." >&2
      exit 1
    fi
    # K2 post-push dual-SoT: re-check the paired artifacts that just pushed
    # (working tree / HEAD content — not remote Pages CDN lag).
    python3 -c "from pathlib import Path; from board_meta import assert_dual_sot_files; assert_dual_sot_files(Path('status.json'), Path('llm-work-now.json')); print('dual-SoT PASS post-push (working-tree pair)')"
    echo "Pushed. Pages: https://${OWNER}.github.io/bob-ops-dashboard/"
  fi
  rm -rf "$WORK"
fi
