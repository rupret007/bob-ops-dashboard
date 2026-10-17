#!/usr/bin/env bash
# Compatible with macOS /bin/bash 3.2: no empty-array expansion under set -u.
# One-command Bob cluster node join (macOS, Linux, WSL). Never enable xtrace (secrets).
set -euo pipefail

REF=""
SHA256=""
CODE=""
PROMPT_CODE=0
LANES=""
DRY_RUN=0
NO_SERVICE=0

usage() {
  echo "Usage: join.sh --ref <40-hex SHA> --sha256 <archive SHA256> [--prompt-code] [--lanes N] [--dry-run] [--no-service]" >&2
  echo "       Set BOB_JOIN_CODE via hidden input, or use --prompt-code." >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt-code)
      PROMPT_CODE=1
      shift
      ;;
    --ref)
      REF="${2:?--ref requires a value}"
      shift 2
      ;;
    --sha256)
      SHA256="${2:?--sha256 requires a value}"
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
      echo "join.sh: unsupported argument (codes must not be passed in argv)" >&2
      usage
      ;;
  esac
done

if [[ ! "$REF" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "join.sh: --ref must be a full 40-hex commit SHA" >&2
  exit 1
fi
if [[ ! "$SHA256" =~ ^[0-9a-fA-F]{64}$ ]]; then
  echo "join.sh: --sha256 must be a 64-hex archive checksum" >&2
  exit 1
fi
SHA256="$(printf '%s' "$SHA256" | tr 'A-F' 'a-f')"

if [[ "$PROMPT_CODE" -eq 1 ]]; then
  read -r -s -p 'Join code (hidden): ' CODE </dev/tty
  printf '\n' >/dev/tty
fi

if [[ -z "$CODE" && -n "${BOB_JOIN_CODE:-}" ]]; then
  CODE="$BOB_JOIN_CODE"
fi

if [[ -z "$CODE" ]]; then
  echo "join.sh: --prompt-code or BOB_JOIN_CODE is required" >&2
  exit 1
fi

APP="${HOME}/.local/share/bob-cluster/app"
VENV="${HOME}/.local/share/bob-cluster/venv"
PYDEPS="${HOME}/.local/share/bob-cluster/pydeps"
BOBPY="${HOME}/.local/share/bob-cluster/bin/bob-python"
# Nodes only need nats-py (pure Python). Pinned wheel + sha256 for hosts with
# neither venv nor pip (e.g. stock Ubuntu/WSL without python3-venv).
NATS_WHEEL_URL="https://files.pythonhosted.org/packages/48/a3/16cec37172144d3362d7551bb823a098b2b2903281fc095dfe4480b8c2fd/nats_py-2.16.0-py3-none-any.whl"
NATS_WHEEL_SHA256="aeb1ff123966c05833d26c7df7e1d54c1c6d32b612428b21677a2e921f1fecae"
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

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
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
  local py="${BOBPY}"
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
  ${env_args[@]+"${env_args[@]}"} "$py" "$@"
}

export BOB_JOIN_CODE_DECODE="$CODE"

if [[ "$DRY_RUN" -eq 1 ]]; then
  check_prereqs
  dry "would verify python3 >= 3.10, git, curl, tar"
  dry "would download ${TARBALL_URL} to a temporary directory"
  dry "would verify archive SHA256 ${SHA256} before extraction"
  dry "would extract cluster/ into ${APP}/cluster (atomic replace via ${APP}.new)"
  dry "would install nats-py via venv+pip, else pip --target, else the pinned wheel (sha256-checked) into ${PYDEPS}"
  dry "would write launcher ${BOBPY}"
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
        dry "would write launchd plist ~/Library/LaunchAgents/com.bob.cluster-node.plist and launchctl bootstrap"
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
if [[ "$(sha256_of "${TMP}/repo.tgz")" != "${SHA256}" ]]; then
  echo "join.sh: archive checksum mismatch; refusing to install" >&2
  exit 1
fi
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

install_deps() {
  mkdir -p "$(dirname "${BOBPY}")"
  if [[ -x "${VENV}/bin/python" ]] || python3 -m venv "${VENV}" >/dev/null 2>&1; then
    if "${VENV}/bin/python" -m pip install -q "nats-py==2.16.0" >/dev/null 2>&1; then
      printf '#!/bin/sh\nexport PYTHONPATH="%s"\nexec "%s" "$@"\n' "${APP}" "${VENV}/bin/python" > "${BOBPY}"
      chmod 755 "${BOBPY}"; echo "Python deps: venv"; return 0
    fi
    rm -rf "${VENV}"
  fi
  rm -rf "${PYDEPS}"; mkdir -p "${PYDEPS}"
  if python3 -m pip --version >/dev/null 2>&1 && python3 -m pip install -q --target "${PYDEPS}" "nats-py==2.16.0" >/dev/null 2>&1; then
    echo "Python deps: pip --target"
  else
    curl -fsSL -o "${TMP}/nats.whl" "${NATS_WHEEL_URL}"
    if [[ "$(sha256_of "${TMP}/nats.whl")" != "${NATS_WHEEL_SHA256}" ]]; then
      echo "join.sh: nats-py wheel checksum mismatch; refusing to install" >&2; exit 1
    fi
    python3 -m zipfile -e "${TMP}/nats.whl" "${PYDEPS}"
    echo "Python deps: pinned wheel (no venv/pip on this host)"
  fi
  printf '#!/bin/sh\nexport PYTHONPATH="%s:%s"\nexec python3 "$@"\n' "${APP}" "${PYDEPS}" > "${BOBPY}"
  chmod 755 "${BOBPY}"
}
install_deps

JOIN_LANE_ARGS=()
if [[ -n "$LANES" ]]; then
  JOIN_LANE_ARGS=(--max-lanes "$LANES")
fi

set +e
CHECK_OUT="$(cd "${APP}" && BOB_JOIN_CODE="$CODE" "${BOBPY}" -m cluster.join --check-only 2>&1)"
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

# The code travels in the environment, not argv, so it never shows up in `ps`.
JOIN_OUT="$(cd "${APP}" && BOB_JOIN_CODE="$CODE" "${BOBPY}" -m cluster.join ${JOIN_LANE_ARGS[@]+"${JOIN_LANE_ARGS[@]}"} 2>&1)" || {
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
  local py="${BOBPY}"
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
BOBPY="${HOME}/.local/share/bob-cluster/bin/bob-python"
NODE_ENV="${HOME}/.config/bob-cluster/node.env"
LOG="${HOME}/.local/share/bob-cluster/node.log"
cd "$APP"
while true; do
  "$BOBPY" -m cluster.mesh_worker --env "$NODE_ENV" >>"$LOG" 2>&1 || true
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
