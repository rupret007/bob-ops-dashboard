#!/usr/bin/env bash
# Public board contract, shared-renderer behavior, and refresh integrity.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
INDEX="${1:-$ROOT/index.html}"
STATUS="${STATUS_JSON:-$ROOT/status.json}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

python3 "$ROOT/qa-generated.py" "$INDEX" "$STATUS"
python3 - "$INDEX" "$TMP" <<'PY'
from pathlib import Path
import re, sys
scripts = re.findall(r'<script>(.*?)</script>', Path(sys.argv[1]).read_text(), re.S)
assert scripts, 'Missing browser script'
for i, script in enumerate(scripts):
    assert script.isascii(), 'Browser script must remain ASCII-safe'
    (Path(sys.argv[2]) / f's{i}.js').write_text(script)
PY
for file in "$TMP"/s*.js; do node --check "$file"; done
node --test "$ROOT/test_dashboard.js"
cd "$ROOT"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q test_board_meta test_refresh_outage_guard test_refresh_publish_artifacts test_offline_qa test_dashboard
printf '%s\n' 'ALL SMOKES PASSED: renderer, public data, and refresh integrity.'
