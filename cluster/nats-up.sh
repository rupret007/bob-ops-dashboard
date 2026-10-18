#!/usr/bin/env bash
# Idempotent keepalive for nats-server (user systemd or nohup loop).
set -euo pipefail

CONFIG_DIR="${HOME}/.config/bob-cluster"
CONF="${CONFIG_DIR}/nats.conf"
PID_FILE="${CONFIG_DIR}/nats-server.pid"
LOG_FILE="${CONFIG_DIR}/nats-server.log"
NATSBIN="${HOME}/.local/bin/nats-server"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Refuse stale or overbroad ACLs before starting/restarting this broker.
check_coord_acl() {
  PYTHONPATH="${SCRIPT_DIR}/..${PYTHONPATH:+:${PYTHONPATH}}" python3 -m cluster.nats_acl --check "${CONF}"
}

running_pid() {
  if [[ -f "${PID_FILE}" ]]; then
    local pid
    pid="$(cat "${PID_FILE}")"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      echo "${pid}"
      return 0
    fi
  fi
  return 1
}

start_once() {
  if running_pid >/dev/null; then
    echo "nats-server already running (pid $(cat "${PID_FILE}"))"
    return 0
  fi
  if [[ ! -x "${NATSBIN}" ]]; then
    echo "Missing ${NATSBIN}; run cluster/install-nats.sh first" >&2
    return 1
  fi
  if [[ ! -f "${CONF}" ]]; then
    echo "Missing ${CONF}; run cluster/install-nats.sh first" >&2
    return 1
  fi
  nohup "${NATSBIN}" -c "${CONF}" >>"${LOG_FILE}" 2>&1 &
  echo $! > "${PID_FILE}"
  echo "Started nats-server pid $(cat "${PID_FILE}")"
}

install_systemd_user() {
  local unit_dir="${HOME}/.config/systemd/user"
  local unit="${unit_dir}/bob-cluster-nats.service"
  mkdir -p "${unit_dir}"
  cat > "${unit}" <<EOF
[Unit]
Description=Bob cluster NATS server
After=network.target

[Service]
Type=simple
ExecStart=${NATSBIN} -c ${CONF}
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now bob-cluster-nats.service
  echo "Enabled systemd user unit bob-cluster-nats.service"
}

case "${1:-start}" in
  start)
    check_coord_acl
    if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
      install_systemd_user
    else
      start_once
    fi
    ;;
  stop)
    if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active bob-cluster-nats.service >/dev/null 2>&1; then
      systemctl --user stop bob-cluster-nats.service
    elif running_pid >/dev/null; then
      kill "$(cat "${PID_FILE}")" || true
      rm -f "${PID_FILE}"
    fi
    ;;
  status)
    if running_pid >/dev/null; then
      echo "running pid $(cat "${PID_FILE}")"
    elif command -v systemctl >/dev/null 2>&1; then
      systemctl --user status bob-cluster-nats.service || true
    else
      echo "not running"
    fi
    ;;
  *)
    echo "Usage: $0 {start|stop|status}" >&2
    exit 1
    ;;
esac
