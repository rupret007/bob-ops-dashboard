#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "${ROOT}/live/load-env.sh"
python3 "${ROOT}/live/aggregator.py" --check-config
STATE_DIR="${HOME}/.local/state/bob-live"
mkdir -p "${STATE_DIR}"
AGG_PID="${STATE_DIR}/aggregator.pid"
AGENT_PID="${STATE_DIR}/nuc-agent.pid"

start_one() {
  local name="$1" pidfile="$2"
  shift 2
  if [[ -f "${pidfile}" ]] && kill -0 "$(cat "${pidfile}")" 2>/dev/null; then
    echo "${name} already running (pid $(cat "${pidfile}"))"
    return 0
  fi
  nohup "$@" >>"${STATE_DIR}/${name}.log" 2>&1 &
  echo $! >"${pidfile}"
  echo "started ${name} pid $(cat "${pidfile}")"
}

start_one aggregator "${AGG_PID}" python3 "${ROOT}/live/aggregator.py"
sleep 1
if ! kill -0 "$(cat "$AGG_PID")" 2>/dev/null; then
  echo "aggregator failed to start; see ${STATE_DIR}/aggregator.log" >&2
  exit 1
fi
PUSH_HOST="${LIVE_BIND:-127.0.0.1}"
[[ "$PUSH_HOST" != *:* ]] || PUSH_HOST="[$PUSH_HOST]"
PUSH_URL="${BOB_LIVE_PUSH:-http://${PUSH_HOST}:${LIVE_PORT:-8787}/api/node}"
start_one nuc-agent "${AGENT_PID}" python3 "${ROOT}/live/agent.py" --node-id nuc --push "$PUSH_URL" --interval 2
