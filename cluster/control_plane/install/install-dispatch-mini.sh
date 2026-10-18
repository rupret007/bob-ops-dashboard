#!/bin/bash
# Install StoryPool Dispatcher on Mac mini
#
# This script:
# - Creates the log directory
# - Installs the launchd plist to ~/Library/LaunchAgents
# - Loads the agent
#
# USER-LEVEL INSTALLATION
# Does NOT touch: ~/.cursor, Codex/Claude auth, Keychain, svc-* screens
#
# Default is shadow/dry-run mode. To enable live dispatch:
#   1. Edit ~/Library/LaunchAgents/com.storypool.dispatch.plist
#   2. Uncomment the --live argument
#   3. Run: launchctl unload/load the plist
#
# Usage: bash install-dispatch-mini.sh
#        bash install-dispatch-mini.sh --uninstall

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.storypool.dispatch.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.storypool.dispatch.plist"
LOG_DIR="$HOME/.local/share/storypool/dispatch"
LABEL="com.storypool.dispatch"

uninstall() {
    echo "Uninstalling StoryPool Dispatcher..."
    
    if launchctl list "$LABEL" &>/dev/null; then
        echo "  Unloading agent..."
        launchctl unload "$PLIST_DST" 2>/dev/null || true
    fi
    
    if [[ -f "$PLIST_DST" ]]; then
        echo "  Removing plist..."
        rm -f "$PLIST_DST"
    fi
    
    echo "Done. Log directory preserved at $LOG_DIR"
    echo "Review and back up $LOG_DIR before deleting logs manually."
}

install() {
    echo "Installing StoryPool Dispatcher on Mac mini..."
    echo "  Repo: $REPO_ROOT"
    echo "  Logs: $LOG_DIR"
    
    # Create log directory
    echo "  Creating log directory..."
    mkdir -p "$LOG_DIR"
    
    # Check for gh CLI
    if ! command -v gh &>/dev/null; then
        echo "ERROR: gh CLI not found. Install with: brew install gh"
        exit 1
    fi
    
    # Check gh auth
    if ! gh auth status &>/dev/null; then
        echo "ERROR: gh not authenticated. Run: gh auth login"
        exit 1
    fi
    
    # Create LaunchAgents directory if needed
    mkdir -p "$HOME/Library/LaunchAgents"
    
    # Generate plist with correct paths
    echo "  Generating plist..."
    sed -e "s|__REPO_PATH__|$REPO_ROOT|g" \
        -e "s|__HOME__|$HOME|g" \
        "$PLIST_SRC" > "$PLIST_DST"
    
    # Clear stale .pyc files to avoid import issues
    echo "  Clearing __pycache__..."
    find "$REPO_ROOT/cluster" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "$REPO_ROOT/cluster" -name "*.pyc" -delete 2>/dev/null || true
    
    # Unload if already loaded
    if launchctl list "$LABEL" &>/dev/null; then
        echo "  Unloading existing agent..."
        launchctl unload "$PLIST_DST" 2>/dev/null || true
    fi
    
    # Load the agent
    echo "  Loading agent..."
    launchctl load "$PLIST_DST"
    
    echo ""
    echo "StoryPool Dispatcher installed!"
    echo ""
    echo "Default: SHADOW MODE (logs decisions, takes NO actions)"
    echo "The dispatcher will run every 15 minutes."
    echo ""
    echo "To enable LIVE mode:"
    echo "  1. Edit $PLIST_DST"
    echo "  2. Uncomment the <string>--live</string> line"
    echo "  3. Run: launchctl unload $PLIST_DST && launchctl load $PLIST_DST"
    echo ""
    echo "Useful commands:"
    echo "  # Check if running"
    echo "  launchctl list | grep storypool.dispatch"
    echo ""
    echo "  # Run manually (shadow mode)"
    echo "  cd $REPO_ROOT && PYTHONPATH=. python3 -m cluster.control_plane.dispatcher --machine mini"
    echo ""
    echo "  # Run manually (live mode)"
    echo "  cd $REPO_ROOT && PYTHONPATH=. python3 -m cluster.control_plane.dispatcher --machine mini --live"
    echo ""
    echo "  # View today's log"
    echo "  cat $LOG_DIR/dispatch-\$(date +%Y-%m-%d).jsonl"
    echo ""
    echo "  # View status"
    echo "  cat $LOG_DIR/dispatch-status.json"
    echo ""
    echo "  # Uninstall"
    echo "  bash $0 --uninstall"
}

# Main
if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
else
    install
fi
