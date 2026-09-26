#!/usr/bin/env bash
# K5: nudge bob-ops-dashboard refresh without waiting for the 15m cron.
#
# Triggers repository_dispatch on rupret007/bob-ops-dashboard (listener in
# .github/workflows/refresh-dashboard.yml since #60).
#
# Usage:
#   ./scripts/dispatch-board-refresh.sh
#   ./scripts/dispatch-board-refresh.sh release-published
#   TAG=v0.28.4 RELEASE_URL=https://github.com/rupret007/webjam/releases/tag/v0.28.4 \
#     ./scripts/dispatch-board-refresh.sh release-published
#
# Auth: uses ambient `gh` auth (box / conductor PAT). Does NOT invent Actions
# secrets. Cross-repo dispatch from webjam Actions still needs a Jeff-owned PAT
# secret on webjam — see docs/k5-release-refresh.md (blocker until that exists).
set -euo pipefail

EVENT_TYPE="${1:-refresh}"
case "$EVENT_TYPE" in
  refresh|release-published) ;;
  *)
    echo "usage: $0 [refresh|release-published]" >&2
    exit 2
    ;;
esac

TARGET_OWNER="${TARGET_OWNER:-rupret007}"
TARGET_REPO="${TARGET_REPO:-bob-ops-dashboard}"
TAG="${TAG:-}"
RELEASE_URL="${RELEASE_URL:-}"
SOURCE_REPO="${SOURCE_REPO:-}"

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI required" >&2
  exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "jq required" >&2
  exit 1
fi

body="$(jq -nc \
  --arg event_type "$EVENT_TYPE" \
  --arg tag "$TAG" \
  --arg release_url "$RELEASE_URL" \
  --arg source_repo "$SOURCE_REPO" \
  --arg dispatched_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{
     event_type: $event_type,
     client_payload: {
       tag: $tag,
       release_url: $release_url,
       source_repo: $source_repo,
       event_type: $event_type,
       dispatched_at: $dispatched_at
     }
   }')"

echo "Dispatching event_type=${EVENT_TYPE} → ${TARGET_OWNER}/${TARGET_REPO}"
echo "$body" | gh api \
  --method POST \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "/repos/${TARGET_OWNER}/${TARGET_REPO}/dispatches" \
  --input -

echo "OK: repository_dispatch accepted (202). Watch Actions:"
echo "  gh run list -R ${TARGET_OWNER}/${TARGET_REPO} --workflow=refresh-dashboard.yml --limit 3"
