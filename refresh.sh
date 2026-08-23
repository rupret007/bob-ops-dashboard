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
  AdoptIQ TACTrack AI-Music-Vault rad-dad-show-night Andrea_NanoBot story-corner-shelf
)
PUSH=0
[[ "${1:-}" == "--push" ]] && PUSH=1

need_gh() {
  command -v gh >/dev/null || { echo "gh required"; exit 1; }
  command -v python3 >/dev/null || { echo "python3 required"; exit 1; }
}

fetch_repo() {
  local r="$1"
  python3 - "$OWNER" "$r" <<'PY'
import json, subprocess, sys
owner, repo = sys.argv[1], sys.argv[2]
full = f"{owner}/{repo}"

def api(path, default=None):
    try:
        out = subprocess.check_output(["gh", "api", path], text=True, stderr=subprocess.DEVNULL)
        return json.loads(out)
    except Exception:
        return default

meta = api(f"repos/{full}")
if not meta:
    print(json.dumps({"name": repo, "accessible": False, "error": "inaccessible"}))
    sys.exit(0)

branch = meta.get("default_branch") or "main"
commit = api(f"repos/{full}/commits/{branch}", {}) or {}
sha = (commit.get("sha") or "")[:7]
c = (commit.get("commit") or {})
date = ((c.get("committer") or {}).get("date")) or ((c.get("author") or {}).get("date"))
msg = (c.get("message") or "").split("\n", 1)[0]

prs = api(f"repos/{full}/pulls?state=open&per_page=100", []) or []
runs = api(f"repos/{full}/actions/runs?per_page=20", {}) or {}
ci = None
for run in runs.get("workflow_runs") or []:
    if run.get("head_branch") == branch and run.get("status") == "completed":
        ci = {
            "name": run.get("name"),
            "conclusion": run.get("conclusion"),
            "branch": branch,
            "sha": (run.get("head_sha") or "")[:7],
            "created": run.get("created_at"),
        }
        break

rels = api(f"repos/{full}/releases?per_page=1", []) or []
release = (rels[0].get("tag_name") if rels else None)

print(json.dumps({
    "accessible": True,
    "name": meta.get("name"),
    "full_name": full,
    "private": bool(meta.get("private")),
    "html_url": meta.get("html_url"),
    "default_branch": branch,
    "tip_sha": sha or None,
    "tip_date": date,
    "tip_msg": msg,
    "open_prs": len(prs),
    "ci": ci,
    "release": release,
}, indent=None))
PY
}

need_gh
echo "Fetching ${#REPOS[@]} repos as $OWNER ..."
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
    CONTROL_ACTIONS,
    attention_rank,
    compact_signal,
    drop_leftover_verify,
    is_quiet_lane,
    merge_first_class,
    presentation,
    short_note,
    sort_pending,
    visible_chip,
)
refresh_started_ms = int(os.environ.get("REFRESH_STARTED_MS") or 0) or int(time.time() * 1000)
fetched = json.loads(Path(sys.argv[2]).read_text())
by = {x.get("name") or x.get("full_name","").split("/")[-1]: x for x in fetched}
now = datetime.now(ZoneInfo("America/Chicago"))
updated_ct = now.strftime("%a %b %-d, %Y · %-I:%M %p %Z")
updated_iso = now.isoformat()

def g(name):
    return by.get(name) or {"accessible": False, "name": name}

def status_for(name, override=None):
    if override:
        return override
    r = g(name)
    if not r.get("accessible"):
        return "parked"
    ci = r.get("ci") or {}
    concl = ci.get("conclusion")
    if concl == "failure":
        return "red"
    if (r.get("open_prs") or 0) > 0:
        return "yellow"
    if concl == "success" or ci is None:
        return "green"
    return "yellow"

CHIP = {
    "green": "Green", "yellow": "Yellow", "red": "Red",
    "parked": "Parked", "jeff-gate": "Jeff-gate",
}

def project(name, *, status=None, notes="", product_sha=None, jeff_gate=False, extra=None):
    r = g(name)
    st = "jeff-gate" if jeff_gate else status_for(name, status)
    p = {
        "name": name if not r.get("name") else r["name"],
        "repo": r.get("full_name"),
        "url": r.get("html_url"),
        "private": r.get("private"),
        "status": st,
        "chip": CHIP.get(st, st),
        "default_branch": r.get("default_branch"),
        "tip_sha": r.get("tip_sha"),
        "tip_date": r.get("tip_date"),
        "open_prs": r.get("open_prs"),
        "ci": r.get("ci"),
        "release": r.get("release"),
        "notes": notes,
        "accessible": r.get("accessible", False),
    }
    if product_sha:
        p["product_sha"] = product_sha
    if extra:
        p.update(extra)
    # Friendly display names
    rename = {
        "webjam": "WebJam",
        "Rad-Dad-Merch": "Rad Dad Merch",
        "rad-dad-show-night": "Show Night",
        "AI-Music-Vault": "AI Music Vault",
        "Andrea_NanoBot": "Andrea NanoBot",
        "story-corner-shelf": "Closet shelf",
    }
    p["name"] = rename.get(name, p["name"])
    return p

# Curated narrative notes (safe / no secrets) -- live SHAs/CI come from gh
web = g("webjam")
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
        project("webjam", jeff_gate=True, product_sha="5ca6ba5",
                notes="PR #21 merged (docs on 5ca6ba5). Release stays v0.26.0 until Jeff names v0.27. Exploratory click-through Jeff-gated."),
        project("StoryLiner", notes="PR #1 lint fix merged; confirm main CI from live fetch."),
        project("StoryBoard", notes="Quiet green unless CI says otherwise."),
        project("Rad-Dad-Merch", notes="Merch lane; watch Release integrity."),
        project("RadDadSite", status="yellow",
                notes="Tip green typical; draft #6 prod deploy parked/Jeff-gate if still open."),
        project("rad-dad-show-night", notes="Show-night run sheet / flyer. No CI is OK."),
        project("AI-Music-Vault", notes="Private docs/index. High-level only on public page."),
        project("Turdanoid", status="yellow",
                notes="Tip usually green; stale open PRs -> hygiene."),
      ],
    },
    {
      "id": "cisco",
      "title": "Cisco work",
      "projects": [
        project("AdoptIQ", status="yellow",
                notes="Cisco CS desktop -- Build 115 in progress (Codex + Cloud Agent). No secrets on this page."),
        project("TACTrack", status="yellow",
                notes="Private. High-level only -- CI / open PRs from live fetch."),
        {
          "name": "AdoptIQ notes", "status": "parked", "chip": "Parked",
          "notes": "High-level only: manager-decision UX + Build 115 candidate path. No CSOne paths, customer rows, or tokens.",
        },
      ],
    },
    {
      "id": "messaging",
      "title": "Messaging / Bob infra",
      "projects": [
        project("Andrea_NanoBot", status="yellow",
                notes="BB AppleScript send preferred. Private API OFF. Approval-fenced sends only."),
        {"name": "Telegram Bot the Bot", "status": "green", "chip": "Green",
         "notes": "Whisper + Chrome mop shipped. Bob front-door via Telegram."},
        {"name": "Codex local goals", "status": "green", "chip": "Green",
         "notes": "goals=true fixed. Local Codex loop for AdoptIQ/ops lanes."},
      ],
    },
    {
      "id": "music-producer",
      "title": "Music producer",
      "projects": [
        {"name": "Logic Pro home", "status": "green", "chip": "Green",
         "notes": "Logic Pro 12.3.1 on Mac mini -- primary producer home."},
        {"name": "LogicProMCP", "status": "jeff-gate", "chip": "Jeff-gate",
         "notes": "Installed. Pending Accessibility / Automation GUI grants from Jeff."},
        {"name": "Moises / Suno", "status": "jeff-gate", "chip": "Jeff-gate",
         "notes": "Jeff-login gated for stems/covers/demos."},
      ],
    },
    {
      "id": "parked",
      "title": "Parked",
      "projects": [
        project("story-corner-shelf", status="parked",
                notes="Parked. Open PRs ignored unless Jeff unparks."),
        {"name": "Stalemate / Trailer Swift", "status": "parked", "chip": "Parked",
         "notes": "Catalog/voice feel only -- parked catalog lane."},
        {"name": "RadDadSite #6", "status": "parked", "chip": "Parked",
         "url": "https://github.com/rupret007/RadDadSite/pull/6",
         "notes": "Draft prod deploy -- keep parked pending Che/server facts."},
      ],
    },
    {
      "id": "active-agents",
      "title": "Active agents NOW",
      "projects": [
        {"name": "AdoptIQ Cloud Agent", "status": "yellow", "chip": "Yellow",
         "notes": "Cloud Agent + Build 115 path in flight elsewhere."},
        {"name": "Local Codex fix", "status": "yellow", "chip": "Yellow",
         "notes": "AdoptIQ local Codex continuation in progress."},
      ],
    },
  ],
  "fetched_repos": [x.get("name") for x in fetched if x.get("accessible")],
  "inaccessible": [x.get("name") for x in fetched if not x.get("accessible")],
  "publish_notes": "Public repo -- no secrets, no CSOne customer paths, AdoptIQ high-level only.",
  "refresh_started_ms": refresh_started_ms,
}
status["sections"] = merge_first_class(status["sections"])

# --- Active agents (Codex / Cursor / Claude) — safe public fields only ---
import os
AGENT_IDS = ("codex", "cursor", "claude")
AGENT_STATE_CHIP = {
    "running": ("green", "Running"),
    "idle": ("yellow", "Idle"),
    "installed": ("parked", "Installed"),
    "down": ("red", "Down"),
    "unknown": ("parked", "Unknown"),
}

def _safe_agent(raw, fallback_id):
    if not isinstance(raw, dict):
        raw = {}
    aid = str(raw.get("id") or fallback_id).strip().lower()[:32] or fallback_id
    name = str(raw.get("name") or aid.title())[:48]
    state = str(raw.get("state") or "unknown").strip().lower()
    if state not in AGENT_STATE_CHIP:
        state = "unknown"
    detail = str(raw.get("detail") or "")[:200]
    for bad in ("token", "secret", "bearer", "CSOne", "csone", "keeper", "password", "api_key", "apikey"):
        if bad.lower() in detail.lower():
            detail = "detail redacted"
            break
    checked = raw.get("checked_at")
    return {
        "id": aid,
        "name": name,
        "state": state,
        "detail": detail,
        "checked_at": checked,
    }

def _default_agents(state="unknown", detail="No Mac probe yet"):
    names = {"codex": "Codex", "cursor": "Cursor", "claude": "Claude"}
    return [_safe_agent({"id": i, "name": names[i], "state": state, "detail": detail}, i) for i in AGENT_IDS]

def _parse_agents_blob(blob):
    if blob is None:
        return None
    if isinstance(blob, str):
        blob = blob.strip()
        if not blob:
            return None
        try:
            blob = json.loads(blob)
        except Exception:
            return None
    if isinstance(blob, dict) and isinstance(blob.get("agents"), list):
        rows = blob["agents"]
    elif isinstance(blob, list):
        rows = blob
    else:
        return None
    by = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        a = _safe_agent(row, str(row.get("id") or "agent"))
        by[a["id"]] = a
    out = []
    for i in AGENT_IDS:
        out.append(by.get(i) or _safe_agent({"id": i, "name": i.title(), "state": "unknown", "detail": "missing from probe"}, i))
    for k, v in by.items():
        if k not in AGENT_IDS:
            out.append(v)
    return out

def _agents_fresh(agents, max_age_sec=45 * 60):
    if not agents:
        return False
    newest = None
    for a in agents:
        ts = a.get("checked_at") if isinstance(a, dict) else None
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=ZoneInfo("America/Chicago"))
            epoch = t.timestamp()
            newest = epoch if newest is None else max(newest, epoch)
        except Exception:
            continue
    if newest is None:
        return False
    return (time.time() - newest) < max_age_sec

prev_early = {}
try:
    prev_early = json.loads((root / "status.json").read_text())
except Exception:
    prev_early = {}

agents = None
src = None
env_blob = os.environ.get("AGENTS_STATUS_JSON")
if env_blob:
    agents = _parse_agents_blob(env_blob)
    src = "env:AGENTS_STATUS_JSON"
if agents is None:
    for cand in (root / "agents-status.json", Path("/workspace/bob-ops-dashboard/agents-status.json")):
        if cand.is_file():
            try:
                agents = _parse_agents_blob(cand.read_text())
                src = f"file:{cand}"
                break
            except Exception:
                continue
if agents is None and isinstance(prev_early, dict):
    prev_agents = prev_early.get("agents")
    if isinstance(prev_agents, list) and prev_agents:
        parsed = _parse_agents_blob(prev_agents)
        if parsed and _agents_fresh(parsed):
            agents = parsed
            src = "previous:<45m"
        elif parsed:
            agents = [
                _safe_agent(
                    {**a, "state": "unknown", "detail": ((a.get("detail") or "stale") + " · probe stale (>45m)")},
                    a.get("id") or "agent",
                )
                for a in parsed
            ]
            src = "previous:stale->unknown"
if agents is None:
    agents = _default_agents("unknown", "No Mac probe yet -- run probe-agents-status.sh")
    src = "default:unknown"

status["agents"] = agents
status["agents_source"] = src

agent_projects = []
for a in agents:
    st, label = AGENT_STATE_CHIP.get(a["state"], AGENT_STATE_CHIP["unknown"])
    notes = a.get("detail") or ""
    if a.get("checked_at"):
        notes = (notes + f" · checked {a['checked_at']}").strip(" ·")
    agent_projects.append({
        "name": a["name"],
        "status": st,
        "chip": label,
        "notes": notes,
        "agent_id": a["id"],
        "agent_state": a["state"],
    })
agent_projects.append({
    "name": "AdoptIQ Cloud Agent",
    "status": "yellow",
    "chip": "Yellow",
    "notes": "Cloud Agent + Build 115 path in flight elsewhere (Cursor Cloud). High-level only.",
})
for sec in status["sections"]:
    if sec.get("id") == "active-agents":
        sec["projects"] = agent_projects
        sec["title"] = "Active agents NOW"
        break

# Decisions inbox (needed before first paint -- Jeff should see pending immediately)
prev = prev_early if isinstance(prev_early, dict) else {}
if drop_leftover_verify(prev):
    print("ignored previous verify challenge (public board, no OTP)")
if drop_leftover_verify(status):
    print("drop leftover verify: public board has no OTP gate")

decisions = []
if isinstance(prev.get("decisions"), list):
    decisions = [d for d in prev["decisions"] if isinstance(d, dict)]

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
    pid = m.group(2).lower()
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
        "id": "webjam-exploratory",
        "title": "WebJam tip exploratory click-through",
        "kind": "jeff-gate",
        "detail": "You walk the installed tip; Bob keeps the checklist ready.",
        "risk": "low",
    },
    {
        "id": "dashboard-refresh",
        "title": "Force dashboard refresh + push",
        "kind": "ops",
        "detail": "Safe. Rebuilds status from live gh and pushes Pages.",
        "risk": "low",
    },
    {
        "id": "adoptiq-cloud-relaunch",
        "title": "Relaunch AdoptIQ Cloud Agent",
        "kind": "cloud-agent",
        "detail": "Needs Cursor on-demand on. Stays inside $10 hard ceiling.",
        "risk": "medium",
    },
    {
        "id": "codex-goal-launch",
        "title": "Launch next Codex goal on Mac",
        "kind": "codex-launch",
        "detail": "High risk. Bob still shows the exact goal before acting.",
        "risk": "high",
    },
    {
        "id": "text-send",
        "title": "Send a drafted text via Andrea",
        "kind": "text-send",
        "detail": "High risk. Never auto-send. Draft must already exist.",
        "risk": "high",
    },
]

pending_out = [s for s in standing if s["id"] not in resolved]
if isinstance(prev.get("pending"), list):
    for item in prev["pending"]:
        if not isinstance(item, dict):
            continue
        iid = str(item.get("id") or "").lower()
        if not iid or iid in resolved or any(s["id"] == iid for s in pending_out):
            continue
        pending_out.append(item)

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
    return (
        f'<div class="pending-item" data-id="{h(pid)}" data-title="{h(title)}">'
        f'<div class="pending-head"><div class="ptitle">{h(title)}</div>'
        f'<span class="prisk {h(risk)}">{h(risk)}</span></div>'
        f'<div class="pdetail">{h(detail)}</div>'
        f'<div class="prow">'
        f'<button type="button" data-dec="APPROVE">Approve</button>'
        f'<button type="button" class="warn" data-dec="HOLD">Hold</button>'
        f'<button type="button" class="danger" data-dec="DENY">Deny</button>'
        f'</div></div>'
    )

def pending_shell(items):
    rows = [pending_item_html(it) for it in sort_pending(items)]
    rows = [r for r in rows if r]
    hidden = "" if rows else " hidden"
    return (
        f'<div id="pending-box" class="pending-box"{hidden}>'
        f'<p class="pending-help">Public board -- Approve opens a GitHub issue as <code>rupret007</code>.</p>'
        f'<div id="pending-list">{"".join(rows)}</div></div>'
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

def lane_html(p):
    chip_label = visible_chip(p)
    chip = chip_html(p.get("status") or "parked", chip_label) if chip_label else ""
    title = h(p.get("name") or "project")
    url = safe_href(p.get("url"))
    title_html = f'<a href="{h(url)}" target="_blank" rel="noopener">{title}</a>' if url else title
    signal = compact_signal(p)
    signal_html = f'<span class="signal">{h(signal)}</span>' if signal else ""
    note = short_note(p.get("notes") or "", 88)
    notes_html = f'<p class="notes">{h(note)}</p>' if note else ""
    quiet = " is-quiet" if is_quiet_lane(p) else ""
    return (
        f'<article class="lane{quiet}">'
        f'<h3>{title_html}</h3>'
        f'<div class="lane-end">{chip}{signal_html}</div>'
        f'{notes_html}</article>'
    )

def lanes_html(projects, *, sort_attention=False):
    rows = [p for p in (projects or []) if isinstance(p, dict)]
    if sort_attention:
        rows = sorted(rows, key=attention_rank)
    return '<div class="lanes">' + "".join(lane_html(p) for p in rows) + "</div>"

sections_html = []
control_projects = []

def agents_strip_html(agents_list):
    pills = []
    for a in agents_list or []:
        st, label = AGENT_STATE_CHIP.get(a.get("state"), AGENT_STATE_CHIP["unknown"])
        chip = chip_html(st, label)
        name = h(a.get("name") or a.get("id") or "agent")
        detail = h(a.get("detail") or "")
        aid = h(a.get("id") or a.get("name") or "agent")
        pills.append(
            f'<div class="agent-pill" data-agent-id="{aid}" title="{detail}">'
            f'<span class="name">{name}</span>{chip}</div>'
        )
    return (
        '<div class="agents-strip" id="agents-strip">'
        + "".join(pills)
        + "</div>"
    )

for sec in status["sections"]:
    sid_raw = str(sec.get("id") or "")
    kind = presentation(sid_raw)
    if kind == "pulse":
        continue
    if sid_raw == "controls":
        control_projects = list(sec.get("projects") or [])
        items = status.get("pending") or []
        hidden_sec = "" if items else " hidden"
        heading = f'<h2>{h(sec.get("title") or "Decisions")}</h2>'
        body = pending_shell(items)
        sections_html.append(
            f'<section id="{h(sid_raw)}" class="block pending"{hidden_sec}>'
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
    sections_html.append(
        f'<section id="{h(sid_raw)}" class="block {cls}">{heading}{body}</section>'
    )


html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
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
  .wrap {{ max-width:40rem; margin:0 auto; padding:1rem 1rem 3.25rem; }}
  header.pulse {{ padding:0 0 1rem; margin:0 0 1.35rem; border:0; background:transparent; }}
  header.pulse h1 {{ margin:0; font-size:1.05rem; font-weight:700; letter-spacing:-.01em; }}
  header.pulse h1 .mark {{ color:var(--orange); }}
  .pulse-row {{ display:flex; flex-direction:column; gap:.55rem; margin-top:.7rem; }}
  .chip {{ display:inline-flex; align-items:center; color:var(--c);
    background:transparent; border:0; padding:0; font-size:.68rem; font-weight:700;
    text-transform:uppercase; letter-spacing:.04em; white-space:nowrap; }}
  section.block {{ margin:0 0 1.75rem; padding:0; border:0; }}
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
  .lanes {{ display:flex; flex-direction:column; }}
  .lane {{
    display:grid; grid-template-columns:minmax(0,1fr) auto; column-gap:.75rem; row-gap:.15rem;
    padding:.72rem 0; border:0; border-bottom:1px solid var(--hair); background:transparent; border-radius:0;
  }}
  .lane:last-child {{ border-bottom:0; }}
  .lane h3 {{ margin:0; font-size:.95rem; font-weight:600; letter-spacing:-.01em; }}
  .lane-end {{ display:flex; align-items:center; gap:.45rem; justify-self:end; }}
  .lane .signal {{ color:var(--muted); font-size:.75rem; font-variant-numeric:tabular-nums; }}
  .lane .notes {{
    grid-column:1 / -1; margin:0; color:var(--muted); font-size:.78rem; line-height:1.35;
    display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:1; overflow:hidden;
  }}
  .lane.is-quiet .notes {{ display:none; }}
  code {{ background:var(--panel2); padding:.05rem .35rem; border-radius:6px; font-size:.78rem; }}
  footer {{ margin-top:1.25rem; padding-top:1rem; border-top:1px solid var(--hair); color:var(--muted); font-size:.75rem; }}
  .status {{ font-size:.78rem; min-height:1.1em; margin-top:.4rem; color:var(--muted); }}
  .status.ok {{ color:var(--ok); }}
  .status.bad {{ color:#f87171; }}
  .status.warn {{ color:var(--warn); }}
  .status.hint {{ color:var(--muted); }}
  .pending-box {{ margin:0; }}
  .pending-box[hidden] {{ display:none !important; }}
  .pending-help {{ margin:0 0 .7rem; color:var(--muted); font-size:.8rem; line-height:1.4; }}
  .pending-item {{
    border:0; border-bottom:1px solid var(--hair); border-radius:0;
    padding:.65rem 0; margin:0; background:transparent;
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
  .pending-item button, .tools button {{
    background:transparent; color:var(--text); border:1px solid var(--border);
    border-radius:8px; padding:.4rem .35rem; font-size:.78rem; cursor:pointer;
    min-height:44px; touch-action:manipulation; width:100%;
  }}
  .pending-item button:hover, .tools button:hover {{ border-color:var(--orange); color:var(--orange); }}
  .pending-item button.warn {{ border-color:#ca8a04; }}
  .pending-item button.danger {{ border-color:#dc2626; color:#fecaca; }}
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
    color:#fecaca; font-size:.88rem; font-weight:600; line-height:1.35;
  }}
  #silence-banner.show {{ display:block; }}
  #board {{ min-height:2rem; }}
  #active-agents {{ margin:0; }}
  .agents-strip {{ display:flex; flex-wrap:wrap; gap:.55rem .85rem; align-items:center; margin:0; padding:0; border:0; background:transparent; }}
  .agent-pill {{ display:inline-flex; align-items:center; gap:.35rem; border:0; background:transparent; padding:0; }}
  .agent-pill .name {{ font-weight:600; font-size:.8rem; }}
  .tools {{ display:flex; flex-wrap:wrap; gap:.45rem; margin:0 0 .85rem; }}
  .how-board, .abilities-foot {{ margin:0; }}
  .how-board summary, .abilities-foot summary {{
    cursor:pointer; font-size:.85rem; font-weight:600; color:var(--muted);
    padding:.2rem 0; list-style:outside disclosure-closed;
  }}
  .how-board[open] summary, .abilities-foot[open] summary {{ color:var(--orange); margin-bottom:.55rem; }}
  @media (min-width:720px) {{
    .wrap {{ padding:1.5rem 1.25rem 3.75rem; }}
    .lane .notes {{ -webkit-line-clamp:2; }}
    .pending-item .pdetail {{
      display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; overflow:hidden;
      color:var(--muted); font-size:.78rem; margin:.15rem 0 0;
    }}
    .lane.is-quiet .notes {{ display:-webkit-box; }}
    section.pending h2, section.primary h2 {{ font-size:1.85rem; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <header class="pulse">
    <h1><span class="mark">Bob</span> Ops</h1>
    <div class="pulse-row">
      <div class="live-stamp" id="live-stamp" data-generated-at="{h(updated_iso)}" data-display="{h(updated_ct)}"><span class="live-dot" id="live-dot" aria-hidden="true"></span><span id="freshness">Live - starting</span><span class="when"> · <strong id="updated-display">{h(updated_ct)}</strong></span></div>
      <div id="active-agents">{agents_strip_html(status.get("agents"))}</div>
    </div>
    <div class="status hint" id="panel-status"></div>
  </header>
  <div id="silence-banner" role="alert" aria-live="assertive"></div>
  <div id="board">
  {''.join(sections_html)}
  </div>
  <footer>
    <p><a href="https://github.com/rupret007/bob-ops-dashboard">rupret007/bob-ops-dashboard</a>
    · <a href="./status.json">status.json</a></p>
    <p id="fetched-line">Live CI via <code>gh</code>: {h(', '.join(status.get('fetched_repos') or []))}.</p>
  </footer>
</div>
<script>
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

  function openDecisionIssue(verb, id, title) {{
    if (verb !== "APPROVE" && verb !== "DENY" && verb !== "HOLD") return;
    var pid = String(id || "").trim();
    if (!/^[a-zA-Z0-9._-]+$/.test(pid)) {{
      setStatus("Bad pending id", "bad");
      return;
    }}
    var key = verb + ":" + pid;
    if (decideBusy[key]) return;
    decideBusy[key] = 1;
    setTimeout(function () {{ delete decideBusy[key]; }}, 2000);
    var t = "BOB-" + verb + ": " + pid;
    var body = [
      "Dashboard control decision",
      "",
      "id: " + pid,
      "title: " + String(title || pid).slice(0, 160),
      "decision: " + verb.toLowerCase(),
      "from: public board",
      "at: " + new Date().toISOString(),
      "",
      "Submit this issue while logged in as rupret007. That GitHub login is the real yes.",
      "Bob: treat this as a one-shot inbox item. High-risk still needs the draft shown in chat before acting."
    ].join(String.fromCharCode(10));
    var url = "https://github.com/rupret007/bob-ops-dashboard/issues/new?title=" +
      encodeURIComponent(t) + "&body=" + encodeURIComponent(body);
    window.open(url, "_blank", "noopener");
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
    rows.forEach(function (it) {{
      var div = document.createElement("div");
      div.className = "pending-item";
      div.setAttribute("data-id", String(it.id));
      div.setAttribute("data-title", String(it.title || it.id));
      div.innerHTML =
        '<div class="pending-head"><div class="ptitle"></div><span class="prisk"></span></div>' +
        '<div class="pdetail"></div>' +
        '<div class="prow">' +
          '<button type="button" data-dec="APPROVE">Approve</button>' +
          '<button type="button" class="warn" data-dec="HOLD">Hold</button>' +
          '<button type="button" class="danger" data-dec="DENY">Deny</button>' +
        '</div>';
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
      els.list.appendChild(div);
    }});
  }}

  function loadPending() {{
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
    var decBtn = ev.target.closest("button[data-dec]");
    if (!decBtn) return;
    ev.preventDefault();
    if (decBtn.disabled) return;
    var item = decBtn.closest(".pending-item");
    if (!item) return;
    decBtn.disabled = true;
    openDecisionIssue(decBtn.getAttribute("data-dec"), item.getAttribute("data-id"), item.getAttribute("data-title"));
    setTimeout(function () {{ decBtn.disabled = false; }}, 2000);
  }});

  var CONTROL_ACTIONS = {{ "refresh-hint": 1, "open-repo": 1, "mark-reviewed": 1 }};
  function handleJeffAction(act) {{
    if (!CONTROL_ACTIONS[act]) return;
    if (act === "refresh-hint") {{
      var cmd = "./refresh.sh --push";
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(cmd).then(function () {{
          setStatus("Copied: " + cmd, "ok");
        }});
      }} else {{
        setStatus(cmd, "ok");
      }}
    }} else if (act === "open-repo") {{
      window.open("https://github.com/rupret007/bob-ops-dashboard", "_blank", "noopener");
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
  window.addEventListener("bob-ops-painted", function () {{
    loadPending();
  }});
}})();

(function () {{
  // Near-realtime: poll status.json every 30s; paint board when generated_at changes (no full reload).
  var POLL_MS = 30000;
  var stamp = document.getElementById("live-stamp");
  var freshness = document.getElementById("freshness");
  var dot = document.getElementById("live-dot");
  var displayEl = document.getElementById("updated-display");
  if (!stamp || !freshness) return;

  var known = stamp.getAttribute("data-generated-at") || "";
  var knownMs = Date.parse(known) || Date.now();
  var lastPollOk = Date.now();

  function fmtAge(ms) {{
    var s = Math.max(0, Math.floor(ms / 1000));
    if (s < 8) return "Live - just now";
    if (s < 60) return "Live - updated " + s + "s ago";
    var m = Math.floor(s / 60);
    if (m < 60) return "Live - updated " + m + "m ago";
    var h = Math.floor(m / 60);
    return "Live - updated " + h + "h ago";
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
    if (typeof updateSilence === "function") updateSilence();
  }}

  // Actions cadence ~15m; silence = max(45m, 3x cadence) - uptime-pulse pattern.
  var EXPECTED_REFRESH_MS = 15 * 60 * 1000;
  var SILENCE_LIMIT_MS = Math.max(45 * 60 * 1000, 3 * EXPECTED_REFRESH_MS);
  var silenceEl = document.getElementById("silence-banner");
  var boardEl = document.getElementById("board");
  var fetchedLine = document.getElementById("fetched-line");
  var pollFailStreak = 0;

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
    if (s.indexOf("./") === 0 && s.indexOf(":") === -1 && !/[\\s<>"']/.test(s)) return s;
    var low = s.toLowerCase();
    if (low.indexOf("https://") !== 0 && low.indexOf("http://") !== 0) return "";
    if (/[\\s<>"']/.test(s)) return "";
    return s;
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
  function compactSignal(p) {{
    if (!p) return "";
    var rel = String(p.release || "").trim();
    if (rel) return rel;
    if (typeof p.open_prs === "number" && isFinite(p.open_prs) && p.open_prs > 0) {{
      return p.open_prs + (p.open_prs === 1 ? " open PR" : " open PRs");
    }}
    var ci = p.ci;
    if (ci && typeof ci === "object") {{
      var concl = String(ci.conclusion || "").toLowerCase();
      if (concl === "failure") return "CI fail";
      if (concl && concl !== "success" && concl !== "skipped" && concl !== "cancelled") return concl;
    }}
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
  function pendingShell(items) {{
    var rank = {{ high: 0, medium: 1, low: 2 }};
    var rows = (items || []).filter(function (it) {{
      return it && /^[a-zA-Z0-9._-]+$/.test(String(it.id || ""));
    }}).slice().sort(function (a, b) {{
      var ra = rank.hasOwnProperty(String(a.risk || "").toLowerCase()) ? rank[String(a.risk).toLowerCase()] : 5;
      var rb = rank.hasOwnProperty(String(b.risk || "").toLowerCase()) ? rank[String(b.risk).toLowerCase()] : 5;
      return ra - rb;
    }});
    var list = "";
    rows.forEach(function (it) {{
      var risk = String(it.risk || "low").toLowerCase();
      if (risk !== "high" && risk !== "medium") risk = "low";
      list += '<div class="pending-item" data-id="' + esc(it.id) + '" data-title="' + esc(it.title || it.id) + '">' +
        '<div class="pending-head"><div class="ptitle">' + esc(it.title || it.id) + "</div>" +
        '<span class="prisk ' + esc(risk) + '">' + esc(risk) + "</span></div>" +
        '<div class="pdetail">' + esc(shortNote(it.detail || "", 72)) + "</div>" +
        '<div class="prow">' +
        '<button type="button" data-dec="APPROVE">Approve</button>' +
        '<button type="button" class="warn" data-dec="HOLD">Hold</button>' +
        '<button type="button" class="danger" data-dec="DENY">Deny</button></div></div>';
    }});
    return '<div id="pending-box" class="pending-box"' + (rows.length ? "" : " hidden") + ">" +
      '<p class="pending-help">Public board -- Approve opens a GitHub issue as <code>rupret007</code>.</p>' +
      '<div id="pending-list">' + list + "</div></div>";
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
    var href = safeHref(p.url);
    var titleHtml = href
      ? '<a href="' + esc(href) + '" target="_blank" rel="noopener">' + esc(title) + "</a>"
      : esc(title);
    var signal = compactSignal(p);
    var signalHtml = signal ? '<span class="signal">' + esc(signal) + "</span>" : "";
    var note = shortNote(p.notes || "", 88);
    var notesHtml = note ? '<p class="notes">' + esc(note) + "</p>" : "";
    var quiet = isQuietLane(p) ? " is-quiet" : "";
    return '<article class="lane' + quiet + '"><h3>' + titleHtml + '</h3><div class="lane-end">' +
      chip + signalHtml + "</div>" + notesHtml + "</article>";
  }}
  function lanesHtml(projects, sortAttn) {{
    var rows = (projects || []).filter(function (p) {{ return p && typeof p === "object"; }});
    if (sortAttn) rows = rows.slice().sort(function (a, b) {{ return attentionRank(a) - attentionRank(b); }});
    var html = "";
    rows.forEach(function (p) {{ html += laneHtml(p); }});
    return '<div class="lanes">' + html + "</div>";
  }}

  function showSilence(msg) {{
    if (!silenceEl) return;
    silenceEl.textContent = msg;
    silenceEl.classList.add("show");
    silenceEl.hidden = false;
  }}

  function hideSilence() {{
    if (!silenceEl) return;
    silenceEl.textContent = "";
    silenceEl.classList.remove("show");
    silenceEl.hidden = true;
  }}

  function fmtSilenceAge(ms) {{
    var m = Math.round(ms / 60000);
    if (m < 120) return "~" + m + " min";
    return "~" + (m / 60).toFixed(1) + " h";
  }}

  function updateSilence() {{
    var age = Date.now() - knownMs;
    if (pollFailStreak >= 1) {{
      showSilence("\\u26a0 status.json poll failing - board may be wrong. Retrying every 30s. Last successful poll data age: " + fmtSilenceAge(age) + ".");
      return;
    }}
    if (age > SILENCE_LIMIT_MS) {{
      var when = (displayEl && displayEl.textContent) || known || "unknown";
      showSilence("\\u26a0 Refresh has been silent since " + when + " (" + fmtSilenceAge(age) + " ago) - statuses below are outdated; repos may be up or down regardless of what this page shows.");
      return;
    }}
    if (pollFailStreak === 0) hideSilence();
  }}

  function agentStateChip(state) {{
    var map = {{
      running: ["green", "Running"],
      idle: ["yellow", "Idle"],
      installed: ["parked", "Installed"],
      down: ["red", "Down"],
      unknown: ["parked", "Unknown"]
    }};
    var m = map[state] || map.unknown;
    return chipHtml(m[0], m[1]);
  }}

  function agentsStripHtml(agents) {{
    var pills = "";
    (agents || []).forEach(function (a) {{
      var name = a.name || a.id || "agent";
      var detail = a.detail || "";
      pills += '<div class="agent-pill" data-agent-id="' + esc(a.id || name) + '" title="' + esc(detail) + '">' +
        '<span class="name">' + esc(name) + "</span>" + agentStateChip(a.state) + "</div>";
    }});
    return '<div class="agents-strip" id="agents-strip">' + pills + "</div>";
  }}

  function renderBoard(data) {{
    if (!boardEl || !data || !Array.isArray(data.sections)) return;
    var host = document.getElementById("active-agents");
    if (host) host.innerHTML = agentsStripHtml(data.agents || []);
    var controlProjects = [];
    var html = "";
    data.sections.forEach(function (sec) {{
      var kind = sectionKind(sec.id);
      if (kind === "pulse") return;
      if (sec.id === "controls") {{
        controlProjects = sec.projects || [];
        var items = data.pending || [];
        var hide = items.length ? "" : " hidden";
        html += '<section id="controls" class="block pending"' + hide + ">" +
          "<h2>" + esc(sec.title || "Decisions") + "</h2>" + pendingShell(items) + "</section>";
        return;
      }}
      if (sec.id === "features") {{
        html += '<section id="features" class="block foot">' +
          '<details class="how-board"><summary>How this board works</summary>' +
          '<p class="pending-help">Engineer notes -- not the daily ops list.</p>' +
          '<p class="pending-help">Public board -- Approve opens a GitHub issue; submit while logged in as <code>rupret007</code>.</p>' +
          toolsRow(controlProjects) + lanesHtml(sec.projects || []) + "</details></section>";
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
      html += '<section id="' + esc(sec.id || "") + '" class="block ' + cls + '">' +
        "<h2>" + esc(sec.title || "") + "</h2>" +
        lanesHtml(sec.projects || [], kind === "primary") + "</section>";
    }});
    boardEl.innerHTML = html;
    if (fetchedLine && Array.isArray(data.fetched_repos)) {{
      fetchedLine.innerHTML = 'Live CI via <code>gh</code>: ' + esc(data.fetched_repos.join(", ")) + ".";
    }}
    window.dispatchEvent(new CustomEvent("bob-ops-painted"));
  }}

  function applyStamp(data) {{
    var next = data && data.generated_at;
    if (next) {{
      known = next;
      knownMs = Date.parse(next) || knownMs;
      stamp.setAttribute("data-generated-at", known);
    }}
    if (data && data.generated_at_display && displayEl) {{
      displayEl.textContent = data.generated_at_display;
      stamp.setAttribute("data-display", data.generated_at_display);
    }}
  }}

  var pollSeq = 0;
  function poll() {{
    var seq = ++pollSeq;
    var url = "./status.json?ts=" + Date.now();
    fetch(url, {{ cache: "no-store" }})
      .then(function (res) {{
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      }})
      .then(function (data) {{
        if (seq !== pollSeq) return;
        lastPollOk = Date.now();
        pollFailStreak = 0;
        if (dot) {{
          dot.classList.add("poll");
          setTimeout(function () {{ dot.classList.remove("poll"); }}, 600);
        }}
        var next = data && data.generated_at;
        var changed = !!(next && known && next !== known);
        applyStamp(data);
        if (changed) {{
          // Soft-paint from JSON -- no full reload. Controls/pending stay public.
          renderBoard(data);
        }}
        paint();
        updateSilence();
      }})
      .catch(function () {{
        if (seq !== pollSeq) return;
        pollFailStreak += 1;
        freshness.textContent = "poll failed -- retrying";
        freshness.classList.add("stale");
        if (dot) dot.classList.add("stale");
        updateSilence();
      }});
  }}

  var pollTimer = null;
  function startPolling() {{
    if (pollTimer) clearInterval(pollTimer);
    poll();
    pollTimer = setInterval(poll, POLL_MS);
  }}
  function stopPolling() {{
    if (pollTimer) {{ clearInterval(pollTimer); pollTimer = null; }}
  }}
  document.addEventListener("visibilitychange", function () {{
    if (document.visibilityState === "hidden") stopPolling();
    else startPolling();
  }});

  paint();
  setInterval(paint, 1000);
  // Pause polls when tab hidden; resume on visible.
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

# Atomic status.json write (uptime-pulse pattern): tmp + replace.
_status_tmp = root / "status.json.tmp"
_status_tmp.write_text(json.dumps(status, indent=2) + "\n")
_status_tmp.replace(root / "status.json")
(root / "index.html").write_text(html)
print(f"Wrote {root/'index.html'} and {root/'status.json'} (atomic JSON)")
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
  [[ -f "$ROOT/test_board_meta.py" ]] && cp "$ROOT/test_board_meta.py" "$WORK/"
  [[ -f "$ROOT/test_open_decision.js" ]] && cp "$ROOT/test_open_decision.js" "$WORK/"
  # Do not commit agents-status.json by default (Mac-local probe snapshot); refresh merges it when present.
  if [[ -f "$ROOT/.github/workflows/refresh-dashboard.yml" ]]; then
    cp "$ROOT/.github/workflows/refresh-dashboard.yml" "$WORK/.github/workflows/"
  fi
  if [[ -f "$ROOT/.github/workflows/qa-claim-smoke.yml" ]]; then
    cp "$ROOT/.github/workflows/qa-claim-smoke.yml" "$WORK/.github/workflows/"
  fi
  chmod +x "$WORK/refresh.sh"
  [[ -f "$WORK/qa-claim-smoke.sh" ]] && chmod +x "$WORK/qa-claim-smoke.sh"
  cd "$WORK"
  git add index.html status.json README.md refresh.sh
  [[ -f board_meta.py ]] && git add board_meta.py
  [[ -f probe-agents-status.sh ]] && git add probe-agents-status.sh
  [[ -f qa-claim-smoke.sh ]] && git add qa-claim-smoke.sh
  [[ -f test_board_meta.py ]] && git add test_board_meta.py
  [[ -f test_open_decision.js ]] && git add test_open_decision.js
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
