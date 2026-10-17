#!/bin/bash
# Install StoryPool Conductor on NucBox (WSL Ubuntu)
#
# This script:
# - Creates the log and verdict directories
# - Installs systemd user service and timer
# - Enables and starts the timer
#
# NO SUDO REQUIRED (uses systemd --user)
# Does NOT touch: ~/.cursor, Codex/Claude auth, Keychain, svc-* screens
#
# Default is shadow/dry-run mode. To enable live actions:
#   1. Edit ~/.config/systemd/user/storypool-conductor.service
#   2. Add --live to ExecStart
#   3. Run: systemctl --user daemon-reload && systemctl --user restart storypool-conductor.timer
#
# Usage: bash install-conductor-nucbox.sh
#        bash install-conductor-nucbox.sh --uninstall

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SERVICE_SRC="$SCRIPT_DIR/storypool-conductor.service"
TIMER_SRC="$SCRIPT_DIR/storypool-conductor.timer"
SYSTEMD_DIR="$HOME/.config/systemd/user"
LOG_DIR="$HOME/.local/share/storypool/conductor"
VERDICT_DIR="$HOME/.local/share/storypool/verdicts"

uninstall() {
    echo "Uninstalling StoryPool Conductor..."
    
    echo "  Stopping timer..."
    systemctl --user stop storypool-conductor.timer 2>/dev/null || true
    
    echo "  Disabling timer..."
    systemctl --user disable storypool-conductor.timer 2>/dev/null || true
    
    echo "  Removing service files..."
    rm -f "$SYSTEMD_DIR/storypool-conductor.service"
    rm -f "$SYSTEMD_DIR/storypool-conductor.timer"
    
    echo "  Reloading systemd..."
    systemctl --user daemon-reload
    
    echo "Done. Log directory preserved at $LOG_DIR"
    echo "Verdict directory preserved at $VERDICT_DIR"
    echo "To remove: rm -rf $LOG_DIR $VERDICT_DIR"
}

install() {
    echo "Installing StoryPool Conductor on NucBox..."
    echo "  Repo: $REPO_ROOT"
    echo "  Logs: $LOG_DIR"
    echo "  Verdicts: $VERDICT_DIR"
    
    # Create directories
    echo "  Creating directories..."
    mkdir -p "$LOG_DIR"
    mkdir -p "$VERDICT_DIR"
    
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
        "$SERVICE_SRC" > "$SYSTEMD_DIR/storypool-conductor.service"
    
    # Copy timer file
    echo "  Installing timer file..."
    cp "$TIMER_SRC" "$SYSTEMD_DIR/storypool-conductor.timer"
    
    # Reload systemd
    echo "  Reloading systemd..."
    systemctl --user daemon-reload
    
    # Enable and start the timer
    echo "  Enabling timer..."
    systemctl --user enable storypool-conductor.timer
    
    echo "  Starting timer..."
    systemctl --user start storypool-conductor.timer
    
    echo ""
    echo "StoryPool Conductor installed!"
    echo ""
    echo "Default: SHADOW MODE (logs decisions, takes NO actions)"
    echo "The conductor will run every hour."
    echo ""
    echo "What it does:"
    echo "  - Labels PRs ready for Karen gate with 'needs-karen'"
    echo "  - Merges PRs with KARENPASS verdict (sha-pinned, never force-push)"
    echo "  - Notes stuck PRs for attention (no messages sent)"
    echo ""
    echo "To enable LIVE mode:"
    echo "  1. Edit $SYSTEMD_DIR/storypool-conductor.service"
    echo "  2. Add --live to ExecStart line"
    echo "  3. Run: systemctl --user daemon-reload && systemctl --user restart storypool-conductor.timer"
    echo ""
    echo "Useful commands:"
    echo "  # Check timer status"
    echo "  systemctl --user status storypool-conductor.timer"
    echo ""
    echo "  # List timers"
    echo "  systemctl --user list-timers storypool-conductor.timer"
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
    echo ""
    echo "  # View today's log"
    echo "  cat $LOG_DIR/conductor-\$(date +%Y-%m-%d).jsonl"
    echo ""
    echo "  # View service logs"
    echo "  journalctl --user -u storypool-conductor.service"
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
