#!/bin/bash
# Install StoryPool Conductor on Mac mini
#
# This script:
# - Creates the log and verdict directories
# - Installs the launchd plist to ~/Library/LaunchAgents
# - Loads the agent
#
# NO SUDO REQUIRED
# Does NOT touch: ~/.cursor, Codex/Claude auth, Keychain, svc-* screens
#
# Default is shadow/dry-run mode. To enable live actions:
#   1. Edit ~/Library/LaunchAgents/com.storypool.conductor.plist
#   2. Uncomment the --live argument
#   3. Run: launchctl unload/load the plist
#
# Usage: bash install-conductor-mini.sh
#        bash install-conductor-mini.sh --uninstall

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.storypool.conductor.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.storypool.conductor.plist"
LOG_DIR="$HOME/.local/share/storypool/conductor"
VERDICT_DIR="$HOME/.local/share/storypool/verdicts"
LABEL="com.storypool.conductor"

uninstall() {
    echo "Uninstalling StoryPool Conductor..."
    
    if launchctl list "$LABEL" &>/dev/null; then
        echo "  Unloading agent..."
        launchctl unload "$PLIST_DST" 2>/dev/null || true
    fi
    
    if [[ -f "$PLIST_DST" ]]; then
        echo "  Removing plist..."
        rm -f "$PLIST_DST"
    fi
    
    echo "Done. Log directory preserved at $LOG_DIR"
    echo "Verdict directory preserved at $VERDICT_DIR"
    echo "To remove: rm -rf $LOG_DIR $VERDICT_DIR"
}

install() {
    echo "Installing StoryPool Conductor on Mac mini..."
    echo "  Repo: $REPO_ROOT"
    echo "  Logs: $LOG_DIR"
    echo "  Verdicts: $VERDICT_DIR"
    
    # Create directories
    echo "  Creating directories..."
    mkdir -p "$LOG_DIR"
    mkdir -p "$VERDICT_DIR"
    
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
    echo "StoryPool Conductor installed!"
    echo ""
    echo "Default: SHADOW MODE (logs decisions, takes NO actions)"
    echo "The conductor will run every hour (at :05 past the hour)."
    echo ""
    echo "What it does:"
    echo "  - Labels PRs ready for Karen gate with 'needs-karen'"
    echo "  - Merges PRs with KARENPASS verdict (sha-pinned, never force-push)"
    echo "  - Notes stuck PRs for attention (no messages sent)"
    echo ""
    echo "To enable LIVE mode:"
    echo "  1. Edit $PLIST_DST"
    echo "  2. Uncomment the <string>--live</string> line"
    echo "  3. Run: launchctl unload $PLIST_DST && launchctl load $PLIST_DST"
    echo ""
    echo "Useful commands:"
    echo "  # Check if running"
    echo "  launchctl list | grep storypool.conductor"
    echo ""
    echo "  # Run manually (shadow mode)"
    echo "  cd $REPO_ROOT && PYTHONPATH=. python3 -m cluster.control_plane.conductor"
    echo ""
    echo "  # Run manually (live mode)"
    echo "  cd $REPO_ROOT && PYTHONPATH=. python3 -m cluster.control_plane.conductor --live"
    echo ""
    echo "  # Run specific step only"
    echo "  cd $REPO_ROOT && PYTHONPATH=. python3 -m cluster.control_plane.conductor --label-only"
    echo "  cd $REPO_ROOT && PYTHONPATH=. python3 -m cluster.control_plane.conductor --merge-only"
    echo "  cd $REPO_ROOT && PYTHONPATH=. python3 -m cluster.control_plane.conductor --nudge-only"
    echo ""
    echo "  # View today's log"
    echo "  cat $LOG_DIR/conductor-\$(date +%Y-%m-%d).jsonl"
    echo ""
    echo "  # View status"
    echo "  cat $LOG_DIR/conductor-status.json"
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
