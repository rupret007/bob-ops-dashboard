#!/usr/bin/env bash
# Rebuild disposable dashboard artifacts, then run the full claim smoke without
# touching the source checkout's scheduler-owned index.html or status.json.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

cp "$ROOT/README.md" "$ROOT/board_meta.py" "$ROOT/index.html" \
  "$ROOT/refresh.sh" "$ROOT/status.json" "$SCRATCH/"
chmod +x "$SCRATCH/refresh.sh" "$ROOT/qa-claim-smoke.sh"

(
  cd "$SCRATCH"
  BOB_DASHBOARD_APPLY_DECISIONS=0 ./refresh.sh
)

REFRESH_SH="$SCRATCH/refresh.sh" \
STATUS_JSON="$SCRATCH/status.json" \
  "$ROOT/qa-claim-smoke.sh" "$SCRATCH/index.html"

if [[ -n "$(git -C "$ROOT" status --porcelain -- index.html status.json)" ]]; then
  echo "FAIL: source-only QA changed scheduler-owned artifacts" >&2
  exit 1
fi

echo "SOURCE-ONLY QA PASSED"
