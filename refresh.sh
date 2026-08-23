#!/usr/bin/env bash
# Rebuild Bob ops dashboard from live gh data, then optionally push to Pages.
# Usage:
#   ./refresh.sh              # write index.html + status.json in this dir
#   ./refresh.sh --push       # also commit+push to rupret007/bob-ops-dashboard main
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
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
echo "Fetching ${#REPOS[@]} repos as $OWNER …"
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
import json, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

root = Path(sys.argv[1])
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

# Curated narrative notes (safe / no secrets) — live SHAs/CI come from gh
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
                notes="Tip usually green; stale open PRs → hygiene."),
      ],
    },
    {
      "id": "cisco",
      "title": "Cisco work",
      "projects": [
        project("AdoptIQ", status="yellow",
                notes="Cisco CS desktop — Build 115 in progress (Codex + Cloud Agent). No secrets on this page."),
        project("TACTrack", status="yellow",
                notes="Private. High-level only — CI / open PRs from live fetch."),
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
         "notes": "Logic Pro 12.3.1 on Mac mini — primary producer home."},
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
         "notes": "Catalog/voice feel only — parked catalog lane."},
        {"name": "RadDadSite #6", "status": "parked", "chip": "Parked",
         "url": "https://github.com/rupret007/RadDadSite/pull/6",
         "notes": "Draft prod deploy — keep parked pending Che/server facts."},
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
  "publish_notes": "Public repo — no secrets, no CSOne customer paths, AdoptIQ high-level only.",
}

# HTML render (same dark mobile layout)
CHIP_COLORS = {
  "green": ("#16a34a", "#052e16", "#bbf7d0"),
  "yellow": ("#ca8a04", "#422006", "#fef08a"),
  "red": ("#dc2626", "#450a0a", "#fecaca"),
  "parked": ("#525252", "#171717", "#d4d4d4"),
  "jeff-gate": ("#d97757", "#2a1510", "#f5c4b3"),
}

def chip_html(st, label):
    border, bg, fg = CHIP_COLORS.get(st, CHIP_COLORS["parked"])
    return f'<span class="chip" style="--c:{border};--bg:{bg};--fg:{fg}">{label}</span>'

def ci_badge(ci):
    if not ci:
        return '<span class="meta">No Actions</span>'
    concl = ci.get("conclusion") or "unknown"
    color = {"success":"#16a34a","failure":"#dc2626","cancelled":"#64748b"}.get(concl, "#ca8a04")
    return f'<span class="ci" style="color:{color}">● {ci.get("name","CI")}: {concl}</span>'

sections_html = []
for sec in status["sections"]:
    cards = []
    for p in sec["projects"]:
        chip = chip_html(p.get("status","parked"), p.get("chip", "?"))
        title = p["name"]
        url = p.get("url")
        title_html = f'<a href="{url}" target="_blank" rel="noopener">{title}</a>' if url else title
        bits = []
        if p.get("tip_sha"):
            bits.append(f'<code>{p["tip_sha"]}</code>')
        if p.get("product_sha"):
            bits.append(f'product <code>{p["product_sha"]}</code>')
        if p.get("release"):
            bits.append(f'release <strong>{p["release"]}</strong>')
        if p.get("open_prs") is not None:
            n = p["open_prs"]
            bits.append(f'{n} open PR' + ("s" if n != 1 else ""))
        if p.get("private"):
            bits.append("private")
        if p.get("accessible") is False and p.get("repo"):
            bits.append("inaccessible")
        meta = " · ".join(bits)
        cards.append(f'''
        <article class="card">
          <header><h3>{title_html}</h3>{chip}</header>
          <div class="row">{meta}</div>
          <div class="row">{ci_badge(p.get("ci"))}</div>
          <p class="notes">{p.get("notes","")}</p>
        </article>''')
    sections_html.append(f'''
    <section id="{sec["id"]}">
      <h2>{sec["title"]}</h2>
      <div class="grid">{''.join(cards)}</div>
    </section>''')

html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="color-scheme" content="dark"/>
<title>Bob Ops Dashboard -- Jeff Story</title>
<style>
  :root {{
    --bg:#0a0a0a; --panel:#141414; --panel2:#1c1c1c; --text:#f5f5f5; --muted:#a3a3a3;
    --border:#2a2a2a; --accent:#d97757; --link:#e8a080; --orange:#d97757;
    --orange-dim:#2a1510; --ok:#16a34a; --warn:#ca8a04;
  }}
  * {{ box-sizing:border-box; }}
  html,body {{ margin:0; padding:0; background:var(--bg); color:var(--text);
    font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; line-height:1.45; }}
  a {{ color:var(--link); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:1.25rem 1rem 3rem; }}
  header.hero {{
    background:linear-gradient(145deg,#0a0a0a 0%,#141414 50%,#1a120e 100%);
    border:1px solid var(--border); border-radius:16px; padding:1.25rem 1.35rem; margin-bottom:1.25rem;
    box-shadow:0 0 0 1px rgba(217,119,87,.12), 0 12px 40px rgba(0,0,0,.45);
  }}
  header.hero h1 {{ margin:0 0 .35rem; font-size:clamp(1.4rem,4vw,1.85rem); }}
  header.hero h1 .mark {{ color:var(--orange); }}
  .sub {{ color:var(--muted); font-size:.95rem; }}
  .legend {{ display:flex; flex-wrap:wrap; gap:.45rem; margin-top:.9rem; }}
  .chip {{ display:inline-flex; align-items:center; gap:.35rem; border:1px solid var(--c);
    background:var(--bg); color:var(--fg); border-radius:999px; padding:.15rem .6rem;
    font-size:.72rem; font-weight:700; text-transform:uppercase; white-space:nowrap; }}
  .chip::before {{ content:""; width:.45rem; height:.45rem; border-radius:50%; background:var(--c); }}
  nav.toc {{ display:flex; flex-wrap:wrap; gap:.5rem; margin:0 0 1.25rem; }}
  nav.toc a {{ background:var(--panel); border:1px solid var(--border); color:var(--text);
    padding:.4rem .7rem; border-radius:999px; font-size:.82rem; }}
  nav.toc a:hover {{ border-color:var(--orange); color:var(--orange); }}
  section {{ margin-bottom:1.75rem; }}
  section h2 {{ margin:0 0 .75rem; font-size:1.15rem; border-left:3px solid var(--accent); padding-left:.6rem; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:.75rem; }}
  .card {{ background:var(--panel); border:1px solid var(--border); border-radius:14px;
    padding:.9rem 1rem; display:flex; flex-direction:column; gap:.35rem; min-height:140px; }}
  .card:hover {{ border-color:#3f3f3f; }}
  .card header {{ display:flex; justify-content:space-between; align-items:flex-start; gap:.5rem; }}
  .card h3 {{ margin:0; font-size:1.02rem; }}
  .row {{ color:var(--muted); font-size:.8rem; }}
  .notes {{ margin:.25rem 0 0; color:#e5e5e5; font-size:.88rem; }}
  code {{ background:var(--panel2); padding:.05rem .35rem; border-radius:6px; font-size:.78rem; }}
  .ci {{ font-weight:600; font-size:.8rem; }}
  footer {{ margin-top:2rem; padding-top:1rem; border-top:1px solid var(--border); color:var(--muted); font-size:.82rem; }}
  .banner {{ background:var(--orange-dim); border:1px solid rgba(217,119,87,.45); color:#f5c4b3;
    border-radius:12px; padding:.75rem 1rem; margin-bottom:1rem; font-size:.88rem; }}
  .auth {{
    margin-top:1rem; padding:1rem; border-radius:12px; border:1px solid rgba(217,119,87,.35);
    background:#101010;
  }}
  .auth h2 {{ margin:0 0 .4rem; font-size:1rem; border:0; padding:0; color:var(--orange); }}
  .auth p {{ margin:.25rem 0 .75rem; color:var(--muted); font-size:.85rem; }}
  .auth-row {{ display:flex; flex-wrap:wrap; gap:.5rem; align-items:center; margin-bottom:.5rem; }}
  .auth input[type="email"], .auth input[type="text"] {{
    flex:1 1 180px; min-width:160px; background:#0a0a0a; color:var(--text);
    border:1px solid var(--border); border-radius:8px; padding:.5rem .65rem; font-size:.9rem;
  }}
  .auth input:focus {{ outline:1px solid var(--orange); border-color:var(--orange); }}
  .auth button {{
    background:var(--orange); color:#0a0a0a; border:0; border-radius:8px;
    padding:.5rem .9rem; font-weight:700; font-size:.85rem; cursor:pointer;
  }}
  .auth button.secondary {{ background:transparent; color:var(--orange); border:1px solid var(--orange); }}
  .auth button:disabled {{ opacity:.45; cursor:not-allowed; }}
  .auth .status {{ font-size:.85rem; min-height:1.2em; }}
  .auth .status.ok {{ color:var(--ok); }}
  .auth .status.bad {{ color:#f87171; }}
  .auth .status.warn {{ color:var(--warn); }}
  .actions {{ margin-top:.75rem; display:none; gap:.5rem; flex-wrap:wrap; }}
  .actions.open {{ display:flex; }}
  .actions button {{
    background:#1c1c1c; color:var(--text); border:1px solid var(--border);
    border-radius:8px; padding:.45rem .75rem; font-size:.82rem; cursor:pointer;
  }}
  .actions button:hover {{ border-color:var(--orange); color:var(--orange); }}
  body:not(.jeff-verified) .gated {{ opacity:.55; }}
  body.jeff-verified .gated {{ opacity:1; box-shadow:inset 0 0 0 1px rgba(217,119,87,.25); }}
</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <h1><span class="mark">Bob</span> Ops Dashboard</h1>
    <div class="sub">Projects Bob is working on for Jeff Story · live music/apps focus · closet parked</div>
    <div class="sub" style="margin-top:.35rem">Last updated: <strong>{updated_ct}</strong></div>
    <div class="legend">
      {chip_html("green","Green")}{chip_html("yellow","Yellow")}{chip_html("red","Red")}{chip_html("parked","Parked")}{chip_html("jeff-gate","Jeff-gate")}
    </div>
    <div class="auth" id="auth-panel">
      <h2>Jeff verify gate</h2>
      <p>Phase 1: client allowlist + mailto challenge (server mailer next). Verified Jeff unlocks gated actions on this device.</p>
      <div class="auth-row">
        <input id="jeff-email" type="email" placeholder="jeff@example.com" autocomplete="email"/>
        <button type="button" id="btn-send-challenge">Verify</button>
        <button type="button" id="btn-sign-out" class="secondary" style="display:none">Sign out</button>
      </div>
      <div class="auth-row" id="code-row" style="display:none">
        <input id="jeff-code" type="text" placeholder="Paste challenge code from email" autocomplete="one-time-code"/>
        <button type="button" id="btn-confirm-code">Unlock</button>
      </div>
      <div class="status" id="auth-status"></div>
      <div class="actions" id="jeff-actions">
        <button type="button" data-action="refresh-hint">Copy refresh command</button>
        <button type="button" data-action="open-repo">Open dashboard repo</button>
        <button type="button" data-action="mark-reviewed">Mark board reviewed</button>
      </div>
    </div>
  </header>
  <div class="banner">Public status page -- no secrets, tokens, CSOne customer paths, or private handoff text. AdoptIQ is high-level Cisco CS desktop summary only. Theme: Claude orange (#d97757) on black.</div>
  <nav class="toc">
    <a href="#live-shipping">Live shipping</a><a href="#cisco">Cisco</a><a href="#messaging">Messaging</a>
    <a href="#music-producer">Music producer</a><a href="#parked">Parked</a><a href="#active-agents">Active agents</a>
  </nav>
  {''.join(sections_html)}
  <footer>
    <p>Source: <a href="https://github.com/rupret007/bob-ops-dashboard">rupret007/bob-ops-dashboard</a>
    · <a href="./status.json">status.json</a> · Refresh: <code>./refresh.sh</code> (weekday morning).</p>
    <p>Live CI via <code>gh</code>: {', '.join(status.get('fetched_repos') or [])}.</p>
  </footer>
</div>
<script>
(function () {{
  // Phase-1 client allowlist. Discovered from GitHub primary public email for rupret007.
  // Server-side mailer is next; until then mailto challenge is local-only.
  var ALLOWLIST = ["jeffstory007@gmail.com"];
  var STORAGE_KEY = "bobOpsJeffAuth_v1";
  var CHALLENGE_KEY = "bobOpsJeffChallenge_v1";

  var emailEl = document.getElementById("jeff-email");
  var codeEl = document.getElementById("jeff-code");
  var statusEl = document.getElementById("auth-status");
  var codeRow = document.getElementById("code-row");
  var actions = document.getElementById("jeff-actions");
  var btnSend = document.getElementById("btn-send-challenge");
  var btnConfirm = document.getElementById("btn-confirm-code");
  var btnOut = document.getElementById("btn-sign-out");

  function norm(e) {{ return (e || "").trim().toLowerCase(); }}
  function setStatus(msg, kind) {{
    statusEl.textContent = msg || "";
    statusEl.className = "status" + (kind ? " " + kind : "");
  }}
  function randCode() {{
    var a = new Uint8Array(4);
    (window.crypto || window.msCrypto).getRandomValues(a);
    var s = "";
    for (var i = 0; i < a.length; i++) s += ("0" + a[i].toString(16)).slice(-2);
    return s.toUpperCase();
  }}
  function loadAuth() {{
    try {{ return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"); }}
    catch (e) {{ return null; }}
  }}
  function saveAuth(obj) {{
    if (!obj) localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, JSON.stringify(obj));
  }}
  function applyVerified(auth) {{
    var ok = !!(auth && auth.email && auth.verifiedAt);
    document.body.classList.toggle("jeff-verified", ok);
    actions.classList.toggle("open", ok);
    btnOut.style.display = ok ? "" : "none";
    if (ok) {{
      emailEl.value = auth.email;
      emailEl.disabled = true;
      btnSend.disabled = true;
      codeRow.style.display = "none";
      setStatus("Verified as " + auth.email + " -- gated actions unlocked on this device.", "ok");
      document.querySelectorAll(".card").forEach(function (c) {{
        var chip = c.querySelector(".chip");
        if (chip && /jeff-gate/i.test(chip.textContent)) c.classList.add("gated");
      }});
    }} else {{
      emailEl.disabled = false;
      btnSend.disabled = false;
      document.querySelectorAll(".card.gated").forEach(function (c) {{ c.classList.remove("gated"); }});
    }}
  }}

  btnSend.addEventListener("click", function () {{
    var email = norm(emailEl.value);
    if (!email || email.indexOf("@") < 0) {{
      setStatus("Enter a valid email.", "bad");
      return;
    }}
    if (ALLOWLIST.indexOf(email) < 0) {{
      setStatus("Email not on Jeff allowlist. Ask Bob to add it after confirm.", "bad");
      return;
    }}
    var code = randCode();
    var payload = {{ email: email, code: code, createdAt: Date.now() }};
    sessionStorage.setItem(CHALLENGE_KEY, JSON.stringify(payload));
    var subject = encodeURIComponent("Bob Ops verify code " + code);
    var body = encodeURIComponent(
      "Jeff -- your Bob Ops Dashboard challenge code is:\n\n" + code +
      "\n\nPaste this code into the dashboard Unlock field.\n" +
      "(Phase 1 mailto challenge; server mailer next.)\n"
    );
    window.location.href = "mailto:" + encodeURIComponent(email) + "?subject=" + subject + "&body=" + body;
    codeRow.style.display = "flex";
    setStatus("Mailto opened. Paste the code from the draft/sent mail to unlock.", "warn");
  }});

  btnConfirm.addEventListener("click", function () {{
    var typed = (codeEl.value || "").trim().toUpperCase();
    var raw = sessionStorage.getItem(CHALLENGE_KEY);
    if (!raw) {{ setStatus("No pending challenge. Click Verify first.", "bad"); return; }}
    var ch;
    try {{ ch = JSON.parse(raw); }} catch (e) {{ setStatus("Challenge corrupt. Retry Verify.", "bad"); return; }}
    if (!typed || typed !== String(ch.code || "").toUpperCase()) {{
      setStatus("Code mismatch. Check the mailto draft.", "bad");
      return;
    }}
    if (Date.now() - (ch.createdAt || 0) > 30 * 60 * 1000) {{
      setStatus("Challenge expired. Click Verify again.", "bad");
      return;
    }}
    var auth = {{ email: ch.email, verifiedAt: new Date().toISOString() }};
    saveAuth(auth);
    sessionStorage.removeItem(CHALLENGE_KEY);
    codeEl.value = "";
    applyVerified(auth);
  }});

  btnOut.addEventListener("click", function () {{
    saveAuth(null);
    sessionStorage.removeItem(CHALLENGE_KEY);
    codeRow.style.display = "none";
    setStatus("Signed out. Gated actions locked.", "warn");
    applyVerified(null);
  }});

  actions.addEventListener("click", function (ev) {{
    var btn = ev.target.closest("button[data-action]");
    if (!btn || !document.body.classList.contains("jeff-verified")) return;
    var act = btn.getAttribute("data-action");
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
  }});

  applyVerified(loadAuth());
}})();
</script>
</body>
</html>
'''

(root / "status.json").write_text(json.dumps(status, indent=2) + "\n")
(root / "index.html").write_text(html)
print(f"Wrote {root/'index.html'} and {root/'status.json'}")
print(f"Updated: {updated_ct}")
print(f"Fetched OK: {status['fetched_repos']}")
if status.get("inaccessible"):
    print(f"Inaccessible: {status['inaccessible']}")
PY

rm -f "$TMP"

# Keep README tip current
if ! grep -q 'refresh.sh' "$ROOT/README.md" 2>/dev/null; then
  printf '\n## refresh.sh\n\n```bash\n./refresh.sh          # rebuild locally\n./refresh.sh --push   # rebuild + push to Pages\n```\n' >> "$ROOT/README.md"
fi

if [[ $PUSH -eq 1 ]]; then
  WORK="$(mktemp -d)"
  gh repo clone "$OWNER/bob-ops-dashboard" "$WORK" -- --quiet
  cp "$ROOT/index.html" "$ROOT/status.json" "$ROOT/README.md" "$ROOT/refresh.sh" "$WORK/"
  chmod +x "$WORK/refresh.sh"
  cd "$WORK"
  git add index.html status.json README.md refresh.sh
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
