#!/usr/bin/env bash
# Fail-closed smoke for bob-ops-dashboard claims before Bob says "good".
# Public board: no OTP / Unlock gate. XSS/race guards and first-class sections stay.
# Usage: ./qa-claim-smoke.sh [path-to-index.html]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
INDEX="${1:-$ROOT/index.html}"
STATUS="${STATUS_JSON:-$ROOT/status.json}"
REFRESH="${REFRESH_SH:-$ROOT/refresh.sh}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

command -v node >/dev/null || fail "node required"
command -v python3 >/dev/null || fail "python3 required"
[[ -f "$INDEX" ]] || fail "missing $INDEX"
[[ -f "$ROOT/qa-source-only.sh" ]] || fail "missing source-only QA wrapper"

# 1) Extract inline scripts and node --check
python3 - "$INDEX" "$TMP" <<'PY'
import re, sys
from pathlib import Path
html = Path(sys.argv[1]).read_text()
out = Path(sys.argv[2])
scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
if not scripts:
    raise SystemExit("no script blocks")
for i, s in enumerate(scripts):
    (out / f"s{i}.js").write_text(s)
print(len(scripts))
PY

for f in "$TMP"/s*.js; do
  node --check "$f" || fail "node --check $f"
done
pass "inline JS syntax"

# 2) ASCII-safe scripts: no smart quotes and no raw non-ASCII (use \\uXXXX)
if ! python3 - "$TMP" <<'PY'
from pathlib import Path
import sys
bad = False
for p in Path(sys.argv[1]).glob("s*.js"):
    t = p.read_text()
    if any(ord(c) in (0x2018, 0x2019, 0x201C, 0x201D) for c in t):
        print("smart-quote", p.name)
        bad = True
    non = sorted({hex(ord(c)) for c in t if ord(c) > 127})
    if non:
        print("non-ascii", p.name, ",".join(non))
        bad = True
sys.exit(1 if bad else 0)
PY
then fail "smart quotes or non-ASCII in JS"
else
  pass "ASCII-safe JS (no smart quotes)"
fi

# 3) Dead OTP / Unlock paths must be gone (fail-closed, no half-state UI)
python3 - "$INDEX" "$REFRESH" "$ROOT" <<'PY' || fail "OTP / Unlock leftovers"
from pathlib import Path
import sys
index, refresh, root = map(Path, sys.argv[1:4])
html = index.read_text()
sh = refresh.read_text()
blob = html + "\n" + sh
banned_fn = (
    "function doUnlock",
    "async function doUnlock",
    "function loadUnlockStatus",
    "function hasUnlockVerify",
    "function applyVerified",
    "function loadAuth",
    "function saveAuth",
    "function sha256Hex",
)
for n in banned_fn:
    if n in blob:
        raise SystemExit("banned function still present: " + n)
for n in (
    'id="jeff-code"',
    'id="btn-confirm-code"',
    'id="btn-sign-out"',
    'id="auth-panel"',
    ">Unlock<",
    "Jeff verify",
    "ask-code",
    "How to ask",
    "Ask Bob for a new code",
    "Email Unlock codes",
    "6-digit code",
    "one-time code",
    "verified device",
    "Unlocked on this phone",
    "No code active yet",
    "That code expired",
    "body.jeff-verified",
    "localStorage.getItem",
):
    if n in blob:
        # getItem is only banned if used for the retired auth key; checked below.
        if n == "localStorage.getItem":
            continue
        raise SystemExit("banned UI/path still present: " + n)
if "localStorage.getItem" in blob and "bobOpsJeffAuth" in blob.split("localStorage.getItem", 1)[1][:80]:
    raise SystemExit("retired auth key is still read")
if "bobOpsJeffAuth_v1" in blob and "removeItem" not in blob:
    raise SystemExit("retired auth key present without removeItem cleanup")
for dead in (
    "preserve_verify.py",
    "test_preserve_verify.py",
    "test_unlock_fallback.js",
    "issue-dashboard-code.py",
    "issue_and_prepare_email.py",
):
    if (root / dead).exists():
        raise SystemExit("dead OTP helper still in repo: " + dead)
if "from preserve_verify" in sh or "apply_preserved_verify" in sh:
    raise SystemExit("refresh.sh still imports preserve_verify")
print("no OTP / Unlock leftovers")
PY
pass "no OTP / Unlock leftovers"

# 4) Required public-board + anti-regression symbols
grep -q 'function openDecisionIssue' "$INDEX" || fail "openDecisionIssue missing"
grep -q 'function decisionHref' "$INDEX" || fail "decisionHref missing"
grep -q 'function openBlank' "$INDEX" || fail "openBlank missing"
grep -q 'pageshow' "$INDEX" || fail "pageshow resume missing"
grep -q 'AbortController' "$INDEX" || fail "poll AbortController missing"
grep -q 'function pollIsNewer' "$INDEX" || fail "pollIsNewer missing"
grep -q 'function pollFailureCounts' "$INDEX" || fail "pollFailureCounts missing"
grep -q 'function pollPaintDecision' "$INDEX" || fail "pollPaintDecision missing"
grep -q 'pollSeq += 1' "$INDEX" || fail "stopPolling must bump pollSeq before abort"
grep -q 'noopener noreferrer' "$INDEX" || fail "decision links missing noreferrer"
grep -q 'function safeHref' "$INDEX" || fail "safeHref missing"
grep -q 'function safeAgentUrl' "$INDEX" || fail "safeAgentUrl missing"
grep -q 'function laneHrefs' "$INDEX" || fail "laneHrefs missing"
grep -q 'function signalHref' "$INDEX" || fail "signalHref missing"
grep -q 'function safePullsUrl' "$INDEX" || fail "safePullsUrl missing"
grep -q 'function safeGameUrl' "$INDEX" || fail "safeGameUrl missing"
grep -q 'function safeGameUrl' "$REFRESH" || fail "refresh.sh safeGameUrl missing"
grep -q 'concl === "skipped"' "$INDEX" || fail "laneHrefs must drop skipped Open CI"
grep -q 'concl === "skipped"' "$REFRESH" || fail "refresh.sh laneHrefs must drop skipped Open CI"
grep -q 'data-open="work"' "$REFRESH" || fail "refresh.sh missing work-link taps"
grep -q 'Open agent' "$REFRESH" || fail "refresh.sh missing Open agent"
grep -q 'Open repo' "$REFRESH" || fail "refresh.sh missing Open repo"
grep -q 'Open CI' "$REFRESH" || fail "refresh.sh missing Open CI"
grep -q 'Play game' "$REFRESH" || fail "refresh.sh missing Play game"
grep -q 'function handleWorkClick' "$INDEX" || fail "handleWorkClick missing"
grep -q 'function openWorkLink' "$INDEX" || fail "openWorkLink missing"
grep -q 'window.openBlank = openBlank' "$INDEX" || fail "openBlank not exposed"
grep -q 'pollSeq' "$INDEX" || fail "pollSeq missing"
grep -q 'decideBusy' "$INDEX" || fail "decideBusy missing"
grep -q 'pendingSeq' "$INDEX" || fail "pendingSeq missing"
grep -q 'function boardFingerprint' "$INDEX" || fail "boardFingerprint missing"
grep -q 'function ageGateAgents' "$INDEX" || fail "ageGateAgents missing"
grep -q 'data-checked-at' "$INDEX" || fail "agent data-checked-at missing"
grep -q 'id="pending-box"' "$INDEX" || fail "pending-box missing"
grep -q 'class="pulse"' "$INDEX" || fail "pulse strip missing"
grep -q 'class="lane"' "$INDEX" || fail "compact lanes missing"
grep -q 'class="abilities-foot"' "$INDEX" || fail "abilities footer collapse missing"
grep -q 'Public board -- Approve opens a GitHub issue' "$INDEX" || fail "public-board note missing"
grep -q 'BOB-APPROVE' "$INDEX" || fail "BOB-APPROVE missing"
grep -q 'How this board works' "$INDEX" || fail "collapsed how-board missing"
grep -q 'class="how-board"' "$INDEX" || fail "how-board details missing"
grep -q 'section.block' "$INDEX" || fail "section.block spacing missing"
if grep -q 'class="card"' "$INDEX"; then
  fail "essay cards still on page -- lanes only"
fi
if grep -q 'nav class="toc"' "$INDEX" || grep -q '<nav class="toc">' "$INDEX"; then
  fail "TOC chip forest still on page"
fi
if grep -q 'class="legend"' "$INDEX"; then
  fail "legend chrome still on default board"
fi
if grep -q 'class="banner"' "$INDEX"; then
  fail "banner chrome still on default board"
fi
if grep -q 'No Actions' "$INDEX"; then
  fail "redundant No Actions chrome still on page"
fi
if grep -q 'Nothing pending' "$INDEX"; then
  fail "empty-pending chrome still on page"
fi
if grep -q 'How to ask' "$INDEX" || grep -q 'Ask Bob for a new code' "$INDEX"; then
  fail "ask-code leftover still on page"
fi
pass "public-board markers present"

# 5) Generator must keep XSS/race guards and drop verify
[[ -f "$REFRESH" ]] || fail "missing $REFRESH"
grep -q 'html.escape' "$REFRESH" || fail "refresh.sh missing html.escape"
grep -q 'def safe_href' "$REFRESH" || fail "refresh.sh missing safe_href"
grep -q 'function safeHref' "$REFRESH" || fail "refresh.sh missing JS safeHref"
grep -q 'pollSeq' "$REFRESH" || fail "refresh.sh missing pollSeq"
grep -q 'decideBusy' "$REFRESH" || fail "refresh.sh missing decideBusy"
grep -q 'drop_leftover_verify' "$REFRESH" || fail "refresh.sh missing drop_leftover_verify"
grep -q 'merge_first_class' "$REFRESH" || fail "refresh.sh missing merge_first_class"
grep -q 'resolve_agents' "$REFRESH" || fail "refresh.sh missing resolve_agents"
grep -q 'status_from_fetch' "$REFRESH" || fail "refresh.sh missing status_from_fetch"
grep -q 'pending-more' "$REFRESH" || fail "refresh.sh missing pending-more"
grep -q 'boardFingerprint' "$REFRESH" || fail "refresh.sh missing boardFingerprint"
grep -q 'ageGateAgents' "$REFRESH" || fail "refresh.sh missing ageGateAgents"
grep -q 'decisionHref' "$REFRESH" || fail "refresh.sh missing decisionHref"
grep -q 'pick_tip_ci' "$REFRESH" || fail "refresh.sh missing pick_tip_ci"
grep -q 'actions/runs?per_page=20&branch=' "$REFRESH" || fail "refresh.sh must fetch default-branch CI only"
grep -q 'is_ci_noise' "$ROOT/board_meta.py" || fail "board_meta.py missing is_ci_noise"
grep -q 'CI pending' "$REFRESH" || fail "refresh.sh missing CI pending signal"
grep -q 'AbortController' "$REFRESH" || fail "refresh.sh missing AbortController"
grep -q 'pollIsNewer' "$REFRESH" || fail "refresh.sh missing pollIsNewer"
grep -q 'pollFailureCounts' "$REFRESH" || fail "refresh.sh missing pollFailureCounts"
grep -q 'pollPaintDecision' "$REFRESH" || fail "refresh.sh missing pollPaintDecision"
grep -q 'pollSeq += 1' "$REFRESH" || fail "refresh.sh stopPolling must bump pollSeq"
grep -q 'pageshow' "$REFRESH" || fail "refresh.sh missing pageshow"
grep -q 'safe_agent_url' "$ROOT/board_meta.py" || fail "board_meta.py missing safe_agent_url"
grep -q 'lane_hrefs' "$ROOT/board_meta.py" || fail "board_meta.py missing lane_hrefs"
grep -q 'signal_href' "$ROOT/board_meta.py" || fail "board_meta.py missing signal_href"
grep -q 'safe_pulls_url' "$ROOT/board_meta.py" || fail "board_meta.py missing safe_pulls_url"
grep -q 'function signalHref' "$REFRESH" || fail "refresh.sh missing signalHref"
grep -q 'function safePullsUrl' "$REFRESH" || fail "refresh.sh missing safePullsUrl"
if grep -q '<span class="signal">1 open PR</span>' "$INDEX"; then
  fail "1 open PR signal is still dead text"
fi
if grep -q '<span class="signal">4 open PRs</span>' "$INDEX"; then
  fail "N open PRs signal is still dead text"
fi
grep -q 'merge_cloud_agents' "$REFRESH" || fail "refresh.sh missing merge_cloud_agents"
grep -q 'pick_open_pr' "$REFRESH" || fail "refresh.sh missing pick_open_pr"
grep -q 'prune_closed_parked_prs' "$REFRESH" || fail "refresh.sh missing parked-PR lifecycle guard"
if grep -q 'RadDadSite #6' "$REFRESH"; then
  fail "refresh.sh still presents closed RadDadSite #6 as current work"
fi
grep -q 'Private-content boundary hold. Keep private; do not publish catalog content.' "$REFRESH" || fail "refresh.sh missing private-content boundary hold"
if grep -q 'Private docs/index' "$REFRESH"; then
  fail "refresh.sh leaked private catalog path detail"
fi
grep -q 'function handleWorkClick' "$REFRESH" || fail "refresh.sh missing handleWorkClick"
grep -q 'function openWorkLink' "$REFRESH" || fail "refresh.sh missing openWorkLink"
grep -q 'window.openBlank = openBlank' "$REFRESH" || fail "refresh.sh must expose openBlank"
grep -q 'BOB_DASHBOARD_APPLY_DECISIONS:-0' "$REFRESH" || fail "decision mutation must default off"
grep -q 'if apply_decisions:' "$REFRESH" || fail "issue close/comment path must be explicitly gated"
if grep -R -q 'BOB_DASHBOARD_APPLY_DECISIONS.*1' "$ROOT/.github/workflows" 2>/dev/null; then
  fail "scheduled workflow must not enable issue close/comment mutations"
fi
if grep -q 'from preserve_verify' "$REFRESH"; then
  fail "refresh.sh still imports preserve_verify"
fi
python3 - "$REFRESH" <<'PY' || fail "smart quotes in refresh.sh"
from pathlib import Path
import sys
t = Path(sys.argv[1]).read_text()
if any(ord(c) in (0x2018, 0x2019, 0x201C, 0x201D) for c in t):
    raise SystemExit("smart quotes")
PY
pass "refresh.sh guards present (XSS/races + public board)"

# 6) Unit tests
[[ -f "$ROOT/test_board_meta.py" ]] || fail "missing test_board_meta.py"
python3 "$ROOT/test_board_meta.py" -v || fail "board_meta unit tests"
pass "board_meta unit tests"
[[ -f "$ROOT/test_open_decision.js" ]] || fail "missing test_open_decision.js"
node "$ROOT/test_open_decision.js" "$INDEX" || fail "open-decision smoke"
pass "open-decision smoke"
[[ -f "$ROOT/test_soft_paint.js" ]] || fail "missing test_soft_paint.js"
node "$ROOT/test_soft_paint.js" "$INDEX" || fail "soft-paint / agent age-gate smoke"
pass "soft-paint / agent age-gate smoke"
[[ -f "$ROOT/test_open_links.js" ]] || fail "missing test_open_links.js"
node "$ROOT/test_open_links.js" "$INDEX" || fail "open-links smoke"
pass "open-links smoke"

# 7) status.json must not carry a verify challenge
if [[ -f "$STATUS" ]]; then
python3 - "$STATUS" "$INDEX" <<'PY' || fail "status.json still has verify or private-media detail"
import json, sys
st = json.load(open(sys.argv[1]))
if not isinstance(st, dict):
    raise SystemExit("status.json must be an object")
if "verify" in st:
    raise SystemExit("status.json.verify present -- public board forbids OTP hashes")
ctrl = st.get("control") or {}
if "unlock" in str(ctrl).lower() or "verified ui" in str(ctrl).lower():
    raise SystemExit("control metadata still claims a verify gate")
if ctrl.get("jeff_github") != "rupret007":
    raise SystemExit("control.jeff_github must stay rupret007")
required_lanes = {
    "WebJam", "Story Shelf", "AdoptIQ", "StoryOps-AI", "Ball Beacon",
    "CSS Conductor", "TACTrack", "Barker", "StoryBoard", "StoryDesk",
    "Andrea NanoBot", "Bob the Bot", "OpenClaw Runtime", "StoryLiner",
    "AI Music Vault", "Bob Ops Dashboard", "RadDadSite", "Rad Dad Merch",
    "Cursor-OpenClaw Integration", "Show Night",
    "Sliding Glass Door Screw", "Turdanoid", "Private media",
}
projects = [
    p
    for sec in (st.get("sections") or [])
    if isinstance(sec, dict)
    for p in (sec.get("projects") or [])
    if isinstance(p, dict)
]
names = {str(p.get("name") or "") for p in projects}
missing = sorted(required_lanes - names)
if missing:
    raise SystemExit("dashboard missing inherited lanes: " + ", ".join(missing))
turdanoid = [p for p in projects if p.get("name") == "Turdanoid"]
if len(turdanoid) != 1:
    raise SystemExit("dashboard must expose exactly one Turdanoid lane")
turdanoid = turdanoid[0]
if turdanoid.get("status") not in {"yellow", "red"}:
    raise SystemExit("Turdanoid gameplay-improvement lane must remain visibly open")
if turdanoid.get("live_game_url") != "https://rupret007.github.io/Turdanoid/hub.html":
    raise SystemExit("Turdanoid lane missing the allowlisted public game hub")
if "remains open" not in str(turdanoid.get("notes") or "").lower():
    raise SystemExit("Turdanoid lane must not claim the gameplay pass is complete")
bob_rows = [
    (str(sec.get("id") or ""), p)
    for sec in (st.get("sections") or [])
    if isinstance(sec, dict)
    for p in (sec.get("projects") or [])
    if isinstance(p, dict) and p.get("name") == "Bob the Bot"
]
if len(bob_rows) != 1 or bob_rows[0][0] != "messaging":
    raise SystemExit("Bob the Bot must be one distinct messaging application lane")
bob = bob_rows[0][1]
if not bob.get("private") or bob.get("status") not in {"yellow", "red"}:
    raise SystemExit("Bob the Bot must remain a private active-bootstrap lane")
expected_bob_note = (
    "Bob application — private bootstrap. Reuses the Andrea messaging engine and "
    "guarded OpenClaw delegation; no live sends, restarts, credentials, or production actions."
)
if bob.get("notes") != expected_bob_note:
    raise SystemExit("Bob the Bot public note drifted from its guarded boundary")
for distinct in ("Andrea NanoBot", "OpenClaw Runtime", "Bob Ops Dashboard"):
    if sum(1 for p in projects if p.get("name") == distinct) != 1:
        raise SystemExit("Bob application boundary lost distinct lane: " + distinct)
media_sections = [
    sec for sec in (st.get("sections") or [])
    if isinstance(sec, dict) and sec.get("id") == "private-media"
]
expected_media = {
    "name": "Private media",
    "status": "jeff-gate",
    "chip": "Owner-only",
    "notes": "Private-content boundary. Upload and publishing stay owner-only.",
}
if len(media_sections) != 1:
    raise SystemExit("dashboard must have exactly one private-media section")
if media_sections[0].get("title") != "Private media":
    raise SystemExit("private-media section title must stay generic")
if media_sections[0].get("projects") != [expected_media]:
    raise SystemExit("private-media section must expose only the generic owner gate")
parked_sections = [
    sec for sec in (st.get("sections") or [])
    if isinstance(sec, dict) and sec.get("id") == "parked"
]
expected_parked_catalog = {
    "name": "Parked private catalog",
    "status": "parked",
    "chip": "Parked",
    "notes": "Private-content boundary. Parked catalog work stays private and owner-only.",
}
if len(parked_sections) != 1 or parked_sections[0].get("projects") != [expected_parked_catalog]:
    raise SystemExit("parked private catalog must expose only the generic owner gate")
abilities = next(
    (sec for sec in (st.get("sections") or []) if isinstance(sec, dict) and sec.get("id") == "abilities"),
    {},
)
media_abilities = [
    row for row in (abilities.get("projects") or [])
    if isinstance(row, dict) and row.get("name") == "Private media boundary"
]
if media_abilities != [{
    "name": "Private media boundary",
    "status": "jeff-gate",
    "chip": "Owner-only",
    "notes": "Private content stays high-level. Upload and publishing require an explicit owner decision.",
}]:
    raise SystemExit("private-media ability must stay a generic owner gate")
private_pending = [
    row for row in (st.get("pending") or [])
    if isinstance(row, dict) and row.get("id") == "private-media-upload"
]
expected_private_pending = {
    "id": "private-media-upload",
    "title": "Private media upload or publish",
    "kind": "owner-upload-gate",
    "detail": "Private content stays high-level; upload and publishing require an explicit owner decision.",
    "risk": "high",
}
if len(private_pending) > 1 or (private_pending and private_pending != [expected_private_pending]):
    raise SystemExit("private-media pending item must stay a generic owner gate")
from pathlib import Path
public_blob = Path(sys.argv[1]).read_text() + "\n" + Path(sys.argv[2]).read_text()
private_markers = tuple(
    bytes.fromhex(encoded).decode("ascii")
    for encoded in (
        "6f7068656c6961",  # private project name
        "6d6f69736573",    # media provider
        "73756e6f",        # media provider
        "6c6f6769632070726f",  # media provider
        "6c6f67696370726f6d6370",  # provider integration
        "7374656d",        # private asset type/count detail
        "7374616c656d617465",  # private creative title
        "747261696c6572207377696674",  # private creative title
        "766f696365206665656c",  # private catalog detail
    )
)
for marker in private_markers[:5] + private_markers[6:]:
    if marker in public_blob.lower():
        raise SystemExit("generated public artifacts leaked private-media detail")
import re
if re.search(rf"\b{re.escape(private_markers[5])}s?\b", public_blob, re.I):
    raise SystemExit("generated public artifacts leaked private-media asset detail")
private_sensitive = (
    "repo", "url", "repo_url", "default_branch", "tip_sha", "tip_date",
    "product_sha", "open_prs", "open_pr_url", "open_pr_number",
    "open_pr_draft", "pr_listing_complete", "agent_url", "live_game_url", "release",
)
for p in projects:
    if not p.get("private"):
        continue
    leaked = [key for key in private_sensitive if p.get(key) not in (None, "", [], {})]
    if p.get("open_pr_stack") not in (None, []):
        leaked.append("open_pr_stack")
    ci = p.get("ci") if isinstance(p.get("ci"), dict) else {}
    if set(ci) - {"conclusion"}:
        leaked.append("ci metadata")
    if leaked:
        raise SystemExit(
            "private lane leaked public metadata (" + ", ".join(leaked) + "): "
            + str(p.get("name"))
        )
    if p.get("status") == "red":
        raise SystemExit(
            "private lane must not diagnose hosted CI as red: " + str(p.get("name"))
        )
html = Path(sys.argv[2]).read_text()
for p in projects:
    if not p.get("private"):
        continue
    name = str(p.get("name") or "")
    article = re.search(
        rf'<article class="lane[^"]*"><h3>{re.escape(name)}</h3>.*?</article>',
        html,
    )
    if not article:
        continue
    if any(sig in article.group(0) for sig in ("CI fail", "CI pending", "CI running")):
        raise SystemExit("private lane published a CI diagnosis signal: " + name)
agents = st.get("agents") if isinstance(st, dict) else None
if isinstance(agents, list):
    for a in agents:
        if not isinstance(a, dict):
            raise SystemExit("agent row must be an object")
        if str(a.get("id") or "") not in ("codex", "cursor", "claude"):
            raise SystemExit("invented agent id: " + str(a.get("id")))
        if str(a.get("state") or "") == "running" and not a.get("checked_at"):
            raise SystemExit("Running without checked_at is invented status")
cloud = st.get("cloud_agents") if isinstance(st, dict) else None
if isinstance(cloud, list):
    for a in cloud:
        if not isinstance(a, dict):
            raise SystemExit("cloud agent row must be an object")
        url = str(a.get("url") or "")
        if "https://cursor.com/agents/bc-" not in url:
            raise SystemExit("cloud agent missing real cursor.com/agents/bc- url")
        if str(a.get("state") or "") == "running":
            raise SystemExit("cloud agent invented Running")
        bc = url.rsplit("/", 1)[-1].split("?")[0].lower()
        if str(a.get("id") or "").lower() != bc:
            raise SystemExit("cloud agent id must match url bc-id")
print("status.json has no verify")
PY
  pass "status.json has no verify"
else
  echo "SKIP: status.json missing"
fi

python3 - "$ROOT/README.md" "$REFRESH" "$ROOT/docs" <<'PY' \
  || fail "README / private-lane live-repo honesty"
from pathlib import Path
import re
import sys

readme, refresh, docs = map(Path, sys.argv[1:4])
text = readme.read_text()
backed = re.search(
    r"GitHub-backed rows use live repository, default-branch CI, and open-PR state for [^.]+",
    text,
)
if not backed:
    raise SystemExit("README missing GitHub-backed live-repo sentence")
public_tap_lanes = (
    "WebJam",
    "Turdanoid",
    "Show Night",
    "Story Shelf",
    "StoryBoard",
    "Andrea NanoBot",
    "StoryLiner",
    "Bob Ops Dashboard",
    "RadDadSite",
    "Rad Dad Merch",
    "Cursor-OpenClaw Integration",
)
private_not_tap = (
    "AdoptIQ",
    "StoryOps-AI",
    "Ball Beacon",
    "CSS Conductor",
    "TACTrack",
    "0xc0re/barker",
    "AI Music Vault",
    "Sliding Glass Door Screw",
    "Bob the Bot",
    "Barker",
)
missing_public = [name for name in public_tap_lanes if name not in backed.group(0)]
if missing_public:
    raise SystemExit(
        "README GitHub-backed sentence missing public tap lane: " + ", ".join(missing_public)
    )
listed_private = [name for name in private_not_tap if name in backed.group(0)]
if listed_private:
    raise SystemExit(
        "README GitHub-backed sentence must not list private/high-level lanes as "
        "live-repo tap rows: " + ", ".join(listed_private)
    )
coverage = text.split("## Portfolio coverage", 1)[-1].split("## Public agent continuity", 1)[0]
if "Bob the Bot" not in coverage or "high-level only" not in coverage:
    raise SystemExit("README must keep Bob the Bot high-level only in portfolio coverage")
if "not a public live-repo, CI, or PR tap row" not in coverage:
    raise SystemExit("README must say Bob the Bot is not a public live-repo tap row")
if "private GitHub lanes stay **high-level only**" not in coverage:
    raise SystemExit("README must say private GitHub lanes are not public tap rows")
if 'project("Bob-the-Bot", status="yellow", high_level_only=True,' not in refresh.read_text():
    raise SystemExit("Bob the Bot must stay high_level_only in refresh.sh")
refresh_text = refresh.read_text()
if "Live CI and review state only" in refresh_text:
    raise SystemExit("StoryOps-AI note still claims live CI / review state")
if "CI / open PRs from live fetch" in refresh_text:
    raise SystemExit("TACTrack note still claims live CI / open-PR fetch")
if "High-level only; no customer data on this board." not in refresh_text:
    raise SystemExit("StoryOps-AI must stay a high-level private utility note")
if "No live-repo, CI, or PR taps on this board." not in refresh_text:
    raise SystemExit("TACTrack must stay a high-level private note with no tap claim")
if "hosted billing blocks CI" in refresh_text or "billing blocks CI" in refresh_text:
    raise SystemExit("CSS Conductor note must not diagnose hosted CI as a billing block")
if "High-level only; hosted-job cause stays unconfirmed." not in refresh_text:
    raise SystemExit("CSS Conductor must stay a high-level note with unconfirmed hosted-job cause")
if "high_level=bool(r.get(\"private\") or high_level_only)" not in refresh_text:
    raise SystemExit("refresh.sh must keep private/high-level lanes off hosted-CI diagnosis")
if "if (p.private) return \"\";" not in refresh_text:
    raise SystemExit("refresh.sh JS compactSignal must hide private-lane CI diagnosis")
sys.path.insert(0, str(refresh.parent))
from board_meta import compact_signal, status_from_fetch
private_fail = {
    "accessible": True,
    "private": True,
    "ci": {"conclusion": "failure"},
}
if status_from_fetch(private_fail, high_level=True) == "red":
    raise SystemExit("private/high-level lanes must not paint hosted CI as red")
if compact_signal({"private": True, "ci": {"conclusion": "failure"}}) == "CI fail":
    raise SystemExit("private/high-level lanes must not publish a CI fail signal")
for md in [readme, *sorted(docs.rglob("*.md"))]:
    for match in re.finditer(
        r"GitHub-backed rows use live repository, default-branch CI, and open-PR state for [^.]+",
        md.read_text(),
    ):
        listed = [name for name in private_not_tap if name in match.group(0)]
        if listed:
            raise SystemExit(
                f"{md} lists private/high-level lanes as GitHub-backed tap rows: "
                + ", ".join(listed)
            )
print("Private/high-level lanes stay off the GitHub-backed tap sentence")
PY
pass "README / private-lane notes do not claim live-repo taps"

# 8) Functional XSS helpers from extracted page JS
HELPER_JS="$TMP/helper-smoke.js"
cat > "$HELPER_JS" <<'JS'
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
function extract(name) {
  const start = src.indexOf("function " + name + "(");
  if (start < 0) throw new Error("missing " + name);
  let i = src.indexOf("{", start);
  if (i < 0) throw new Error("unopened " + name);
  let depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error("unclosed " + name);
}
eval(extract("esc"));
eval(extract("safeHref"));
eval(extract("cleanPublicUrl"));
eval(extract("safeGameUrl"));
if (esc("<img onerror=x>") !== "&lt;img onerror=x&gt;") throw new Error("esc lt");
if (esc('"') !== "&quot;") throw new Error("esc quote");
if (safeHref("javascript:alert(1)") !== "") throw new Error("js url");
if (safeHref("data:text/html,x") !== "") throw new Error("data url");
if (safeHref("//evil.example") !== "") throw new Error("proto-relative");
if (safeHref("https://github.com/rupret007/webjam") !== "https://github.com/rupret007/webjam") {
  throw new Error("https url");
}
if (safeHref('https://x.com/"onclick="') !== "") throw new Error("quoted url");
if (safeHref("./status.json") !== "./status.json") throw new Error("relative url");
if (safeHref("./evil:foo") !== "") throw new Error("colon relative");
if (safeHref("./ok\\\\x") !== "") throw new Error("backslash relative");
if (safeGameUrl("https://rupret007.github.io/Turdanoid/hub.html?from=board") !== "https://rupret007.github.io/Turdanoid/hub.html") {
  throw new Error("live game url");
}
if (safeGameUrl("https://rupret007.github.io/Turdanoid/") !== "") throw new Error("broad pages url");
if (safeGameUrl("https://evil.example/Turdanoid/hub.html") !== "") throw new Error("evil game url");
if (!src.includes("pollSeq") || !src.includes("decideBusy")) {
  throw new Error("guards missing in extracted JS");
}
if (src.includes("function doUnlock") || src.includes("unlockBusy")) {
  throw new Error("Unlock helpers leaked into extracted JS");
}
console.log("helpers ok");
JS
HELPER_SRC=""
for f in "$TMP"/s*.js; do
  if grep -q 'function safeHref' "$f" && grep -q 'function esc' "$f"; then
    HELPER_SRC="$f"
    break
  fi
done
[[ -n "$HELPER_SRC" ]] || fail "could not find safeHref/esc script"
node "$HELPER_JS" "$HELPER_SRC" || fail "safeHref/esc behavior"
pass "safeHref/esc behavior"

# 9) Python HTML helpers replica (must stay fail-closed)
python3 - <<'PY' || fail "python html helpers"
import html
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
assert "&lt;img" in h('<img onerror="x">')
assert "&quot;" in h('"')
assert safe_href("javascript:alert(1)") == ""
assert safe_href("https://github.com/rupret007/webjam") == "https://github.com/rupret007/webjam"
assert safe_href('https://x.com/"x') == ""
assert safe_href("./status.json") == "./status.json"
assert safe_href("./evil:foo") == ""
assert safe_href("./ok\\foo") == ""
print("py helpers ok")
PY
pass "python html helpers"

# 10) e2e: leftover verify must be stripped by refresh.sh
if command -v gh >/dev/null; then
  PRIVATE_E2E="$TMP/private-e2e"
  MOCK_BIN="$PRIVATE_E2E/bin"
  mkdir -p "$MOCK_BIN"
  cp "$ROOT/refresh.sh" "$ROOT/board_meta.py" "$ROOT/README.md" "$PRIVATE_E2E/"
  chmod +x "$PRIVATE_E2E/refresh.sh"
  python3 - "$MOCK_BIN/gh" <<'PY' || fail "private fixture setup failed"
from pathlib import Path
import sys

Path(sys.argv[1]).write_text(r'''#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
if args[:1] == ["api"] and len(args) >= 2:
    path = args[1]
    if path == "repos/rupret007/AI-Music-Vault":
        value = {
            "name": "AI-Music-Vault",
            "full_name": "rupret007/AI-Music-Vault",
            "private": True,
            "html_url": "https://github.com/rupret007/AI-Music-Vault",
            "default_branch": "super-secret-branch",
        }
    elif path == "repos/rupret007/AI-Music-Vault/commits/super-secret-branch":
        value = {
            "sha": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            "commit": {
                "message": "private commit subject",
                "committer": {"date": "2026-08-25T00:00:00Z"},
            },
        }
    elif path == "repos/rupret007/AI-Music-Vault/pulls?state=open&per_page=100&page=1":
        value = [{
            "number": 77,
            "state": "open",
            "draft": True,
            "title": "private-pr-title",
            "body": "https://cursor.com/agents/bc-12345678-1234-1234-1234-123456789abc",
            "html_url": "https://github.com/rupret007/AI-Music-Vault/pull/77",
            "base": {
                "ref": "super-secret-branch",
                "repo": {"full_name": "rupret007/AI-Music-Vault"},
            },
            "head": {
                "ref": "private-feature-branch",
                "repo": {"full_name": "rupret007/AI-Music-Vault"},
            },
            "updated_at": "2026-08-25T00:00:00Z",
        }]
    elif path == "repos/rupret007/AI-Music-Vault/actions/runs?per_page=20&branch=super-secret-branch":
        value = {"workflow_runs": [{
            "name": "Private validation",
            "path": ".github/workflows/private.yml",
            "head_branch": "super-secret-branch",
            "head_sha": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://github.com/rupret007/AI-Music-Vault/actions/runs/88",
            "updated_at": "2026-08-25T00:00:00Z",
        }]}
    elif path == "repos/rupret007/AI-Music-Vault/releases?per_page=1":
        value = [{"tag_name": "private-release-v9"}]
    elif path == "repos/rupret007/Bob-the-Bot":
        value = {
            "name": "Bob-the-Bot",
            "full_name": "rupret007/Bob-the-Bot",
            "private": True,
            "html_url": "https://github.com/rupret007/Bob-the-Bot",
            "default_branch": "bob-private-main",
        }
    elif path == "repos/rupret007/Bob-the-Bot/commits/bob-private-main":
        value = {
            "sha": "b0bb0bb0bb0bb0bb0bb0bb0bb0bb0bb0bb0bb0bb",
            "commit": {
                "message": "bob-private-commit-subject",
                "committer": {"date": "2026-08-26T00:00:00Z"},
            },
        }
    elif path == "repos/rupret007/Bob-the-Bot/pulls?state=open&per_page=100&page=1":
        value = [{
            "number": 91,
            "state": "open",
            "draft": True,
            "title": "bob-private-pr-title",
            "body": "https://cursor.com/agents/bc-99999999-9999-9999-9999-999999999999",
            "html_url": "https://github.com/rupret007/Bob-the-Bot/pull/91",
            "base": {
                "ref": "bob-private-main",
                "repo": {"full_name": "rupret007/Bob-the-Bot"},
            },
            "head": {
                "ref": "bob-private-feature",
                "repo": {"full_name": "rupret007/Bob-the-Bot"},
            },
            "updated_at": "2026-08-26T00:00:00Z",
        }]
    elif path == "repos/rupret007/Bob-the-Bot/actions/runs?per_page=20&branch=bob-private-main":
        value = {"workflow_runs": [{
            "name": "Bob private validation",
            "path": ".github/workflows/bob-private.yml",
            "head_branch": "bob-private-main",
            "head_sha": "b0bb0bb0bb0bb0bb0bb0bb0bb0bb0bb0bb0bb0bb",
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://github.com/rupret007/Bob-the-Bot/actions/runs/92",
            "updated_at": "2026-08-26T00:00:00Z",
        }]}
    elif path == "repos/rupret007/Bob-the-Bot/releases?per_page=1":
        value = [{"tag_name": "bob-private-release"}]
    elif path == "repos/rupret007/bob-ops-dashboard/pulls?state=open&per_page=20":
        value = []
    else:
        value = None
    print(json.dumps(value))
    raise SystemExit(0)
if args[:2] == ["issue", "list"]:
    print("[]")
    raise SystemExit(0)
raise SystemExit(1)
''')
PY
  chmod +x "$MOCK_BIN/gh"
  (
    cd "$PRIVATE_E2E"
    PATH="$MOCK_BIN:$PATH" BOB_DASHBOARD_APPLY_DECISIONS=0 ./refresh.sh
  ) || fail "private fixture refresh failed"
  python3 - "$PRIVATE_E2E/status.json" "$PRIVATE_E2E/index.html" <<'PY' \
    || fail "private fixture leaked repository metadata"
import json
import sys
from pathlib import Path

status_path, index_path = map(Path, sys.argv[1:])
st = json.loads(status_path.read_text())
projects = [
    p
    for section in (st.get("sections") or [])
    if isinstance(section, dict)
    for p in (section.get("projects") or [])
    if isinstance(p, dict)
]
private = next((p for p in projects if p.get("name") == "AI Music Vault"), None)
if not private or not private.get("private") or not private.get("accessible"):
    raise SystemExit("fixture did not exercise an accessible private lane")
allowed = {"name", "private", "status", "chip", "notes", "accessible", "ci"}
extra = sorted(set(private) - allowed)
if extra:
    raise SystemExit("private row contains non-allowlisted keys: " + ", ".join(extra))
ci = private.get("ci") if isinstance(private.get("ci"), dict) else {}
if set(ci) - {"conclusion"} or ci.get("conclusion") != "success":
    raise SystemExit("private CI must expose conclusion only")
bob = next((p for p in projects if p.get("name") == "Bob the Bot"), None)
if not bob or not bob.get("private") or not bob.get("accessible"):
    raise SystemExit("fixture did not exercise the accessible private Bob application lane")
if set(bob) - allowed:
    raise SystemExit("Bob application row contains non-allowlisted keys")
if bob.get("status") != "yellow" or bob.get("chip") != "Yellow":
    raise SystemExit("Bob application must remain an active private bootstrap")
bob_ci = bob.get("ci") if isinstance(bob.get("ci"), dict) else {}
if set(bob_ci) - {"conclusion"} or bob_ci.get("conclusion") != "success":
    raise SystemExit("Bob application CI must expose conclusion only")
public_blob = status_path.read_text() + "\n" + index_path.read_text()
for secret in (
    "super-secret-branch", "private-feature-branch", "deadbeefdeadbeef",
    "private-pr-title", "private-release-v9", "AI-Music-Vault/pull/77",
    "AI-Music-Vault/actions/runs/88", "bc-12345678-1234-1234-1234-123456789abc",
    "bob-private-main", "bob-private-feature", "b0bb0bb0bb0b",
    "bob-private-pr-title", "bob-private-release", "Bob-the-Bot/pull/91",
    "Bob-the-Bot/actions/runs/92", "bc-99999999-9999-9999-9999-999999999999",
):
    if secret in public_blob:
        raise SystemExit("private fixture leaked: " + secret)
print("accessible private fixture stayed high-level")
PY
  pass "accessible private repository metadata stays allowlisted"

  E2E="$TMP/refresh-e2e"
  mkdir -p "$E2E"
  cp "$ROOT/refresh.sh" "$ROOT/board_meta.py" "$ROOT/README.md" "$E2E/"
  chmod +x "$E2E/refresh.sh"
  python3 - "$E2E/status.json" <<'PY' || fail "e2e seed failed"
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "generated_at": "e2e-seed",
    "decisions": [{
        "id": bytes.fromhex("6f7068656c69612d75706c6f6164").decode("ascii"),
        "decision": "hold",
        "issue": 1,
    }],
    "verify": {
        "email": "jeffstory007@gmail.com",
        "sha256": "aa" * 32,
        "exp": 9_999_999_999_999,
        "issued_at": 1,
    },
}, indent=2) + "\n")
print("seeded leftover verify")
PY
  python3 - "$E2E/agents-status.json" <<'PY' || fail "e2e stale agents seed failed"
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "agents": [
        {"id": "codex", "name": "Codex", "state": "running", "detail": "seed PID", "checked_at": "2020-01-01T00:00:00Z"},
        {"id": "cursor", "name": "Cursor", "state": "running", "detail": "seed app", "checked_at": "2020-01-01T00:00:00Z"},
        {"id": "claude", "name": "Claude", "state": "installed", "detail": "seed app", "checked_at": "2020-01-01T00:00:00Z"},
        {"id": "ghost", "name": "Ghost", "state": "running", "detail": "invented"},
    ],
    "cloud_agents": [
        {
            "name": "Seed",
            "state": "running",
            "url": "https://cursor.com/agents/bc-12345678-1234-1234-1234-123456789abc",
            "pr_url": "https://github.com/rupret007/bob-ops-dashboard/pull/8",
        },
        {"name": "Fake", "url": "https://evil.example/agents/bc-12345678-1234-1234-1234-123456789abc"},
    ],
}, indent=2) + "\n")
print("seeded stale Running agents")
PY
  (cd "$E2E" && ./refresh.sh) || fail "refresh.sh e2e failed"
  python3 - "$E2E/status.json" "$E2E/index.html" <<'PY' || fail "refresh.sh did not strip verify / public board"
import json, sys
from pathlib import Path
st = json.load(open(sys.argv[1]))
if "verify" in st:
    raise SystemExit("refresh.sh kept leftover verify")
if st.get("agents_source") != "file:stale->unknown":
    raise SystemExit("refresh.sh exposed more than the safe agents source class/state")
public_artifacts = Path(sys.argv[1]).read_text() + "\n" + Path(sys.argv[2]).read_text()
for unsafe_path in ("/home/runner", "/Users", "file:/"):
    if unsafe_path in public_artifacts:
        raise SystemExit("refresh.sh leaked an absolute agent-source path")
private_media_decisions = [
    row for row in (st.get("decisions") or [])
    if isinstance(row, dict) and row.get("id") == "private-media-upload"
]
if len(private_media_decisions) != 1:
    raise SystemExit("refresh.sh did not normalize the legacy private-media decision id")
legacy_media_name = bytes.fromhex("6f7068656c6961").decode("ascii")
if legacy_media_name in Path(sys.argv[1]).read_text().lower():
    raise SystemExit("refresh.sh retained a private project name in decision history")
ids = [s.get("id") for s in (st.get("sections") or [])]
for need in ("abilities", "controls", "features"):
    if need not in ids:
        raise SystemExit("missing section " + need)
html = Path(sys.argv[2]).read_text()
if "Public board -- Approve opens a GitHub issue" not in html:
    raise SystemExit("generated page missing public-board note")
if 'id="btn-confirm-code"' in html or ">Unlock<" in html:
    raise SystemExit("generated page still has Unlock UI")
if "function doUnlock" in html:
    raise SystemExit("generated page still has doUnlock")
if "No Actions" in html:
    raise SystemExit("generated page still has No Actions chrome")
if "how-board" not in html:
    raise SystemExit("generated page missing collapsed how-board")
if "abilities-foot" not in html:
    raise SystemExit("generated page missing collapsed abilities footer")
if "header.pulse" not in html and 'class="pulse"' not in html:
    raise SystemExit("generated page missing pulse strip")
if 'class="lane"' not in html:
    raise SystemExit("generated page missing compact lanes")
if 'class="card"' in html:
    raise SystemExit("generated page still paints essay cards")
if "pending-more" not in html:
    raise SystemExit("generated page missing collapsed lower-risk pending")
if "function boardFingerprint" not in html or "function ageGateAgents" not in html:
    raise SystemExit("generated page missing no-flash / age-gate helpers")
if "function decisionHref" not in html or "pageshow" not in html or "AbortController" not in html:
    raise SystemExit("generated page missing phone decision / poll helpers")
if "function pollIsNewer" not in html or "function pollFailureCounts" not in html:
    raise SystemExit("generated page missing poll no-flash helpers")
if "pollSeq += 1" not in html:
    raise SystemExit("generated page stopPolling must bump pollSeq")
if "function safeAgentUrl" not in html or "function laneHrefs" not in html:
    raise SystemExit("generated page missing work-link helpers")
if "function signalHref" not in html or "function safePullsUrl" not in html:
    raise SystemExit("generated page missing open-PR signal helpers")
if '<span class="signal">1 open PR</span>' in html or '<span class="signal">4 open PRs</span>' in html:
    raise SystemExit("generated page still has dead open-PR signal text")
if "function handleWorkClick" not in html or "function openWorkLink" not in html:
    raise SystemExit("generated page missing iOS work-link fallback")
if "window.openBlank = openBlank" not in html:
    raise SystemExit("generated page missing window.openBlank")
if "CI pending" not in html:
    raise SystemExit("generated page missing CI pending signal")
if 'rel="noopener noreferrer"' not in html:
    raise SystemExit("generated page missing noreferrer decision links")
if "Open agent" not in html:
    raise SystemExit("generated page missing Open agent tap")
if "RadDadSite #6" in html or "RadDadSite #6" in str(st):
    raise SystemExit("closed RadDadSite #6 leaked into generated current work")
if "Private docs/index" in html or "Private docs/index" in str(st):
    raise SystemExit("generated board leaked private catalog path detail")
boundary = "Private-content boundary hold. Keep private; do not publish catalog content."
if boundary not in html or boundary not in str(st):
    raise SystemExit("generated board missing private-content boundary hold")
if "lane-links" not in html and "data-open=\"work\"" not in html:
    raise SystemExit("generated page missing work links")
agents = st.get("agents") or []
if any(str(a.get("state") or "") == "running" for a in agents):
    raise SystemExit("stale seed Running leaked into agents")
if any(str(a.get("id") or "") not in ("codex", "cursor", "claude") for a in agents):
    raise SystemExit("invented extra agent id leaked")
if any(str(a.get("state") or "") != "unknown" for a in agents):
    raise SystemExit("stale probe must fail closed to unknown")
cloud = st.get("cloud_agents") or []
seed_present = any(
    str(a.get("url") or "") == "https://cursor.com/agents/bc-12345678-1234-1234-1234-123456789abc"
    for a in cloud
    if isinstance(a, dict)
)
if seed_present:
    raise SystemExit("probe-only cloud agent survived without an open same-repo PR binding")
if any("evil.example" in str(a.get("url") or "") for a in cloud if isinstance(a, dict)):
    raise SystemExit("evil cloud agent URL leaked")
if any(str(a.get("state") or "") == "running" for a in cloud if isinstance(a, dict)):
    raise SystemExit("cloud agent invented Running")
names = []
for sec in st.get("sections") or []:
    if sec.get("id") == "active-agents":
        names = [p.get("name") for p in (sec.get("projects") or [])]
if "AdoptIQ Cloud Agent" in names:
    raise SystemExit("invented Cloud Agent yellow card leaked into pulse data")
if "Running" in html.split('id="active-agents"', 1)[-1].split('id="silence-banner"', 1)[0]:
    raise SystemExit("pulse strip still claims Running from stale seed")
print("refresh.sh e2e stripped leftover verify")
PY
  pass "refresh.sh e2e stripped leftover verify"
else
  echo "SKIP: refresh.sh e2e (gh not available)"
fi

# 11) Pulse + pending + live lanes; abilities / how-board collapsed
for id in abilities controls features live-shipping active-agents; do
  grep -q "id=\"$id\"" "$INDEX" || fail "index.html missing #$id"
done
grep -q 'id="active-agents"' "$REFRESH" || fail "refresh.sh missing agents pulse host"
grep -q 'class="abilities-foot"' "$REFRESH" || fail "refresh.sh missing collapsed abilities"
python3 - "$INDEX" "$STATUS" <<'PY' || fail "hierarchy / collapsed honesty"
from pathlib import Path
import json, sys
html = Path(sys.argv[1]).read_text()
st = json.loads(Path(sys.argv[2]).read_text()) if Path(sys.argv[2]).is_file() else {}
for needle in ("Decisions", "Live shipping", "How this board works", "No send button", "no order button"):
    if needle not in html:
        raise SystemExit("missing " + needle)
if "<details" not in html or "how-board" not in html:
    raise SystemExit("features must be collapsed details")
if "abilities-foot" not in html:
    raise SystemExit("abilities must be collapsed details")
# Soft-paint / engineer cards must not lead the default phone view.
pre_how = html.split('<details class="how-board">', 1)[0]
if "Soft-paint poll" in pre_how:
    raise SystemExit("plumbing Features leaked above collapsed details")
if "Agents strip" in pre_how:
    raise SystemExit("Agents strip Feature card leaked above collapsed details")
if "Copy refresh command" in pre_how or "Mark board reviewed" in pre_how:
    raise SystemExit("engineer control cards leaked onto the default scroll")
pre_ab = html.split('<details class="abilities-foot">', 1)[0]
if "Rebuild this board" in pre_ab or "Life-ops" in pre_ab:
    raise SystemExit("Abilities card grid leaked onto the default phone scroll")
if "javascript:" in html.lower():
    raise SystemExit("javascript url in page")
if "6-digit" in html or "Jeff verify" in html:
    raise SystemExit("OTP copy still on page")
if "font-size:1.7rem" not in html:
    raise SystemExit("section H2 must be distinctly larger than lane titles")
if "font-size:.95rem" not in html:
    raise SystemExit("lane titles must stay smaller than section H2")
board = html.split('id="board"', 1)[-1]
idx_c = board.find('id="controls"')
idx_f = board.find('id="features"')
idx_l = board.find('id="live-shipping"')
idx_a = board.find('id="abilities"')
if idx_c < 0 or idx_l < 0 or idx_f < 0 or idx_a < 0:
    raise SystemExit("missing ordered sections")
if not (idx_c < idx_l < idx_a < idx_f):
    raise SystemExit("board order must be decisions, live shipping, abilities, how-board")
if 'id="active-agents"' in board:
    raise SystemExit("active-agents must live in the pulse strip, not the board card list")
pending = st.get("pending") if isinstance(st, dict) else None
if isinstance(pending, list) and pending:
    if "pending-item" not in html:
        raise SystemExit("pending items must paint on first load")
    if 'id="pending-box" class="pending-box" hidden' in html or 'id="pending-box" class="pending-box"hidden' in html:
        raise SystemExit("pending-box hidden while status.json has pending")
print("ops-first pulse + collapsed footers present")
PY
pass "ops-first board + collapsed how-board"

echo "ALL SMOKES PASSED (static). Public board -- GitHub login is the real yes."
