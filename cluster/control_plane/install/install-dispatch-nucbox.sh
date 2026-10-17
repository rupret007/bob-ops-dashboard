#!/bin/bash
# Install StoryPool Dispatcher on NucBox (WSL Ubuntu)
#
# This script:
# - Creates the log directory
# - Installs systemd user service and timer
# - Enables and starts the timer
#
# NO SUDO REQUIRED (uses systemd --user)
# Does NOT touch: ~/.cursor, Codex/Claude auth, Keychain, svc-* screens
#
# Default is shadow/dry-run mode. To enable live dispatch:
#   1. Edit ~/.config/systemd/user/storypool-dispatch.service
#   2. Add --live to ExecStart
#   3. Run: systemctl --user daemon-reload && systemctl --user restart storypool-dispatch.timer
#
# Usage: bash install-dispatch-nucbox.sh
#        bash install-dispatch-nucbox.sh --uninstall

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SERVICE_SRC="$SCRIPT_DIR/storypool-dispatch.service"
TIMER_SRC="$SCRIPT_DIR/storypool-dispatch.timer"
SYSTEMD_DIR="$HOME/.config/systemd/user"
LOG_DIR="$HOME/.local/share/storypool/dispatch"

uninstall() {
    echo "Uninstalling StoryPool Dispatcher..."
    
    echo "  Stopping timer..."
    systemctl --user stop storypool-dispatch.timer 2>/dev/null || true
    
    echo "  Disabling timer..."
    systemctl --user disable storypool-dispatch.timer 2>/dev/null || true
    
    echo "  Removing service files..."
    rm -f "$SYSTEMD_DIR/storypool-dispatch.service"
    rm -f "$SYSTEMD_DIR/storypool-dispatch.timer"
    
    echo "  Reloading systemd..."
    systemctl --user daemon-reload
    
    echo "Done. Log directory preserved at $LOG_DIR"
    echo "To remove logs: rm -rf $LOG_DIR"
}

install() {
    echo "Installing StoryPool Dispatcher on NucBox..."
    echo "  Repo: $REPO_ROOT"
    echo "  Logs: $LOG_DIR"
    
    # Create log directory
    echo "  Creating log directory..."
    mkdir -p "$LOG_DIR"
    
    # Check for gh CLI
    if ! command -v gh &>/dev/null; then
        echo "ERROR: gh CLI not found. Install with: sudo apt install gh"
        exit 1
    fi
    
    # Check gh auth
    if ! gh auth status &>/dev/null; then
        echo "ERROR: gh not authenticated. Run: gh auth login"
        exit 1
    fi
    
    # Create systemd user directory
    echo "  Creating systemd user directory..."
    mkdir -p "$SYSTEMD_DIR"
    
    # Generate service file with correct paths
    echo "  Installing service file..."
    sed -e "s|__REPO_PATH__|$REPO_ROOT|g" \
        "$SERVICE_SRC" > "$SYSTEMD_DIR/storypool-dispatch.service"
    
    # Copy timer file
    echo "  Installing timer file..."
    cp "$TIMER_SRC" "$SYSTEMD_DIR/storypool-dispatch.timer"
    
    # Clear stale .pyc files to avoid import issues
    echo "  Clearing __pycache__..."
    find "$REPO_ROOT/cluster" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "$REPO_ROOT/cluster" -name "*.pyc" -delete 2>/dev/null || true
    
    # Reload systemd
    echo "  Reloading systemd..."
    systemctl --user daemon-reload
    
    # Enable and start the timer
    echo "  Enabling timer..."
    systemctl --user enable storypool-dispatch.timer
    
    echo "  Starting timer..."
    systemctl --user start storypool-dispatch.timer
    
    echo ""
    echo "StoryPool Dispatcher installed!"
    echo ""
    echo "Default: SHADOW MODE (logs decisions, takes NO actions)"
    echo "The dispatcher will run every 15 minutes."
    echo ""
    echo "To enable LIVE mode:"
    echo "  1. Edit $SYSTEMD_DIR/storypool-dispatch.service"
    echo "  2. Add --live to ExecStart line"
    echo "  3. Run: systemctl --user daemon-reload && systemctl --user restart storypool-dispatch.timer"
    echo ""
    echo "Useful commands:"
    echo "  # Check timer status"
    echo "  systemctl --user status storypool-dispatch.timer"
    echo ""
    echo "  # List timers"
    echo "  systemctl --user list-timers storypool-dispatch.timer"
    echo ""
    echo "  # Run manually (shadow mode)"
    echo "  cd $REPO_ROOT && PYTHONPATH=. python3 -m cluster.control_plane.dispatcher --machine nucbox"
    echo ""
    echo "  # Run manually (live mode)"
    echo "  cd $REPO_ROOT && PYTHONPATH=. python3 -m cluster.control_plane.dispatcher --machine nucbox --live"
    echo ""
    echo "  # View today's log"
    echo "  cat $LOG_DIR/dispatch-\$(date +%Y-%m-%d).jsonl"
    echo ""
    echo "  # View service logs"
    echo "  journalctl --user -u storypool-dispatch.service"
    echo ""
    echo "  # Uninstall"
    echo "  bash $0 --uninstall"
    echo ""
    echo "NOTE: For systemd user timers to work after logout, run once:"
    echo "  loginctl enable-linger \$USER"
}

# Main
if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
else
    install
fi
