#!/usr/bin/env bash
# One-command Bob cluster node join (macOS, Linux, WSL). Never enable xtrace (secrets).
set -euo pipefail

REF="main"
CODE=""
LANES=""
DRY_RUN=0
NO_SERVICE=0

usage() {
  echo "Usage: join.sh --code 'bobjoin1:...' [--ref <git sha or branch>] [--lanes N] [--dry-run] [--no-service]" >&2
  echo "       join.sh [--ref ...] (with BOB_JOIN_CODE in the environment)" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --code)
      CODE="${2:?--code requires a value}"
      shift 2
      ;;
    --ref)
      REF="${2:?--ref requires a value}"
      shift 2
      ;;
    --lanes)
      LANES="${2:?--lanes requires a value}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --no-service)
      NO_SERVICE=1
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      ;;
  esac
done

if [[ -z "$CODE" && -n "${BOB_JOIN_CODE:-}" ]]; then
  CODE="$BOB_JOIN_CODE"
fi

if [[ -z "$CODE" ]]; then
  echo "join.sh: --code or BOB_JOIN_CODE is required" >&2
  exit 1
fi

APP="${HOME}/.local/share/bob-cluster/app"
VENV="${HOME}/.local/share/bob-cluster/venv"
NODE_ENV="${HOME}/.config/bob-cluster/node.env"
REPO="rupret007/bob-ops-dashboard"
TARBALL_URL="https://codeload.github.com/${REPO}/tar.gz/${REF}"

OS_KIND="linux"
if [[ "$(uname -s)" == "Darwin" ]]; then
  OS_KIND="darwin"
elif grep -qi microsoft /proc/version 2>/dev/null; then
  OS_KIND="wsl"
fi

dry() {
  echo "DRY: $*"
}

need_cmd() {
  local name="$1"
  local hint="$2"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "join.sh: required command not found: $name" >&2
    echo "$hint" >&2
    exit 1
  fi
}

python_hint() {
  case "$OS_KIND" in
    darwin) echo "Install Python 3.10+: brew install python@3.12" ;;
    wsl)    echo "Install Python 3.10+: sudo apt update && sudo apt install -y python3 python3-venv python3-pip" ;;
    *)      echo "Install Python 3.10+: sudo apt install -y python3 python3-venv || sudo dnf install -y python3" ;;
  esac
}

git_hint() {
  case "$OS_KIND" in
    darwin) echo "Install git: xcode-select --install  OR  brew install git" ;;
    *)      echo "Install git: sudo apt install -y git  OR  sudo dnf install -y git" ;;
  esac
}

curl_hint() {
  case "$OS_KIND" in
    darwin) echo "Install curl: brew install curl" ;;
    *)      echo "Install curl: sudo apt install -y curl  OR  sudo dnf install -y curl" ;;
  esac
}

tar_hint() {
  case "$OS_KIND" in
    darwin) echo "Install gnu-tar: brew install gnu-tar" ;;
    *)      echo "Install tar: sudo apt install -y tar" ;;
  esac
}

check_prereqs() {
  need_cmd python3 "$(python_hint)"
  if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "join.sh: python3 >= 3.10 is required" >&2
    python_hint >&2
    exit 1
  fi
  need_cmd git "$(git_hint)"
  need_cmd curl "$(curl_hint)"
  need_cmd tar "$(tar_hint)"
}

pythonpath_root() {
  if [[ -d "${APP}/cluster" ]]; then
    echo "${APP}"
    return 0
  fi
  local script_path=""
  if [[ "${BASH_SOURCE[0]}" == /* ]] && [[ -f "${BASH_SOURCE[0]}" ]]; then
    script_path="${BASH_SOURCE[0]}"
  fi
  if [[ -n "$script_path" ]]; then
    local install_dir pkg_dir
    install_dir="$(cd "$(dirname "$script_path")" && pwd)"
    pkg_dir="$(cd "${install_dir}/.." && pwd)"
    if [[ -f "${pkg_dir}/registry.py" ]]; then
      echo "$(cd "${pkg_dir}/.." && pwd)"
      return 0
    fi
  fi
  if [[ -n "${PYTHONPATH:-}" ]]; then
    echo ""
    return 0
  fi
  return 1
}

run_registry_py() {
  local py="${VENV}/bin/python"
  if [[ ! -x "$py" ]]; then
    py="python3"
  fi
  local root
  if ! root="$(pythonpath_root)"; then
    echo "join.sh: cannot decode join code (cluster package not available)" >&2
    return 1
  fi
  local -a env_args=()
  if [[ -n "$root" ]]; then
    env_args=(env "PYTHONPATH=${root}${PYTHONPATH:+:${PYTHONPATH}}")
  fi
  "${env_args[@]}" "$py" "$@"
}

export BOB_JOIN_CODE_DECODE="$CODE"

if [[ "$DRY_RUN" -eq 1 ]]; then
  check_prereqs
  dry "would verify python3 >= 3.10, git, curl, tar"
  dry "would download ${TARBALL_URL} to a temporary directory"
  dry "would extract cluster/ into ${APP}/cluster (atomic replace via ${APP}.new)"
  dry "would create venv at ${VENV} and pip install -r ${APP}/cluster/requirements.txt"
  dry "would run cluster.join --check-only (hub reachability)"
  dry "would run cluster.join redeem"
  if command -v tailscale >/dev/null 2>&1; then
    dry "would print first line of: tailscale status --self"
  fi
  if BOB_JOIN_CODE_DECODE="$CODE" run_registry_py -c '
import os
from cluster.registry import decode_join_code
print(decode_join_code(os.environ["BOB_JOIN_CODE_DECODE"])["r"])
' >/dev/null 2>&1; then
    role="$(BOB_JOIN_CODE_DECODE="$CODE" run_registry_py -c '
import os
from cluster.registry import decode_join_code
print(decode_join_code(os.environ["BOB_JOIN_CODE_DECODE"])["r"])
')"
    if [[ "$role" == "guest" ]]; then
      if command -v docker >/dev/null 2>&1 || command -v podman >/dev/null 2>&1; then
        dry "guest role: docker or podman present for sandbox jobs"
      else
        dry "would warn: sandbox jobs need Docker or Podman"
      fi
    fi
  fi
  if [[ "$NO_SERVICE" -eq 0 ]]; then
    case "$OS_KIND" in
      darwin)
        dry "would write ~/Library/LaunchAgents/com.bob.cluster-node.plist and launchctl bootstrap"
        ;;
      *)
        if systemctl --user show-environment >/dev/null 2>&1; then
          dry "would write ~/.config/systemd/user/bob-cluster-node.service"
          dry "would systemctl --user daemon-reload enable --now bob-cluster-node"
          dry "would try loginctl enable-linger ${USER}"
        else
          dry "would write ~/.local/bin/bob-cluster-node nohup wrapper"
          dry "would add @reboot crontab line if crontab exists"
          dry "would start ~/.local/bin/bob-cluster-node now"
        fi
        ;;
    esac
  else
    dry "would skip service install (--no-service)"
  fi
  name_role="$(BOB_JOIN_CODE_DECODE="$CODE" run_registry_py -c '
import os
from cluster.registry import decode_join_code
d = decode_join_code(os.environ["BOB_JOIN_CODE_DECODE"])
print(d["n"], d["r"])
' 2>/dev/null || echo "node role")"
  read -r node_name node_role <<< "$name_role"
  echo "Bob cluster node: ${node_name:-?} (${node_role:-?})"
  case "$OS_KIND" in
    darwin)
      echo "Logs: tail -f ~/Library/Logs/bob-cluster-node.log"
      echo "Remove: launchctl bootout gui/$(id -u)/com.bob.cluster-node; rm -rf ~/.local/share/bob-cluster ~/.config/bob-cluster/node.env"
      ;;
    *)
      if systemctl --user show-environment >/dev/null 2>&1; then
        echo "Logs: journalctl --user -u bob-cluster-node -f"
        echo "Remove: systemctl --user disable --now bob-cluster-node; rm -rf ~/.local/share/bob-cluster ~/.config/bob-cluster/node.env"
      else
        echo "Logs: tail -f ~/.local/share/bob-cluster/node.log"
        echo "Remove: rm -f ~/.local/bin/bob-cluster-node; rm -rf ~/.local/share/bob-cluster ~/.config/bob-cluster/node.env"
      fi
      ;;
  esac
  exit 0
fi

check_prereqs

if command -v tailscale >/dev/null 2>&1; then
  tailscale status --self 2>/dev/null | head -n 1 || true
fi

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

echo "Downloading cluster installer payload (${REF})..."
curl -fsSL -o "${TMP}/repo.tgz" "${TARBALL_URL}"
tar -xzf "${TMP}/repo.tgz" -C "${TMP}"
CLUSTER_SRC="$(find "${TMP}" -mindepth 1 -maxdepth 2 -type d -name cluster | head -n 1)"
if [[ -z "$CLUSTER_SRC" || ! -d "$CLUSTER_SRC" ]]; then
  echo "join.sh: archive did not contain cluster/" >&2
  exit 1
fi

rm -rf "${APP}.new"
mkdir -p "${APP}.new"
mv "$CLUSTER_SRC" "${APP}.new/cluster"
rm -rf "${APP}"
mv "${APP}.new" "${APP}"

if [[ ! -d "${VENV}" ]]; then
  python3 -m venv "${VENV}"
fi
"${VENV}/bin/pip" install -q -r "${APP}/cluster/requirements.txt"

JOIN_LANE_ARGS=()
if [[ -n "$LANES" ]]; then
  JOIN_LANE_ARGS=(--max-lanes "$LANES")
fi

set +e
CHECK_OUT="$(cd "${APP}" && "${VENV}/bin/python" -m cluster.join --check-only --code "$CODE" 2>&1)"
CHECK_RC=$?
set -e
if [[ "$CHECK_RC" -eq 3 ]]; then
  echo "Install Tailscale (https://tailscale.com/download) and sign in to Jeff's tailnet, or accept the NucBox share link Jeff sent you, then re-run this same command." >&2
  exit 3
fi
if [[ "$CHECK_RC" -ne 0 ]]; then
  echo "$CHECK_OUT" >&2
  exit "$CHECK_RC"
fi

ROLE="$(BOB_JOIN_CODE_DECODE="$CODE" run_registry_py -c '
import os
from cluster.registry import decode_join_code
print(decode_join_code(os.environ["BOB_JOIN_CODE_DECODE"])["r"])
')"
if [[ "$ROLE" == "guest" ]]; then
  if ! command -v docker >/dev/null 2>&1 && ! command -v podman >/dev/null 2>&1; then
    echo "Warning: Docker or Podman is not installed; sandbox jobs will be refused until one is available." >&2
  fi
fi

JOIN_OUT="$(cd "${APP}" && "${VENV}/bin/python" -m cluster.join --code "$CODE" "${JOIN_LANE_ARGS[@]}" 2>&1)" || {
  echo "$JOIN_OUT" >&2
  exit 4
}
echo "$JOIN_OUT"

node_name=""
node_role=""
if [[ "$JOIN_OUT" =~ node\ \'([^\']+)\'\ joined\ as\ ([a-z]+) ]]; then
  node_name="${BASH_REMATCH[1]}"
  node_role="${BASH_REMATCH[2]}"
fi

install_service() {
  local py="${VENV}/bin/python"
  case "$OS_KIND" in
    darwin)
      local plist="${HOME}/Library/LaunchAgents/com.bob.cluster-node.plist"
      mkdir -p "${HOME}/Library/LaunchAgents" "${HOME}/Library/Logs"
      cat >"$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.bob.cluster-node</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>${APP}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${py}</string>
    <string>-m</string>
    <string>cluster.mesh_worker</string>
    <string>--env</string>
    <string>${NODE_ENV}</string>
  </array>
  <key>StandardOutPath</key>
  <string>${HOME}/Library/Logs/bob-cluster-node.log</string>
  <key>StandardErrorPath</key>
  <string>${HOME}/Library/Logs/bob-cluster-node.log</string>
</dict>
</plist>
PLIST
      launchctl bootout "gui/$(id -u)/com.bob.cluster-node" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$plist"
      ;;
    *)
      if systemctl --user show-environment >/dev/null 2>&1; then
        local unit_dir="${HOME}/.config/systemd/user"
        mkdir -p "$unit_dir"
        cat >"${unit_dir}/bob-cluster-node.service" <<UNIT
[Unit]
Description=Bob cluster mesh worker
After=network-online.target

[Service]
Type=simple
Restart=always
RestartSec=10
WorkingDirectory=${APP}
ExecStart=${py} -m cluster.mesh_worker --env ${NODE_ENV}

[Install]
WantedBy=default.target
UNIT
        systemctl --user daemon-reload
        systemctl --user enable --now bob-cluster-node.service
        if ! loginctl enable-linger "$USER" 2>/dev/null; then
          echo "Run: sudo loginctl enable-linger $USER" >&2
        fi
      else
        mkdir -p "${HOME}/.local/bin" "${HOME}/.local/share/bob-cluster"
        local wrapper="${HOME}/.local/bin/bob-cluster-node"
        cat >"$wrapper" <<'WRAP'
#!/usr/bin/env bash
set -euo pipefail
APP="${HOME}/.local/share/bob-cluster/app"
VENV="${HOME}/.local/share/bob-cluster/venv"
NODE_ENV="${HOME}/.config/bob-cluster/node.env"
LOG="${HOME}/.local/share/bob-cluster/node.log"
cd "$APP"
while true; do
  "$VENV/bin/python" -m cluster.mesh_worker --env "$NODE_ENV" >>"$LOG" 2>&1 || true
  sleep 10
done
WRAP
        chmod +x "$wrapper"
        if crontab -l >/dev/null 2>&1; then
          (crontab -l 2>/dev/null | grep -v 'bob-cluster-node' || true
           echo "@reboot ${wrapper} >/dev/null 2>&1 &") | crontab -
        fi
        if ! pgrep -f 'bob-cluster-node' >/dev/null 2>&1; then
          nohup "$wrapper" >/dev/null 2>&1 &
        fi
      fi
      ;;
  esac
}

if [[ "$NO_SERVICE" -eq 0 ]]; then
  install_service
fi

echo "Bob cluster node: ${node_name:-joined} (${node_role:-$ROLE})"
case "$OS_KIND" in
  darwin)
    echo "Logs: tail -f ~/Library/Logs/bob-cluster-node.log"
    echo "Remove: launchctl bootout gui/$(id -u)/com.bob.cluster-node; rm -rf ~/.local/share/bob-cluster ~/.config/bob-cluster/node.env"
    ;;
  *)
    if systemctl --user show-environment >/dev/null 2>&1; then
      echo "Logs: journalctl --user -u bob-cluster-node -f"
      echo "Remove: systemctl --user disable --now bob-cluster-node; rm -rf ~/.local/share/bob-cluster ~/.config/bob-cluster/node.env"
    else
      echo "Logs: tail -f ~/.local/share/bob-cluster/node.log"
      echo "Remove: rm -f ~/.local/bin/bob-cluster-node; rm -rf ~/.local/share/bob-cluster ~/.config/bob-cluster/node.env"
    fi
    ;;
esac

unset BOB_JOIN_CODE_DECODE
