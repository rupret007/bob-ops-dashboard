#!/bin/bash
# Go-Live Script for NucBox (WSL/Linux)
#
# Updates the worktree, reinstalls, runs self-check, and ONLY if ALL checks PASS:
# - Switches dispatcher + conductor to --live mode
# - Loads the Karen poller
#
# If ANY check fails, stays in shadow mode and reports why.
#
# Usage: ~/storypool-install/cluster/control_plane/install/go-live-nucbox.sh
#
# Kill switch: storypool-stop.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SYSTEMD_DIR="$HOME/.config/systemd/user"

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
    if [[ "$(uname)" == "Darwin" ]]; then
        log_error "This script is for Linux/WSL (NucBox) only"
        exit 1
    fi
}

update_worktree() {
    log_header "Updating Worktree"
    
    cd "$REPO_ROOT"
    
    local target_branch="cursor/storypool-all-in-one-47c7"
    
    if ! git fetch origin "$target_branch" 2>&1; then
        log_fail "git fetch failed"
        FAILURES+=("git fetch")
        ALL_PASS=false
        return 1
    fi
    
    if git merge-base --is-ancestor HEAD "origin/$target_branch" 2>/dev/null; then
        if ! git checkout "$target_branch" 2>/dev/null; then
            git checkout -B "$target_branch" "origin/$target_branch" 2>&1
        fi
        git merge --ff-only "origin/$target_branch" 2>&1 || true
    else
        log_info "HEAD diverged or detached, resetting to origin/$target_branch"
        git checkout -B "$target_branch" "origin/$target_branch" 2>&1
    fi
    
    log_pass "Worktree updated to $(git rev-parse --short HEAD)"
}

reinstall() {
    log_header "Reinstalling Components"
    
    # Run install script in subshell to prevent set -e from propagating
    # The script has its own set -euo pipefail but we handle its exit ourselves
    local install_output
    local install_exit=0
    install_output=$(bash "$SCRIPT_DIR/install-all-nucbox.sh" 2>&1) || install_exit=$?
    
    if [[ $install_exit -ne 0 ]]; then
        log_fail "install-all-nucbox.sh failed (exit $install_exit)"
        echo "$install_output" | tail -20
        FAILURES+=("install")
        ALL_PASS=false
        return 1
    fi
    
    log_pass "Components reinstalled"
}

run_self_check() {
    log_header "Running Self-Check"
    
    if ! systemctl --user is-active storypool-shadow.timer &>/dev/null; then
        log_fail "shadow runner timer not active"
        FAILURES+=("shadow-runner")
        ALL_PASS=false
    else
        log_pass "shadow runner timer active"
    fi
    
    if ! systemctl --user is-active storypool-dispatch.timer &>/dev/null; then
        log_fail "dispatcher timer not active"
        FAILURES+=("dispatcher")
        ALL_PASS=false
    else
        log_pass "dispatcher timer active"
    fi
    
    if ! systemctl --user is-active storypool-conductor.timer &>/dev/null; then
        log_fail "conductor timer not active"
        FAILURES+=("conductor")
        ALL_PASS=false
    else
        log_pass "conductor timer active"
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
    local shadow_tmp
    shadow_tmp=$(mktemp)
    local shadow_exit=0
    (cd "$REPO_ROOT" && PYTHONPATH="$REPO_ROOT" python3 -m cluster.control_plane.shadow_runner --machine nucbox --json > "$shadow_tmp" 2>&1) || shadow_exit=$?
    if [[ $shadow_exit -eq 0 ]] && python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$shadow_tmp" 2>/dev/null; then
        log_pass "shadow tick ran successfully"
    else
        log_fail "shadow tick failed (exit $shadow_exit)"
        echo "    Output: $(head -c 300 "$shadow_tmp")"
        FAILURES+=("shadow-tick")
        ALL_PASS=false
    fi
    rm -f "$shadow_tmp"
    
    log_info "Testing conductor tick..."
    local conductor_tmp
    conductor_tmp=$(mktemp)
    local conductor_exit=0
    (cd "$REPO_ROOT" && PYTHONPATH="$REPO_ROOT" python3 -m cluster.control_plane.conductor --json > "$conductor_tmp" 2>&1) || conductor_exit=$?
    if [[ $conductor_exit -eq 0 ]] && python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$conductor_tmp" 2>/dev/null; then
        log_pass "conductor tick ran successfully"
    else
        log_fail "conductor tick failed (exit $conductor_exit)"
        echo "    Output: $(head -c 300 "$conductor_tmp")"
        FAILURES+=("conductor-tick")
        ALL_PASS=false
    fi
    rm -f "$conductor_tmp"
    
    if ! loginctl show-user "$USER" 2>/dev/null | grep -q "Linger=yes"; then
        log_warn "loginctl linger not enabled - services may stop after logout"
        log_warn "Run: loginctl enable-linger \$USER"
    fi
}

enable_live_mode() {
    log_header "Enabling LIVE Mode"
    
    local dispatch_service="$SYSTEMD_DIR/storypool-dispatch.service"
    local conductor_service="$SYSTEMD_DIR/storypool-conductor.service"
    local live_marker="$HOME/.config/storypool/live"
    
    systemctl --user stop storypool-dispatch.timer 2>/dev/null || true
    systemctl --user stop storypool-conductor.timer 2>/dev/null || true
    systemctl --user stop storypool-dispatch.service 2>/dev/null || true
    systemctl --user stop storypool-conductor.service 2>/dev/null || true
    
    # Add --live to ExecStart lines using exact sed that works
    if [[ -f "$dispatch_service" ]]; then
        sed -i '/^ExecStart=/{/--live/!s/$/ --live/}' "$dispatch_service"
        log_info "Ran sed on dispatcher service"
    else
        log_fail "Dispatcher service not found: $dispatch_service"
        ALL_PASS=false
        FAILURES+=("dispatch-service-missing")
    fi
    
    if [[ -f "$conductor_service" ]]; then
        sed -i '/^ExecStart=/{/--live/!s/$/ --live/}' "$conductor_service"
        log_info "Ran sed on conductor service"
    else
        log_fail "Conductor service not found: $conductor_service"
        ALL_PASS=false
        FAILURES+=("conductor-service-missing")
    fi
    
    # VERIFY --live is actually in the files, FAIL if not
    log_info "Verifying --live flag in service files..."
    
    if ! grep -q '^ExecStart=.*--live' "$dispatch_service" 2>/dev/null; then
        log_fail "VERIFICATION FAILED: --live NOT in dispatcher ExecStart"
        log_fail "Content: $(grep '^ExecStart=' "$dispatch_service" 2>/dev/null || echo 'not found')"
        ALL_PASS=false
        FAILURES+=("dispatch-live-verification")
        exit 1
    fi
    log_pass "Dispatcher has --live in ExecStart"
    
    if ! grep -q '^ExecStart=.*--live' "$conductor_service" 2>/dev/null; then
        log_fail "VERIFICATION FAILED: --live NOT in conductor ExecStart"
        log_fail "Content: $(grep '^ExecStart=' "$conductor_service" 2>/dev/null || echo 'not found')"
        ALL_PASS=false
        FAILURES+=("conductor-live-verification")
        exit 1
    fi
    log_pass "Conductor has --live in ExecStart"
    
    # Persist live marker so install/self-update won't strip --live
    mkdir -p "$(dirname "$live_marker")"
    echo "$(date -Iseconds)" > "$live_marker"
    log_info "Created live marker: $live_marker"
    
    log_info "Reloading systemd daemon..."
    systemctl --user daemon-reload
    
    log_info "Restarting timers..."
    systemctl --user restart storypool-dispatch.timer
    systemctl --user restart storypool-conductor.timer
    
    sleep 1
    
    if systemctl --user is-active storypool-dispatch.timer &>/dev/null; then
        log_pass "Dispatcher timer active"
    else
        log_fail "Dispatcher timer not active after restart"
        FAILURES+=("dispatch-timer-restart")
    fi
    
    if systemctl --user is-active storypool-conductor.timer &>/dev/null; then
        log_pass "Conductor timer active"
    else
        log_fail "Conductor timer not active after restart"
        FAILURES+=("conductor-timer-restart")
    fi
    
    log_pass "Dispatcher and Conductor now in LIVE mode"
}

skip_karen_poller() {
    log_header "Karen Poller"
    
    log_info "Karen poller runs on Mini only (it SSHs to NucBox for Codex)"
    log_info "NucBox provides the Codex backend; Mini runs the gate checks"
}

main() {
    check_os
    
    log_header "StoryPool Go-Live (NucBox)"
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
        skip_karen_poller
        
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
