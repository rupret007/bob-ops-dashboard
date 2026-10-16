#!/usr/bin/env bash
# Sourced by both launchers before creating state or starting any process.
LIVE_CONFIG="${LIVE_CONFIG:-${HOME}/.config/bob-live/live.env}"
if [[ -f "$LIVE_CONFIG" ]]; then
  python3 "${ROOT}/live/private_config.py" check "$LIVE_CONFIG"
  set -a
  source "$LIVE_CONFIG"
  set +a
elif [[ -n "${LIVE_CONFIG_REQUIRED:-}" ]]; then
  echo "live configuration: missing LIVE_CONFIG" >&2
  exit 1
fi
LIVE_TOKEN_FILE="${LIVE_TOKEN_FILE:-${HOME}/.config/bob-live/token}"
LIVE_TOKEN="$(python3 "${ROOT}/live/private_config.py" token "$LIVE_TOKEN_FILE")"
export LIVE_TOKEN
