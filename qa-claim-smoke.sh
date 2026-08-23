#!/usr/bin/env bash
# Fail-closed smoke for bob-ops-dashboard claims before Bob says "good".
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

# 3) If status.json has verify + OTP in env, check hash
if [[ -f "$STATUS" && -n "${QA_OTP:-}" ]]; then
  python3 - "$STATUS" "$QA_OTP" <<'PY' || fail "OTP hash mismatch or expired"
import json, hashlib, sys, time
st = json.load(open(sys.argv[1]))
code = sys.argv[2].strip()
v = st.get("verify") or {}
email = (v.get("email") or "").lower()
if email != "jeffstory007@gmail.com":
    raise SystemExit("verify email not allowlisted")
want = hashlib.sha256(f"{code}:{email}".encode()).hexdigest()
if want != (v.get("sha256") or "").lower():
    raise SystemExit("hash mismatch")
if int(time.time() * 1000) > int(v.get("exp") or 0):
    raise SystemExit("expired")
if not (code.isdigit() and len(code) == 6):
    raise SystemExit("otp not 6 digits")
print("otp ok")
PY
  pass "OTP hash/expiry"
else
  echo "SKIP: OTP hash (set QA_OTP to enable)"
fi

# 4) Required unlock / anti-regression symbols in live page
grep -q 'function doUnlock\|async function doUnlock' "$INDEX" || fail "doUnlock missing"
grep -q 'btn-confirm-code' "$INDEX" || fail "Unlock button missing"
grep -q 'var JEFF_EMAIL = "jeffstory007@gmail.com"' "$INDEX" || fail "JEFF_EMAIL allowlist missing or changed"
grep -q 'unlockBusy' "$INDEX" || fail "unlockBusy missing"
grep -q 'unlockTouchAt' "$INDEX" || fail "unlockTouchAt missing"
grep -q 'function safeHref' "$INDEX" || fail "safeHref missing"
grep -q 'pollSeq' "$INDEX" || fail "pollSeq missing"
grep -q 'decideBusy' "$INDEX" || fail "decideBusy missing"
grep -q 'pendingSeq' "$INDEX" || fail "pendingSeq missing"
pass "unlock markers present"

# 5) Generator must keep the same fail-closed guards (refresh.sh is source of truth)
if [[ -f "$REFRESH" ]]; then
  grep -q 'html.escape' "$REFRESH" || fail "refresh.sh missing html.escape"
  grep -q 'def safe_href' "$REFRESH" || fail "refresh.sh missing safe_href"
  grep -q 'unlockBusy' "$REFRESH" || fail "refresh.sh missing unlockBusy"
  grep -q 'function safeHref' "$REFRESH" || fail "refresh.sh missing JS safeHref"
  grep -q 'pollSeq' "$REFRESH" || fail "refresh.sh missing pollSeq"
  grep -q 'var JEFF_EMAIL = "jeffstory007@gmail.com"' "$REFRESH" || fail "refresh.sh JEFF_EMAIL allowlist changed"
  grep -q 'em == "jeffstory007@gmail.com"' "$REFRESH" || fail "refresh.sh verify allowlist not fail-closed"
  python3 - "$REFRESH" <<'PY' || fail "smart quotes in refresh.sh"
from pathlib import Path
import sys
t = Path(sys.argv[1]).read_text()
if any(ord(c) in (0x2018, 0x2019, 0x201C, 0x201D) for c in t):
    raise SystemExit("smart quotes")
PY
  pass "refresh.sh guards present"
else
  echo "SKIP: refresh.sh not present"
fi

# 6) status.json verify allowlist (when present)
if [[ -f "$STATUS" ]]; then
  python3 - "$STATUS" <<'PY' || fail "status.json verify allowlist"
import json, re, sys
st = json.load(open(sys.argv[1]))
v = st.get("verify")
if not v:
    print("no verify block")
    raise SystemExit(0)
if (v.get("email") or "").lower() != "jeffstory007@gmail.com":
    raise SystemExit("email not allowlisted")
sha = (v.get("sha256") or "").lower()
if not re.fullmatch(r"[0-9a-f]{64}", sha):
    raise SystemExit("sha256 not 64 hex")
int(v.get("exp") or 0)
print("verify allowlist ok")
PY
  pass "status.json verify allowlist"
else
  echo "SKIP: status.json missing"
fi

# 7) Functional XSS helpers from extracted page JS
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
if (!src.includes("unlockBusy") || !src.includes("pollSeq") || !src.includes("decideBusy")) {
  throw new Error("guards missing in extracted JS");
}
console.log("helpers ok");
JS
node "$HELPER_JS" "$TMP"/s0.js || fail "safeHref/esc behavior"
pass "safeHref/esc behavior"

# 8) Python HTML helpers replica (must stay fail-closed)
python3 - <<'PY' || fail "python html helpers"
import html
def h(s):
    return html.escape("" if s is None else str(s), quote=True)
def safe_href(u):
    if not u:
        return ""
    s = str(u).strip()
    low = s.lower()
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
print("py helpers ok")
PY
pass "python html helpers"

echo "ALL SMOKES PASSED (static). Browser Unlock click still required for full PASS."
