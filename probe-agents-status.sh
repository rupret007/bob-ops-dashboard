#!/usr/bin/env bash
# Mac probe for Bob ops dashboard agent strip (Codex / Cursor / Claude).
# Safe public fields only — no tokens, secrets, CSOne, or customer paths.
# Run on Jeff's Mac (Bob via ExternalShell or locally). Prints JSON to stdout.
#
# Usage:
#   ./probe-agents-status.sh
#   ./probe-agents-status.sh > /path/to/bob-ops-dashboard/agents-status.json
#   AGENTS_STATUS_JSON="$(./probe-agents-status.sh)" ./refresh.sh
#
# Optional env:
#   CODEX_PID_HINT   — known Codex PID to prefer when pgrep finds several
#   CODEX_LOCAL_URL  — default http://127.0.0.1:3210/meta
set -euo pipefail

CHECKED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
# Prefer America/Chicago wall clock when available (Jeff TZ)
if command -v python3 >/dev/null 2>&1; then
  CHECKED_AT="$(python3 - <<'PY'
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
    print(datetime.now(ZoneInfo("America/Chicago")).isoformat())
except Exception:
    print(datetime.now().astimezone().isoformat())
PY
)"
fi

CODEX_LOCAL_URL="${CODEX_LOCAL_URL:-http://127.0.0.1:3210/meta}"

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

# --- Codex: process + optional codex-local :3210 ---
codex_state="unknown"
codex_detail="no Codex process seen"
codex_pid=""

# Prefer explicit goal/exec style processes; avoid matching this probe script.
if command -v pgrep >/dev/null 2>&1; then
  for pat in \
    '[c]odex --enable goals' \
    '[c]odex.*goals' \
    '[c]odex-goal' \
    '[c]odex '; do
    hit="$(pgrep -lf "$pat" 2>/dev/null | head -n 1 || true)"
    if [[ -n "$hit" ]]; then
      codex_pid="$(awk '{print $1}' <<<"$hit")"
      # Keep detail short + safe: pid + truncated argv (no paths that look like customer/CSOne)
      safe_argv="$(sed -E 's#/(Users|home)/[^/]+/#~/ #g; s#CSOne|customer|token|secret|keeper##Ig' <<<"$hit" | cut -c1-120)"
      codex_state="running"
      codex_detail="PID ${codex_pid} · ${safe_argv}"
      break
    fi
  done
fi

if [[ -n "${CODEX_PID_HINT:-}" ]] && kill -0 "$CODEX_PID_HINT" 2>/dev/null; then
  codex_pid="$CODEX_PID_HINT"
  codex_state="running"
  codex_detail="PID ${codex_pid} (hint) running"
fi

local_bit=""
if command -v curl >/dev/null 2>&1; then
  code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 1 --max-time 2 "$CODEX_LOCAL_URL" 2>/dev/null || echo 000)"
  if [[ "$code" =~ ^2 ]]; then
    local_bit="codex-local :3210 ready"
  elif [[ "$code" == "401" || "$code" == "403" ]]; then
    local_bit="codex-local :3210 up (auth)"
  else
    local_bit="codex-local :3210 down"
  fi
fi

if [[ -n "$local_bit" ]]; then
  if [[ "$codex_state" == "running" ]]; then
    codex_detail="${codex_detail} · ${local_bit}"
  elif [[ "$local_bit" == *ready* || "$local_bit" == *auth* ]]; then
    codex_state="idle"
    codex_detail="$local_bit"
  else
    if [[ "$codex_state" == "unknown" ]]; then
      if command -v codex >/dev/null 2>&1 || [[ -x "$HOME/bin/codex" ]]; then
        codex_state="installed"
        codex_detail="codex CLI present · ${local_bit}"
      else
        codex_state="down"
        codex_detail="$local_bit"
      fi
    fi
  fi
elif [[ "$codex_state" == "unknown" ]]; then
  if command -v codex >/dev/null 2>&1 || [[ -x "$HOME/bin/codex" ]]; then
    codex_state="installed"
    codex_detail="codex CLI present; no live PID / :3210"
  else
    codex_state="down"
    codex_detail="codex CLI not found"
  fi
fi

# --- Cursor.app ---
cursor_state="unknown"
cursor_detail="Cursor.app not checked"
if [[ -d "/Applications/Cursor.app" ]]; then
  ver=""
  if [[ -f "/Applications/Cursor.app/Contents/Info.plist" ]] && command -v defaults >/dev/null 2>&1; then
    ver="$(defaults read /Applications/Cursor.app/Contents/Info.plist CFBundleShortVersionString 2>/dev/null || true)"
  fi
  running=0
  if command -v pgrep >/dev/null 2>&1; then
    if pgrep -x "Cursor" >/dev/null 2>&1 || pgrep -lf "Cursor.app/Contents/MacOS/Cursor" >/dev/null 2>&1; then
      running=1
    fi
  fi
  if [[ $running -eq 1 ]]; then
    cursor_state="running"
    cursor_detail="Cursor.app running${ver:+ · v$ver}"
  else
    cursor_state="installed"
    cursor_detail="Cursor.app installed${ver:+ · v$ver}, not running"
  fi
else
  cursor_state="down"
  cursor_detail="Cursor.app not in /Applications"
fi

# --- Claude.app + claude CLI ---
claude_state="unknown"
claude_detail="Claude not checked"
claude_ver=""
if command -v claude >/dev/null 2>&1; then
  claude_ver="$(claude --version 2>/dev/null | head -n 1 | tr -d '\r' | cut -c1-80 || true)"
fi
app_present=0
[[ -d "/Applications/Claude.app" ]] && app_present=1
app_running=0
if command -v pgrep >/dev/null 2>&1; then
  if pgrep -x "Claude" >/dev/null 2>&1 || pgrep -lf "Claude.app/Contents/MacOS" >/dev/null 2>&1; then
    app_running=1
  fi
fi

if [[ $app_running -eq 1 ]]; then
  claude_state="running"
  claude_detail="Claude.app running${claude_ver:+ · $claude_ver}"
elif [[ $app_present -eq 1 && -n "$claude_ver" ]]; then
  claude_state="installed"
  claude_detail="Claude.app + ${claude_ver}"
elif [[ $app_present -eq 1 ]]; then
  claude_state="installed"
  claude_detail="Claude.app installed"
elif [[ -n "$claude_ver" ]]; then
  claude_state="installed"
  claude_detail="$claude_ver"
else
  claude_state="down"
  claude_detail="Claude.app / claude CLI not found"
fi

# Emit JSON object with agents array (refresh.sh merges this).
python3 - "$CHECKED_AT" "$codex_state" "$codex_detail" "$cursor_state" "$cursor_detail" "$claude_state" "$claude_detail" <<'PY'
import json, sys
checked_at, cs, cd, us, ud, ls, ld = sys.argv[1:8]
out = {
  "agents": [
    {"id": "codex", "name": "Codex", "state": cs, "detail": cd[:200], "checked_at": checked_at},
    {"id": "cursor", "name": "Cursor", "state": us, "detail": ud[:200], "checked_at": checked_at},
    {"id": "claude", "name": "Claude", "state": ls, "detail": ld[:200], "checked_at": checked_at},
  ],
  "source": "probe-agents-status.sh",
  "checked_at": checked_at,
}
print(json.dumps(out, indent=2))
PY
