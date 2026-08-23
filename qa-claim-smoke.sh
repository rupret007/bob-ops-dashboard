#!/usr/bin/env bash
# Fail-closed smoke for bob-ops-dashboard claims before Bob says "good".
# Usage: ./qa-claim-smoke.sh [path-to-index.html]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
INDEX="${1:-$ROOT/index.html}"
STATUS="${STATUS_JSON:-$ROOT/status.json}"
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

# 2) No smart quotes in scripts
if ! python3 - "$TMP" <<'PY'
from pathlib import Path
import sys
bad = False
for p in Path(sys.argv[1]).glob("s*.js"):
    t = p.read_text()
    if any(ord(c) in (0x2018,0x2019,0x201C,0x201D) for c in t):
        print(p.name); bad = True
sys.exit(1 if bad else 0)
PY
then fail "smart quotes in JS"
else
  pass "no smart quotes"
fi

# 3) If status.json has verify + OTP in env, check hash
if [[ -f "$STATUS" && -n "${QA_OTP:-}" ]]; then
  python3 - "$STATUS" "$QA_OTP" <<'PY' || fail "OTP hash mismatch or expired"
import json, hashlib, sys, time
st = json.load(open(sys.argv[1]))
code = sys.argv[2].strip()
v = st.get("verify") or {}
email = (v.get("email") or "jeffstory007@gmail.com").lower()
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

# 4) Required unlock symbols present in source
grep -q 'function doUnlock\|async function doUnlock' "$INDEX" || fail "doUnlock missing"
grep -q 'btn-confirm-code' "$INDEX" || fail "Unlock button missing"
pass "unlock markers present"

echo "ALL SMOKES PASSED (static). Browser Unlock click still required for full PASS."
