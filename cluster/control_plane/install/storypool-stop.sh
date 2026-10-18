#!/bin/bash
# StoryPool Kill Switch
#
# Emergency stop for ALL StoryPool services.
# Works on both Mac mini (launchd) and NucBox (systemd --user).
#
# Usage: ~/storypool-install/cluster/control_plane/install/storypool-stop.sh
#
# This will:
# - Stop and disable all timers/agents
# - Set conductor to PAUSED state
# - Leave logs intact for review

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_header() { echo -e "\n${BLUE}=== $1 ===${NC}"; }

stop_macos() {
    log_header "Stopping StoryPool Services (macOS)"
    
    local services=(
        "com.storypool.shadow"
        "com.storypool.dispatch"
        "com.storypool.conductor"
        "com.storypool.karen-poller"
    )
    
    for svc in "${services[@]}"; do
        local plist="$HOME/Library/LaunchAgents/$svc.plist"
        if launchctl list "$svc" &>/dev/null; then
            log_info "Stopping $svc..."
            launchctl unload "$plist" 2>/dev/null || true
        fi
    done
    
    log_info "All StoryPool services stopped"
}

stop_linux() {
    log_header "Stopping StoryPool Services (Linux/WSL)"
    
    local timers=(
        "storypool-shadow"
        "storypool-dispatch"
        "storypool-conductor"
        "storypool-karen-poller"
    )
    
    for timer in "${timers[@]}"; do
        if systemctl --user is-active "$timer.timer" &>/dev/null; then
            log_info "Stopping $timer.timer..."
            systemctl --user stop "$timer.timer" 2>/dev/null || true
            systemctl --user disable "$timer.timer" 2>/dev/null || true
        fi
    done
    
    systemctl --user daemon-reload
    
    log_info "All StoryPool services stopped"
}

set_paused_state() {
    log_header "Setting PAUSED State"
    
    local state_dir="$HOME/.local/share/storypool/conductor"
    mkdir -p "$state_dir"
    
    local state_file="$state_dir/merge-state.json"
    local alert_file="$state_dir/conductor-alert.json"
    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    
    cat > "$state_file" << EOF
{
  "paused": true,
  "pause_reason": "Manual stop via storypool-stop.sh at $timestamp",
  "consecutive_ci_failures": 0,
  "merges": []
}
EOF
    
    cat > "$alert_file" << EOF
{
  "timestamp": "$timestamp",
  "message": "Services stopped manually via storypool-stop.sh",
  "status": "PAUSED",
  "details": {
    "source": "storypool-stop.sh"
  }
}
EOF
    
    log_info "Conductor marked as PAUSED"
}

main() {
    log_header "StoryPool Emergency Stop"
    echo ""
    
    if [[ "$(uname)" == "Darwin" ]]; then
        stop_macos
    else
        stop_linux
    fi
    
    set_paused_state
    
    echo ""
    log_header "ALL SERVICES STOPPED"
    echo ""
    echo "To resume:"
    echo "  1. Fix any issues"
    echo "  2. Run the go-live script again:"
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "     $SCRIPT_DIR/go-live-mini.sh"
    else
        echo "     $SCRIPT_DIR/go-live-nucbox.sh"
    fi
    echo ""
    echo "Logs preserved at: ~/.local/share/storypool/"
}

main "$@"
