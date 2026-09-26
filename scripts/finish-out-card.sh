#!/usr/bin/env bash
# K6: fill a finish-out card from live gh + optional Pages board stamp.
#
# Usage:
#   ./scripts/finish-out-card.sh [owner/repo] [tag]
#   ./scripts/finish-out-card.sh rupret007/webjam v0.28.4
#   ./scripts/finish-out-card.sh rupret007/webjam          # uses latest release tag if any
#
# Writes markdown to stdout; optional -o path. Exit 2 if CI green and Release URL blank.
set -euo pipefail

OUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output) OUT="${2:-}"; shift 2 ;;
    -h|--help)
      echo "usage: $0 [-o file] [owner/repo] [tag]" >&2
      exit 0
      ;;
    *) break ;;
  esac
done

REPO="${1:-rupret007/webjam}"
TAG_ARG="${2:-}"
BOARD_STATUS_URL="${BOARD_STATUS_URL:-https://rupret007.github.io/bob-ops-dashboard/status.json}"

if ! command -v gh >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
  echo "gh and jq required" >&2
  exit 1
fi

# Tip SHA (default branch)
DEFAULT_BRANCH="$(gh api "repos/${REPO}" --jq .default_branch)"
TIP_SHA="$(gh api "repos/${REPO}/commits/${DEFAULT_BRANCH}" --jq .sha)"
TIP_SHORT="${TIP_SHA:0:7}"

# Resolve tag
TAG="$TAG_ARG"
if [[ -z "$TAG" ]]; then
  TAG="$(gh api "repos/${REPO}/releases/latest" --jq .tag_name 2>/dev/null || true)"
fi
if [[ -z "$TAG" ]]; then
  # fallback: newest tag ref
  TAG="$(gh api "repos/${REPO}/tags?per_page=1" --jq '.[0].name // empty' 2>/dev/null || true)"
fi

RELEASE_URL=""
IS_LATEST="unknown"
CI_CONCLUSION="n/a"
CI_RUN_URL=""

if [[ -n "$TAG" ]]; then
  # Release
  if rel_json="$(gh api "repos/${REPO}/releases/tags/${TAG}" 2>/dev/null)"; then
    RELEASE_URL="$(echo "$rel_json" | jq -r .html_url)"
  else
    RELEASE_URL=""
  fi
  # Latest flag
  if latest_json="$(gh api "repos/${REPO}/releases/latest" 2>/dev/null)"; then
    latest_tag="$(echo "$latest_json" | jq -r .tag_name)"
    if [[ "$latest_tag" == "$TAG" ]]; then
      IS_LATEST="true"
    else
      IS_LATEST="false (latest=${latest_tag})"
    fi
  else
    IS_LATEST="no-latest-release"
  fi
  # CI on tag (workflow runs for that head branch / tag)
  run_json="$(gh run list -R "$REPO" --branch "$TAG" --limit 1 \
    --json conclusion,url,databaseId,displayTitle,headSha 2>/dev/null || echo '[]')"
  if [[ "$(echo "$run_json" | jq 'length')" -gt 0 ]]; then
    CI_CONCLUSION="$(echo "$run_json" | jq -r '.[0].conclusion // "pending"')"
    CI_RUN_URL="$(echo "$run_json" | jq -r '.[0].url // empty')"
  else
    # try tip SHA runs
    run_json="$(gh run list -R "$REPO" --commit "$TIP_SHA" --limit 1 \
      --json conclusion,url 2>/dev/null || echo '[]')"
    if [[ "$(echo "$run_json" | jq 'length')" -gt 0 ]]; then
      CI_CONCLUSION="$(echo "$run_json" | jq -r '.[0].conclusion // "pending"')"
      CI_RUN_URL="$(echo "$run_json" | jq -r '.[0].url // empty')"
    fi
  fi
else
  TAG="(none)"
fi

# Board stamp from live Pages status.json
BOARD_STAMP=""
BOARD_TIP=""
if board="$(curl -fsSL "$BOARD_STATUS_URL" 2>/dev/null)"; then
  BOARD_STAMP="$(echo "$board" | jq -r '.generated_at // .llm_work_now_freshness.generated_at // empty')"
  # Prefer matching project tip_sha from board sections (e.g. WebJam chip).
  BOARD_TIP="$(echo "$board" | jq -r --arg repo "$REPO" '
    [.sections[]?.projects[]? | select((.repo // "") == $repo) | .tip_sha // empty]
    | first // empty')"
  if [[ -z "$BOARD_TIP" ]]; then
    BOARD_TIP="$(echo "$board" | jq -r '.commit // .git_sha // .tip // empty')"
  fi
fi

FILLED_AT_CT="$(TZ=America/Chicago date '+%Y-%m-%d %H:%M CT')"

# Hole detection
HOLE=""
if [[ "$CI_CONCLUSION" == "success" && -z "$RELEASE_URL" ]]; then
  HOLE="HOLE: CI green but Release URL blank"
fi

card="$(cat <<CARD
# Finish-out card (K6)

Filled by \`scripts/finish-out-card.sh\` at **${FILLED_AT_CT}**.
${HOLE:+
**${HOLE}**
}

| Field | Value |
|-------|-------|
| Repo | \`${REPO}\` |
| Tip SHA | \`${TIP_SHA}\` |
| Tip short | \`${TIP_SHORT}\` |
| CI conclusion | \`${CI_CONCLUSION}\` |
| CI run URL | ${CI_RUN_URL:-*(blank)*} |
| Tag | \`${TAG}\` |
| Release URL | ${RELEASE_URL:-**(blank — hole if CI green)**} |
| Latest flag | \`${IS_LATEST}\` |
| Board stamp | \`${BOARD_STAMP:-unknown}\` |
| Board tip | \`${BOARD_TIP:-unknown}\` |
| Filled at (CT) | ${FILLED_AT_CT} |

## Next
$(if [[ -n "$RELEASE_URL" && "$IS_LATEST" == "true" ]]; then
  echo "1. Nudge board: \`TAG=${TAG} RELEASE_URL=${RELEASE_URL} SOURCE_REPO=${REPO} ./scripts/dispatch-board-refresh.sh release-published\`"
  echo "2. Re-check board stamp moves within one refresh."
elif [[ -n "$HOLE" ]]; then
  echo "1. Run repo publish path (draft → Latest) or escalate once to Jeff."
  echo "2. Do not claim shipped until Release URL is filled."
else
  echo "1. Wait/backoff (finish-out-watchdog); re-run this script."
fi)
CARD
)"

if [[ -n "$OUT" ]]; then
  printf '%s\n' "$card" > "$OUT"
  echo "Wrote $OUT" >&2
else
  printf '%s\n' "$card"
fi

if [[ -n "$HOLE" ]]; then
  exit 2
fi
