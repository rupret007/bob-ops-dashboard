#!/usr/bin/env bash
# End-to-end mesh check on one Linux/WSL host against a THROWAWAY nats-server
# on a random loopback port. Never touches the live hub, its config or tokens.
#   bash cluster/e2e-local.sh [REF] [--llm]
# REF must be pushed to GitHub (join.sh downloads it). --llm also runs one tiny
# llm.composer job through the local cursor-agent login.
set -euo pipefail
REF="${1:?usage: e2e-local.sh <pushed git sha> [--llm]}"
LLM="${2:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d /tmp/bob-mesh-e2e.XXXXXX)"
CFG="$T/hub"; HOME2="$T/home"; mkdir -p "$CFG" "$HOME2"; chmod 700 "$CFG"
PORT="$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')"
HTTP="$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')"
URL="nats://127.0.0.1:${PORT}"
NATS_BIN="$(command -v nats-server || echo "$HOME/.local/bin/nats-server")"
PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; rm -rf "$T"; }
trap cleanup EXIT
step() { echo "== $*"; }
gen() { python3 -c 'import secrets;print(secrets.token_urlsafe(24))'; }
{ echo "NUCBOX_TOKEN=$(gen)"; echo "DASHBOARD_TOKEN=$(gen)"; } > "$CFG/tokens.env"; chmod 600 "$CFG/tokens.env"
HUB=(--config-dir "$CFG" --conf-out "$CFG/nats.conf" --store-dir "$T/js" --pidfile "$CFG/nats.pid" --port "$PORT" --http "127.0.0.1:${HTTP}")
export PYTHONPATH="$ROOT"
PY="${PY:-python3}"

step "render throwaway hub config (port $PORT)"
"$PY" -m cluster.bobctl init "${HUB[@]}" --nats "$URL" >/dev/null 2>&1
"$NATS_BIN" -t -c "$CFG/nats.conf" >/dev/null 2>&1 && echo "config valid"
"$NATS_BIN" -c "$CFG/nats.conf" -P "$CFG/nats.pid" >"$T/nats.log" 2>&1 & PIDS+=($!)
sleep 1
"$PY" -m cluster.bobctl setup-streams "${HUB[@]}" --nats "$URL" | sed 's/^/   /'

step "start joind"
"$PY" -m cluster.joind "${HUB[@]}" --live --nats "$URL" >"$T/joind.log" 2>&1 & PIDS+=($!)
sleep 1

step "mint single-use member code (not printed)"
"$PY" -m cluster.bobctl mint "${HUB[@]}" --live --nats "$URL" --name e2e-node --role member \
  --caps llm.composer,llm.codex.high,shell.test --url "$URL" --ref "$REF" > "$T/mint.txt" 2>/dev/null
sleep 1
grep -E "^Join code|^Caps" "$T/mint.txt" | sed 's/^/   /'
CODE="$(grep -o "bobjoin1:[A-Za-z0-9_-]*" "$T/mint.txt" | head -1)"

step "run the one-command installer from GitHub at $REF (isolated HOME, no service)"
curl -fsSL "https://raw.githubusercontent.com/rupret007/bob-ops-dashboard/${REF}/cluster/install/join.sh" -o "$T/join.sh"
HOME="$HOME2" BOB_JOIN_CODE="$CODE" bash "$T/join.sh" --ref "$REF" --no-service --lanes 2 2>&1 | sed 's/^/   /'
sleep 2
NODE_ENV="$HOME2/.config/bob-cluster/node.env"
stat -c '   node.env mode %a' "$NODE_ENV"

step "replay the same code (must fail)"
if BOB_JOIN_CODE="$CODE" "$PY" -m cluster.join --env-out "$T/replay.env" >/dev/null 2>&1; then echo "   REPLAY WORKED (BAD)"; exit 1; else echo "   replay refused"; fi

step "start the node agent (real HOME so real CLI logins are detected)"
APP="$HOME2/.local/share/bob-cluster"
(cd "$APP/app" && "$APP/venv/bin/python" -m cluster.mesh_worker --env "$NODE_ENV" >"$T/worker.log" 2>&1) & PIDS+=($!)
sleep 4
step "live capability heartbeat as the dashboard sees it"
"$PY" -m cluster.bobctl caps "${HUB[@]}" --nats "$URL" --seconds 17 | sed 's/^/   /'

step "capability-routed shell.test job (runs the cluster unit tests at $REF)"
"$PY" -m cluster.bobctl submit "${HUB[@]}" --nats "$URL" --cap shell.test --ref "$REF" \
  --cmd "python3 -m unittest discover -s cluster/tests -v" --timeout 600 --wait 600 \
  | "$PY" -c 'import json,sys;d=json.load(sys.stdin);r=d.get("result") or {};print("   job",d["id"],"cap",d["cap"],"->",r.get("node"),r.get("status"),"rc",r.get("rc"),"in",r.get("duration_s"),"s");t=(r.get("tail") or "").strip().splitlines();print("   tail:", " | ".join(t[-3:])[:300])'

if [ "$LLM" = "--llm" ]; then
  step "llm.composer job through this machine's own cursor-agent login"
  printf 'Reply with exactly the word PONG and nothing else. Do not edit, create or delete any files.' > "$T/p.txt"
  "$PY" -m cluster.bobctl submit "${HUB[@]}" --nats "$URL" --cap llm.composer --ref "$REF" --prompt-file "$T/p.txt" \
    --timeout 300 --wait 330 \
    | "$PY" -c 'import json,sys;d=json.load(sys.stdin);r=d.get("result") or {};t=(r.get("tail") or "").strip().splitlines();print("   job",d["id"],"->",r.get("node"),r.get("status"),"rc",r.get("rc"),"last line:",(t[-1] if t else "")[:60])'
fi

step "revoke the node"
"$PY" -m cluster.bobctl revoke "${HUB[@]}" --live --nats "$URL" e2e-node | sed 's/^/   /'
sleep 2
if grep -qiE "authorization|closed|disconnect" "$T/worker.log" "$T/nats.log"; then echo "   worker connection dropped by the hub"; else echo "   (no disconnect line seen)"; fi
echo "== done; throwaway hub removed"
