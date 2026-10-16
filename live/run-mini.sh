#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "${ROOT}/live/load-env.sh"
: "${BOB_LIVE_PUSH:?Set BOB_LIVE_PUSH in the environment or private live.env}"
STATE_DIR="${HOME}/.local/state/bob-live"
mkdir -p "${STATE_DIR}"
AGENT_PID="${STATE_DIR}/mini-agent.pid"
if [[ -f "${AGENT_PID}" ]] && kill -0 "$(cat "${AGENT_PID}")" 2>/dev/null; then
  echo "mini agent already running (pid $(cat "${AGENT_PID}"))"
  exit 0
fi
nohup nice -n 15 python3 "${ROOT}/live/agent.py" --node-id mini --push "$BOB_LIVE_PUSH" --interval 2 \
  >>"${STATE_DIR}/mini-agent.log" 2>&1 &
echo $! >"${AGENT_PID}"
echo "started mini agent pid $(cat "${AGENT_PID}")"
