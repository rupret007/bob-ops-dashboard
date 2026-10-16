#!/usr/bin/env bash
set -euo pipefail
STATE_DIR="${HOME}/.local/state/bob-live"
mkdir -p "${STATE_DIR}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGG_PID="${STATE_DIR}/aggregator.pid"
AGENT_PID="${STATE_DIR}/nuc-agent.pid"

start_one() {
  local name="$1" pidfile="$2" cmd="$3"
  if [[ -f "${pidfile}" ]] && kill -0 "$(cat "${pidfile}")" 2>/dev/null; then
    echo "${name} already running (pid $(cat "${pidfile}"))"
    return 0
  fi
  nohup bash -c "${cmd}" >>"${STATE_DIR}/${name}.log" 2>&1 &
  echo $! >"${pidfile}"
  echo "started ${name} pid $(cat "${pidfile}")"
}

start_one aggregator "${AGG_PID}" "cd '${ROOT}' && python3 live/aggregator.py"
sleep 1
PUSH_URL="http://127.0.0.1:${LIVE_PORT:-8787}/api/node"
start_one nuc-agent "${AGENT_PID}" "cd '${ROOT}' && python3 live/agent.py --node-id nuc --push '${PUSH_URL}' --interval 2"
