#!/usr/bin/env bash
# Idempotently install pinned nats-server (linux-amd64) and cluster config.
set -euo pipefail

NAT_VERSION="v2.15.1"
NAT_TARBALL="nats-server-${NAT_VERSION}-linux-amd64.tar.gz"
NAT_URL="https://github.com/nats-io/nats-server/releases/download/${NAT_VERSION}/${NAT_TARBALL}"
NAT_SHA256="bf5edc7b1ce98e885c368cb43a886eb148dc5d0551d99f3cf6141fb5e1954c70"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CI_MODE=0
INSTALL_BIN="${HOME}/.local/bin"
CONFIG_DIR="${HOME}/.config/bob-cluster"
STATE_DIR="${HOME}/.local/state/bob-cluster/js"

usage() {
  echo "Usage: $0 [--ci DEST_BIN_DIR]" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ci)
      CI_MODE=1
      INSTALL_BIN="${2:?--ci requires destination directory}"
      shift 2
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

mkdir -p "${INSTALL_BIN}"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

echo "Downloading ${NAT_URL}..."
curl -fsSL -o "${TMP}/${NAT_TARBALL}" "${NAT_URL}"
(
  cd "${TMP}"
  echo "${NAT_SHA256}  ${NAT_TARBALL}" | sha256sum -c -
)

tar -xzf "${TMP}/${NAT_TARBALL}" -C "${TMP}"
BIN_SRC="${TMP}/nats-server-${NAT_VERSION}-linux-amd64/nats-server"
install -m 0755 "${BIN_SRC}" "${INSTALL_BIN}/nats-server"
echo "Installed nats-server to ${INSTALL_BIN}/nats-server"

if [[ "${CI_MODE}" -eq 1 ]]; then
  echo "CI mode: skipping token and config generation."
  exit 0
fi

mkdir -p "${CONFIG_DIR}" "${STATE_DIR}"
TOKENS_FILE="${CONFIG_DIR}/tokens.env"
if [[ ! -f "${TOKENS_FILE}" ]]; then
  echo "Generating ${TOKENS_FILE}..."
  gen_token() { openssl rand -hex 24; }
  {
    echo "# Bob cluster NATS tokens — never commit"
    echo "MINI_TOKEN=$(gen_token)"
    echo "NUCBOX_TOKEN=$(gen_token)"
    echo "MACBOOK_TOKEN=$(gen_token)"
    echo "DASHBOARD_TOKEN=$(gen_token)"
  } > "${TOKENS_FILE}"
  chmod 600 "${TOKENS_FILE}"
fi

# Existing installations need the new restricted identity without rotating
# any machine credentials. Never print tokens or place them in argv.
if ! grep -q '^COORD_TOKEN=' "${TOKENS_FILE}"; then
  printf '\nCOORD_TOKEN=%s\n' "$(openssl rand -hex 24)" >> "${TOKENS_FILE}"
fi
# A separate operator identity can provision only the registered buckets.
if ! grep -q '^COORDOP_TOKEN=' "${TOKENS_FILE}"; then
  printf '\nCOORDOP_TOKEN=%s\n' "$(openssl rand -hex 24)" >> "${TOKENS_FILE}"
fi
chmod 600 "${TOKENS_FILE}"

CONF_OUT="${CONFIG_DIR}/nats.conf"
python3 - "${TOKENS_FILE}" "${SCRIPT_DIR}/nats.conf.template" "${STATE_DIR}" "${CONF_OUT}" <<'PYRENDER'
import json
import sys
from pathlib import Path

tokens_path, template_path, state_dir, output_path = map(Path, sys.argv[1:])
sys.path.insert(0, str(template_path.parent.parent))
from cluster.nats_acl import render_coord_acl, verify_coord_acl
values = {}
for line in tokens_path.read_text().splitlines():
    key, separator, value = line.partition('=')
    if separator and not key.strip().startswith('#'):
        values[key.strip()] = value.strip().strip('"')
values['JS_STORE_DIR'] = str(state_dir)
text = render_coord_acl(template_path.read_text())
verify_coord_acl(text)
for key in ('MINI_TOKEN', 'NUCBOX_TOKEN', 'MACBOOK_TOKEN', 'DASHBOARD_TOKEN', 'COORD_TOKEN', 'COORDOP_TOKEN', 'JS_STORE_DIR'):
    if not values.get(key):
        raise SystemExit('Broker configuration requires all protected credentials')
    text = text.replace('{{' + key + '}}', json.dumps(values[key])[1:-1])
# Create restrictive output before writing credential contents.
import os
fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as output:
    os.fchmod(output.fileno(), 0o600)
    output.write(text)
PYRENDER
chmod 600 "${CONF_OUT}"
echo "Wrote ${CONF_OUT}"
