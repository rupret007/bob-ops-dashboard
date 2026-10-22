#!/bin/bash
# Install StoryPool shadow mode on Mac mini
#
# This script:
# - Creates the log directory
# - Installs the launchd plist to ~/Library/LaunchAgents
# - Loads the agent
#
# NO SUDO REQUIRED
# Does NOT touch: ~/.cursor, Codex/Claude auth, Keychain, svc-* screens
#
# Usage: bash install-shadow-mini.sh
#        bash install-shadow-mini.sh --uninstall

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.storypool.shadow.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.storypool.shadow.plist"
LOG_DIR="$HOME/.local/share/storypool/shadow"
LABEL="com.storypool.shadow"

uninstall() {
    echo "Uninstalling StoryPool shadow mode..."
    
    if launchctl list "$LABEL" &>/dev/null; then
        echo "  Unloading agent..."
        launchctl unload "$PLIST_DST" 2>/dev/null || true
    fi
    
    if [[ -f "$PLIST_DST" ]]; then
        echo "  Removing plist..."
        rm -f "$PLIST_DST"
    fi
    
    echo "Done. Log directory preserved at $LOG_DIR"
}

install() {
    echo "Installing StoryPool shadow mode on Mac mini..."
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
        echo "ERROR: existing gh credentials are unavailable; ask the operator to configure read-only access."
        exit 1
    fi
    
    # Create LaunchAgents directory if needed
    mkdir -p "$HOME/Library/LaunchAgents"
    
    # Generate plist with correct paths
    echo "  Generating plist..."
    sed -e "s|__REPO_PATH__|$REPO_ROOT|g" \
        -e "s|__HOME__|$HOME|g" \
        "$PLIST_SRC" > "$PLIST_DST"
    
    # Unload if already loaded
    if launchctl list "$LABEL" &>/dev/null; then
        echo "  Unloading existing agent..."
        launchctl unload "$PLIST_DST" 2>/dev/null || true
    fi
    
    # Load the agent
    echo "  Loading agent..."
    launchctl load "$PLIST_DST"
    
    echo ""
    echo "StoryPool shadow mode installed!"
    echo ""
    echo "The shadow runner will execute every 15 minutes."
    echo "It logs what it WOULD do but takes NO ACTIONS."
    echo ""
    echo "Useful commands:"
    echo "  # Check if running"
    echo "  launchctl list | grep storypool"
    echo ""
    echo "  # Run manually"
    echo "  cd $REPO_ROOT && PYTHONPATH=. python3 -m cluster.control_plane.shadow_runner --machine mini"
    echo ""
    echo "  # View today's log"
    echo "  cat $LOG_DIR/shadow-\$(date +%Y-%m-%d).jsonl"
    echo ""
    echo "  # Generate comparison report"
    echo "  cd $REPO_ROOT && PYTHONPATH=. python3 -m cluster.control_plane.shadow_compare"
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
