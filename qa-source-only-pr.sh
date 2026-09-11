#!/usr/bin/env bash
# Check the PR's contribution and the integrated snapshots separately.
set -euo pipefail

if [[ ! "${PR_HEAD_SHA:-}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "A full PR_HEAD_SHA is required." >&2
  exit 1
fi
read -r base actual_head extra <<< "$(git show -s --format=%P HEAD)"
if [[ -z "$base" || -z "$actual_head" || -n "${extra:-}" || "$actual_head" != "$PR_HEAD_SHA" ]]; then
  echo "Source-only QA requires the expected two-parent PR merge checkout." >&2
  exit 1
fi
if ! git diff --quiet "$base...$PR_HEAD_SHA" -- index.html status.json; then
  echo "Feature PRs must not change scheduler-owned index.html or status.json." >&2
  exit 1
fi
if ! git diff --quiet "$base" HEAD -- index.html status.json; then
  echo "The PR integration must preserve the base's scheduler-owned snapshots." >&2
  exit 1
fi
echo "SOURCE-ONLY PR PASSED: feature changes excluded snapshots; integration preserved base snapshots."
