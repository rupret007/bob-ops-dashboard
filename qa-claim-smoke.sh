#!/usr/bin/env bash
# Fail-closed smoke for bob-ops-dashboard claims before Bob says "good".
# Keeps PR #1 XSS/race gates AND PR #2 OTP preserve / fallback / first-class sections.
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

# 4) Required unlock / anti-regression symbols in live page (#1 + #2)
grep -q 'function doUnlock\|async function doUnlock' "$INDEX" || fail "doUnlock missing"
grep -q 'btn-confirm-code' "$INDEX" || fail "Unlock button missing"
grep -q 'var JEFF_EMAIL = "jeffstory007@gmail.com"' "$INDEX" || fail "JEFF_EMAIL allowlist missing or changed"
grep -q 'unlockBusy' "$INDEX" || fail "unlockBusy missing"
grep -q 'unlockTouchAt' "$INDEX" || fail "unlockTouchAt missing"
grep -q 'function safeHref' "$INDEX" || fail "safeHref missing"
grep -q 'pollSeq' "$INDEX" || fail "pollSeq missing"
grep -q 'decideBusy' "$INDEX" || fail "decideBusy missing"
grep -q 'pendingSeq' "$INDEX" || fail "pendingSeq missing"
grep -q 'No code active yet. Ask Bob for one.' "$INDEX" || fail "missing-verify Unlock message changed"
grep -q 'That code expired. Ask Bob for a new one.' "$INDEX" || fail "expired Unlock message changed"
grep -q 'raw.githubusercontent.com/rupret007/bob-ops-dashboard/main/status.json' "$INDEX" || fail "Unlock raw fallback URL missing"
grep -q 'function loadUnlockStatus' "$INDEX" || fail "loadUnlockStatus missing"
grep -q 'function hasUnlockVerify' "$INDEX" || fail "hasUnlockVerify missing"
grep -q 'String(v.email || "").toLowerCase() !== JEFF_EMAIL' "$INDEX" || fail "doUnlock email not fail-closed"
if grep -q '(v.email || JEFF_EMAIL)' "$INDEX"; then
  fail "doUnlock defaults missing email to Jeff"
fi
pass "unlock markers present"

# 5) Generator must keep #1 fail-closed guards AND #2 preserve clock
[[ -f "$REFRESH" ]] || fail "missing $REFRESH"
grep -q 'html.escape' "$REFRESH" || fail "refresh.sh missing html.escape"
grep -q 'def safe_href' "$REFRESH" || fail "refresh.sh missing safe_href"
grep -q 'unlockBusy' "$REFRESH" || fail "refresh.sh missing unlockBusy"
grep -q 'function safeHref' "$REFRESH" || fail "refresh.sh missing JS safeHref"
grep -q 'pollSeq' "$REFRESH" || fail "refresh.sh missing pollSeq"
grep -q 'var JEFF_EMAIL = "jeffstory007@gmail.com"' "$REFRESH" || fail "refresh.sh JEFF_EMAIL allowlist changed"
grep -q 'em == "jeffstory007@gmail.com"' "$REFRESH" || fail "refresh.sh verify allowlist not fail-closed"
grep -q 'from preserve_verify import apply_preserved_verify' "$REFRESH" || fail "refresh.sh missing preserve_verify import"
grep -q 'REFRESH_STARTED_MS' "$REFRESH" || fail "refresh.sh missing REFRESH_STARTED_MS"
grep -q 'refresh_started_ms=' "$REFRESH" || fail "refresh.sh missing refresh_started_ms preserve clock"
if grep -q 'int(v\["exp"\]) > int(time.time()' "$REFRESH"; then
  fail "refresh.sh still uses exp>now preserve (wipe race)"
fi
grep -q 'jeffstory007@gmail.com' "$ROOT/preserve_verify.py" || fail "preserve_verify allowlist missing"
python3 - "$REFRESH" <<'PY' || fail "smart quotes in refresh.sh"
from pathlib import Path
import sys
t = Path(sys.argv[1]).read_text()
if any(ord(c) in (0x2018, 0x2019, 0x201C, 0x201D) for c in t):
    raise SystemExit("smart quotes")
PY
pass "refresh.sh guards present (#1 XSS/races + #2 preserve)"

# 6) Unit + seed: unexpired verify must survive the preserve snippet
[[ -f "$ROOT/test_preserve_verify.py" ]] || fail "missing test_preserve_verify.py"
python3 "$ROOT/test_preserve_verify.py" -v || fail "preserve_verify unit tests"
pass "preserve_verify unit tests"
[[ -f "$ROOT/test_board_meta.py" ]] || fail "missing test_board_meta.py"
python3 "$ROOT/test_board_meta.py" -v || fail "board_meta unit tests"
pass "board_meta unit tests"

SEED_STATUS="$TMP/seed-status.json"
python3 - "$SEED_STATUS" "$ROOT" <<'PY' || fail "seeded verify did not survive preserve"
import hashlib, json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from preserve_verify import JEFF_EMAIL, TTL_MS, apply_status_file

now = int(time.time() * 1000)
# Near-end-of-window race: stored exp already 20s in the past; issued_at+TTL still live.
issued_at = now - TTL_MS + 10_000
exp = issued_at + TTL_MS - 30_000  # exp 20s behind "now" after a 20s rebuild
assert exp < now
sha = hashlib.sha256(b"000000:" + JEFF_EMAIL.encode()).hexdigest()
Path(sys.argv[1]).write_text(json.dumps({
    "generated_at": "seed",
    "verify": {"email": JEFF_EMAIL, "sha256": sha, "exp": exp, "issued_at": issued_at},
}) + "\n")
kept = apply_status_file(sys.argv[1], now_ms=now)
if not kept or kept.get("sha256") != sha:
    raise SystemExit("seeded unexpired verify was dropped")
data = json.loads(Path(sys.argv[1]).read_text())
if (data.get("verify") or {}).get("sha256") != sha:
    raise SystemExit("status.json verify missing after preserve")
print("seeded verify survived exp-past / issued_at-live race")
PY
pass "seeded unexpired verify survived preserve"

# 7) Issuer TTL stays 2h and allowlist stays Jeff-only (do not run issuer; it writes OTP)
python3 - "$ROOT" <<'PY' || fail "issuer TTL/allowlist drift"
import importlib.util, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from preserve_verify import JEFF_EMAIL, TTL_MS
assert JEFF_EMAIL == "jeffstory007@gmail.com"
assert TTL_MS == 2 * 60 * 60 * 1000
for name in ("issue-dashboard-code.py", "issue_and_prepare_email.py"):
    spec = importlib.util.spec_from_file_location("issuer", root / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert getattr(mod, "EMAIL") == JEFF_EMAIL, name
    assert getattr(mod, "TTL_MS") == TTL_MS, name
print("issuer constants ok")
PY
pass "issuer 2h TTL + Jeff allowlist"

# 8) status.json verify allowlist (when present)
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

# 9) Functional XSS helpers from extracted page JS (#1)
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
if (!src.includes("unlockBusy") || !src.includes("pollSeq") || !src.includes("decideBusy")) {
  throw new Error("guards missing in extracted JS");
}
console.log("helpers ok");
JS
# helper functions live in the soft-paint script (usually s1.js)
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

# 10) Python HTML helpers replica (must stay fail-closed)
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
print("py helpers ok")
PY
pass "python html helpers"

# 11) e2e: seed a live-window verify (exp already past) and run refresh.sh in a temp dir
if command -v gh >/dev/null; then
  E2E="$TMP/refresh-e2e"
  mkdir -p "$E2E"
  cp "$ROOT/refresh.sh" "$ROOT/preserve_verify.py" "$ROOT/board_meta.py" "$ROOT/README.md" "$E2E/"
  chmod +x "$E2E/refresh.sh"
  python3 - "$E2E/status.json" <<'PY' || fail "e2e seed failed"
import hashlib, json, sys, time
from pathlib import Path
now = int(time.time() * 1000)
issued = now - 30_000
exp = now - 5_000
sha = hashlib.sha256(b"000000:jeffstory007@gmail.com").hexdigest()
Path(sys.argv[1]).write_text(json.dumps({
    "generated_at": "e2e-seed",
    "verify": {
        "email": "jeffstory007@gmail.com",
        "sha256": sha,
        "exp": exp,
        "issued_at": issued,
    },
}, indent=2) + "\n")
print(sha)
PY
  (cd "$E2E" && ./refresh.sh) || fail "refresh.sh e2e failed"
  python3 - "$E2E/status.json" <<'PY' || fail "refresh.sh wiped seeded verify"
import hashlib, json, sys, time
st = json.load(open(sys.argv[1]))
v = st.get("verify") or {}
want = hashlib.sha256(b"000000:jeffstory007@gmail.com").hexdigest()
if (v.get("email") or "").lower() != "jeffstory007@gmail.com":
    raise SystemExit("email missing after refresh")
if (v.get("sha256") or "").lower() != want:
    raise SystemExit("sha wiped after refresh")
if not st.get("refresh_started_ms"):
    raise SystemExit("refresh_started_ms missing from status")
ids = [s.get("id") for s in (st.get("sections") or [])]
for need in ("abilities", "controls", "features"):
    if need not in ids:
        raise SystemExit("missing section " + need)
print("refresh.sh e2e preserved seeded verify")
print("refresh_started_ms", st.get("refresh_started_ms"))
PY
  pass "refresh.sh e2e preserved seeded verify"
else
  echo "SKIP: refresh.sh e2e (gh not available)"
fi

# 12) first-class Abilities / Controls / Features on the draft page
for id in abilities controls features; do
  grep -q "id=\"$id\"" "$INDEX" || fail "index.html missing section #$id"
  grep -q "href=\"#$id\"" "$INDEX" || fail "index.html TOC missing #$id"
done
grep -q 'href="#abilities"' "$REFRESH" || fail "refresh.sh TOC missing abilities"
grep -q 'merge_first_class' "$REFRESH" || fail "refresh.sh missing merge_first_class"
python3 - "$INDEX" <<'PY' || fail "first-class sections honesty"
from pathlib import Path
import sys
html = Path(sys.argv[1]).read_text()
for needle in ("Abilities", "Controls", "Features", "jeffstory007@gmail.com", "No send button", "no order button"):
    if needle not in html:
        raise SystemExit("missing " + needle)
if "javascript:" in html.lower():
    raise SystemExit("javascript url in page")
print("first-class sections present")
PY
pass "Abilities / Controls / Features present"

# 13) Pages-cache fallback: null/failed Pages verify must use raw main (fail-closed)
[[ -f "$ROOT/test_unlock_fallback.js" ]] || fail "missing test_unlock_fallback.js"
node "$ROOT/test_unlock_fallback.js" "$INDEX" || fail "unlock fallback smoke"
grep -q 'raw.githubusercontent.com/rupret007/bob-ops-dashboard/main/status.json' "$REFRESH" || fail "refresh.sh missing raw fallback URL"
grep -q 'function loadUnlockStatus' "$REFRESH" || fail "refresh.sh missing loadUnlockStatus"
pass "unlock Pages-cache fallback"

echo "ALL SMOKES PASSED (static). Browser Unlock click still required for full PASS."
