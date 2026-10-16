#!/usr/bin/env bash
set -euo pipefail
STATE_DIR="${HOME}/.local/state/bob-live"
mkdir -p "${STATE_DIR}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_PID="${STATE_DIR}/mini-agent.pid"
PUSH_URL="${BOB_LIVE_PUSH:-http://100.93.165.1:8787/api/node}"

if [[ -f "${AGENT_PID}" ]] && kill -0 "$(cat "${AGENT_PID}")" 2>/dev/null; then
  echo "mini agent already running (pid $(cat "${AGENT_PID}"))"
  exit 0
fi
nohup nice -n 15 bash -c "cd '${ROOT}' && python3 live/agent.py --node-id mini --push '${PUSH_URL}' --interval 2" \
  >>"${STATE_DIR}/mini-agent.log" 2>&1 &
echo $! >"${AGENT_PID}"
echo "started mini agent pid $(cat "${AGENT_PID}")"
