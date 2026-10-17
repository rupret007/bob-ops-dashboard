#!/bin/bash
# Go-Live Script for Mac mini
#
# Updates the worktree, reinstalls, runs self-check, and ONLY if ALL checks PASS:
# - Switches dispatcher + conductor to --live mode
# - Loads the Karen poller
#
# If ANY check fails, stays in shadow mode and reports why.
#
# Usage: ~/storypool-install/cluster/control_plane/install/go-live-mini.sh
#
# Kill switch: storypool-stop.sh

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
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_header() { echo -e "\n${BLUE}=== $1 ===${NC}"; }
log_pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
log_fail() { echo -e "${RED}[FAIL]${NC} $1"; }

ALL_PASS=true
FAILURES=()

check_os() {
    if [[ "$(uname)" != "Darwin" ]]; then
        log_error "This script is for macOS (Mac mini) only"
        exit 1
    fi
}

update_worktree() {
    log_header "Updating Worktree"
    
    cd "$REPO_ROOT"
    
    if ! git fetch origin 2>&1; then
        log_fail "git fetch failed"
        FAILURES+=("git fetch")
        ALL_PASS=false
        return 1
    fi
    
    local branch
    branch=$(git rev-parse --abbrev-ref HEAD)
    
    if ! git pull origin "$branch" 2>&1; then
        log_fail "git pull failed"
        FAILURES+=("git pull")
        ALL_PASS=false
        return 1
    fi
    
    log_pass "Worktree updated to $(git rev-parse --short HEAD)"
}

reinstall() {
    log_header "Reinstalling Components"
    
    if ! bash "$SCRIPT_DIR/install-all-mini.sh" 2>&1; then
        log_fail "install-all-mini.sh failed"
        FAILURES+=("install")
        ALL_PASS=false
        return 1
    fi
    
    log_pass "Components reinstalled"
}

run_self_check() {
    log_header "Running Self-Check"
    
    if ! launchctl list com.storypool.shadow &>/dev/null; then
        log_fail "shadow runner not loaded"
        FAILURES+=("shadow-runner")
        ALL_PASS=false
    else
        log_pass "shadow runner loaded"
    fi
    
    if ! launchctl list com.storypool.dispatch &>/dev/null; then
        log_fail "dispatcher not loaded"
        FAILURES+=("dispatcher")
        ALL_PASS=false
    else
        log_pass "dispatcher loaded"
    fi
    
    if ! launchctl list com.storypool.conductor &>/dev/null; then
        log_fail "conductor not loaded"
        FAILURES+=("conductor")
        ALL_PASS=false
    else
        log_pass "conductor loaded"
    fi
    
    if ! command -v karen-gate &>/dev/null && [[ ! -x "$HOME/.local/bin/karen-gate" ]]; then
        log_fail "karen-gate CLI not found"
        FAILURES+=("karen-gate")
        ALL_PASS=false
    else
        if ! PATH="$HOME/.local/bin:$PATH" karen-gate --help &>/dev/null; then
            log_fail "karen-gate --help failed"
            FAILURES+=("karen-gate-help")
            ALL_PASS=false
        else
            log_pass "karen-gate CLI available"
        fi
    fi
    
    log_info "Testing shadow tick..."
    local shadow_output
    local shadow_exit
    shadow_output=$(cd "$REPO_ROOT" && PYTHONPATH="$REPO_ROOT" python3 -m cluster.control_plane.shadow_runner --machine mini --json 2>&1)
    shadow_exit=$?
    if [[ $shadow_exit -eq 0 ]] && echo "$shadow_output" | head -1 | grep -q '"timestamp"'; then
        log_pass "shadow tick ran successfully"
    else
        log_fail "shadow tick failed (exit $shadow_exit)"
        echo "    Output: ${shadow_output:0:300}"
        FAILURES+=("shadow-tick")
        ALL_PASS=false
    fi
    
    log_info "Testing conductor tick..."
    local conductor_output
    local conductor_exit
    conductor_output=$(cd "$REPO_ROOT" && PYTHONPATH="$REPO_ROOT" python3 -m cluster.control_plane.conductor --json 2>&1)
    conductor_exit=$?
    if [[ $conductor_exit -eq 0 ]] && echo "$conductor_output" | grep -q '"timestamp"'; then
        log_pass "conductor tick ran successfully"
    else
        log_fail "conductor tick failed (exit $conductor_exit)"
        echo "    Output: ${conductor_output:0:300}"
        FAILURES+=("conductor-tick")
        ALL_PASS=false
    fi
}

enable_live_mode() {
    log_header "Enabling LIVE Mode"
    
    local dispatch_plist="$HOME/Library/LaunchAgents/com.storypool.dispatch.plist"
    local conductor_plist="$HOME/Library/LaunchAgents/com.storypool.conductor.plist"
    
    launchctl unload "$dispatch_plist" 2>/dev/null || true
    launchctl unload "$conductor_plist" 2>/dev/null || true
    
    sed -i '' 's|<!-- <string>--live</string> -->|<string>--live</string>|g' "$dispatch_plist" 2>/dev/null || true
    sed -i '' 's|<!--<string>--live</string>-->|<string>--live</string>|g' "$dispatch_plist" 2>/dev/null || true
    
    sed -i '' 's|<!-- <string>--live</string> -->|<string>--live</string>|g' "$conductor_plist" 2>/dev/null || true
    sed -i '' 's|<!--<string>--live</string>-->|<string>--live</string>|g' "$conductor_plist" 2>/dev/null || true
    
    if ! grep -q '<string>--live</string>' "$dispatch_plist"; then
        sed -i '' 's|</array>|<string>--live</string>\n</array>|' "$dispatch_plist"
    fi
    if ! grep -q '<string>--live</string>' "$conductor_plist"; then
        sed -i '' 's|</array>|<string>--live</string>\n</array>|' "$conductor_plist"
    fi
    
    launchctl load "$dispatch_plist"
    launchctl load "$conductor_plist"
    
    log_pass "Dispatcher and Conductor now in LIVE mode"
}

load_karen_poller() {
    log_header "Loading Karen Poller"
    
    local poller_plist_src="$SCRIPT_DIR/com.storypool.karen-poller.plist"
    local poller_plist_dst="$HOME/Library/LaunchAgents/com.storypool.karen-poller.plist"
    
    if [[ -f "$poller_plist_src" ]]; then
        sed -e "s|__REPO_PATH__|$REPO_ROOT|g" \
            -e "s|__HOME__|$HOME|g" \
            "$poller_plist_src" > "$poller_plist_dst"
        
        launchctl unload "$poller_plist_dst" 2>/dev/null || true
        launchctl load "$poller_plist_dst"
        
        log_pass "Karen poller loaded"
    else
        log_warn "Karen poller plist not found, skipping"
    fi
}

main() {
    check_os
    
    log_header "StoryPool Go-Live (Mac mini)"
    echo "Repo: $REPO_ROOT"
    echo ""
    
    update_worktree
    reinstall
    run_self_check
    
    echo ""
    log_header "Pre-Live Check Results"
    
    if $ALL_PASS; then
        log_pass "All checks PASSED"
        echo ""
        
        enable_live_mode
        load_karen_poller
        
        echo ""
        log_header "LIVE MODE ENABLED"
        echo ""
        echo "Safety limits active:"
        echo "  - Max 3 merges/hour, 15/day"
        echo "  - Auto-pause on 2 consecutive CI failures"
        echo "  - Two-reviewer agreement required for KARENPASS"
        echo "  - Never-touch repos blocked"
        echo ""
        echo "To stop all services:"
        echo "  $SCRIPT_DIR/storypool-stop.sh"
        echo ""
        echo "Logs: ~/.local/share/storypool/"
        
    else
        log_fail "Some checks FAILED - staying in SHADOW mode"
        echo ""
        echo "Failed checks:"
        for f in "${FAILURES[@]}"; do
            echo "  - $f"
        done
        echo ""
        echo "Fix the issues above and re-run this script."
        exit 1
    fi
}

main "$@"
