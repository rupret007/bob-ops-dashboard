#!/bin/bash
# Enable Karen Poller on Mac mini
#
# Installs and loads just the Karen poller, then runs one dry-run poll
# as a self-check.
#
# Karen runs on Mini only (it SSHs to NucBox for Codex).
# The poller:
# - Polls repos for PRs with 'needs-karen' label
# - Runs the gate (two-reviewer agreement required for PASS)
# - Posts KARENPASS/KARENFAIL comments at exact head SHA
# - Respects never-touch repos (rad-dad-show-night)
#
# Usage: ~/storypool-install/cluster/control_plane/install/enable-karen-poller-mini.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$SCRIPT_DIR/launchd-reload.sh"

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

check_os() {
    if [[ "$(uname)" != "Darwin" ]]; then
        log_error "Karen poller runs on macOS (Mac mini) only"
        log_error "NucBox provides Codex backend; Mini runs the gate"
        exit 1
    fi
}

check_prerequisites() {
    log_header "Checking Prerequisites"
    
    if ! command -v gh &>/dev/null; then
        log_error "gh CLI not found. Install with: brew install gh"
        exit 1
    fi
    log_pass "gh CLI found"
    
    if ! gh auth status &>/dev/null; then
        log_error "gh not authenticated. Run: gh auth login"
        exit 1
    fi
    log_pass "gh authenticated"
    
    if ! command -v python3 &>/dev/null; then
        log_error "python3 not found"
        exit 1
    fi
    log_pass "python3 found"
    
    local karen_cli="$HOME/.local/bin/karen-gate"
    if [[ ! -x "$karen_cli" ]]; then
        log_error "karen-gate CLI not found at $karen_cli"
        log_error "Run install-all-mini.sh first"
        exit 1
    fi
    log_pass "karen-gate CLI found"
}

install_plist() {
    log_header "Installing Karen Poller Plist"
    
    local poller_plist_src="$SCRIPT_DIR/com.storypool.karen-poller.plist"
    local poller_plist_dst="$HOME/Library/LaunchAgents/com.storypool.karen-poller.plist"
    
    if [[ ! -f "$poller_plist_src" ]]; then
        log_error "Poller plist not found at $poller_plist_src"
        exit 1
    fi
    
    mkdir -p "$HOME/Library/LaunchAgents"
    mkdir -p "$HOME/.local/share/karen"
    
    sed -e "s|__REPO_PATH__|$REPO_ROOT|g" \
        -e "s|__HOME__|$HOME|g" \
        -e "s|__PATH__|$PATH|g" \
        "$poller_plist_src" > "$poller_plist_dst"
    
    log_pass "Plist installed to $poller_plist_dst"
}

load_poller() {
    log_header "Loading Karen Poller"
    
    local poller_plist_dst="$HOME/Library/LaunchAgents/com.storypool.karen-poller.plist"
    
    storypool_reload_agent "$poller_plist_dst"
    
    if launchctl list com.storypool.karen-poller &>/dev/null; then
        log_pass "Karen poller loaded (polls every 10 min)"
    else
        log_fail "Failed to load Karen poller"
        exit 1
    fi
}

run_dry_poll() {
    log_header "Running Dry-Run Poll (Self-Check)"
    
    local poll_tmp
    poll_tmp=$(mktemp)
    local poll_exit
    
    log_info "Running: karen-gate poll --once --no-comment --keep-label"
    
    (cd "$REPO_ROOT" && PYTHONPATH="$REPO_ROOT" python3 -m cluster.control_plane.local_karen.poller \
        --once --no-comment --keep-label \
        --repos "rupret007/webjam" > "$poll_tmp" 2>&1)
    poll_exit=$?
    
    if [[ $poll_exit -eq 0 ]]; then
        log_pass "Dry-run poll completed successfully"
        echo ""
        echo "Poll output:"
        cat "$poll_tmp"
    else
        log_fail "Dry-run poll failed (exit $poll_exit)"
        echo ""
        echo "Poll output:"
        cat "$poll_tmp"
        rm -f "$poll_tmp"
        exit 1
    fi
    
    rm -f "$poll_tmp"
}

main() {
    check_os
    
    log_header "Enable Karen Poller (Mac mini)"
    echo "Repo: $REPO_ROOT"
    echo ""
    
    check_prerequisites
    install_plist
    load_poller
    run_dry_poll
    
    echo ""
    log_header "Karen Poller ENABLED"
    echo ""
    echo "The poller will:"
    echo "  - Poll every 10 minutes for PRs with 'needs-karen' label"
    echo "  - Run gate with two-reviewer agreement"
    echo "  - Post KARENPASS/KARENFAIL comments at exact head SHA"
    echo "  - Skip never-touch repos (rad-dad-show-night)"
    echo ""
    echo "Logs: ~/.local/share/karen/poller.log"
    echo ""
    echo "To disable:"
    echo "  launchctl unload ~/Library/LaunchAgents/com.storypool.karen-poller.plist"
}

main "$@"
