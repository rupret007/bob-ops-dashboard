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
grep -q 'concl === "skipped"' "$INDEX" || fail "laneHrefs must drop skipped Open CI"
grep -q 'concl === "skipped"' "$REFRESH" || fail "refresh.sh laneHrefs must drop skipped Open CI"
grep -q 'data-open="work"' "$REFRESH" || fail "refresh.sh missing work-link taps"
grep -q 'Open agent' "$REFRESH" || fail "refresh.sh missing Open agent"
grep -q 'Open repo' "$REFRESH" || fail "refresh.sh missing Open repo"
grep -q 'Open CI' "$REFRESH" || fail "refresh.sh missing Open CI"
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
  python3 - "$STATUS" <<'PY' || fail "status.json still has verify"
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
    "Andrea NanoBot", "OpenClaw Runtime", "StoryLiner", "AI Music Vault",
    "Bob Ops Dashboard", "RadDadSite", "Cursor-OpenClaw Integration",
    "Sliding Glass Door Screw", "Ophelia / Moises",
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
for p in projects:
    if p.get("private") and (p.get("agent_url") or p.get("cloud_agents")):
        raise SystemExit("private lane leaked agent metadata: " + str(p.get("name")))
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
  E2E="$TMP/refresh-e2e"
  mkdir -p "$E2E"
  cp "$ROOT/refresh.sh" "$ROOT/board_meta.py" "$ROOT/README.md" "$E2E/"
  chmod +x "$E2E/refresh.sh"
  python3 - "$E2E/status.json" <<'PY' || fail "e2e seed failed"
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "generated_at": "e2e-seed",
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
if "bob-ops-dashboard" in (st.get("fetched_repos") or []):
    if not seed_present:
        raise SystemExit("public-PR-backed cloud agent URL missing")
elif seed_present:
    raise SystemExit("cloud agent survived without proof its PR repository is public")
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
