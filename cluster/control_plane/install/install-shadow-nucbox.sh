#!/bin/bash
# Install StoryPool shadow mode on NucBox (WSL Ubuntu)
#
# This script:
# - Creates the log directory
# - Installs systemd user service and timer
# - Enables and starts the timer
#
# NO SUDO REQUIRED (uses systemd --user)
# Does NOT touch: ~/.cursor, Codex/Claude auth, Keychain, svc-* screens
#
# Usage: bash install-shadow-nucbox.sh
#        bash install-shadow-nucbox.sh --uninstall

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
SERVICE_SRC="$SCRIPT_DIR/storypool-shadow.service"
TIMER_SRC="$SCRIPT_DIR/storypool-shadow.timer"
SYSTEMD_DIR="$HOME/.config/systemd/user"
LOG_DIR="$HOME/.local/share/storypool/shadow"

uninstall() {
    echo "Uninstalling StoryPool shadow mode..."
    
    echo "  Stopping timer..."
    systemctl --user stop storypool-shadow.timer 2>/dev/null || true
    
    echo "  Disabling timer..."
    systemctl --user disable storypool-shadow.timer 2>/dev/null || true
    
    echo "  Removing service files..."
    rm -f "$SYSTEMD_DIR/storypool-shadow.service"
    rm -f "$SYSTEMD_DIR/storypool-shadow.timer"
    
    echo "  Reloading systemd..."
    systemctl --user daemon-reload
    
    echo "Done. Log directory preserved at $LOG_DIR"
    echo "To remove logs: rm -rf $LOG_DIR"
}

install() {
    echo "Installing StoryPool shadow mode on NucBox..."
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
        "$SERVICE_SRC" > "$SYSTEMD_DIR/storypool-shadow.service"
    
    # Copy timer file
    echo "  Installing timer file..."
    cp "$TIMER_SRC" "$SYSTEMD_DIR/storypool-shadow.timer"
    
    # Reload systemd
    echo "  Reloading systemd..."
    systemctl --user daemon-reload
    
    # Enable and start the timer
    echo "  Enabling timer..."
    systemctl --user enable storypool-shadow.timer
    
    echo "  Starting timer..."
    systemctl --user start storypool-shadow.timer
    
    echo ""
    echo "StoryPool shadow mode installed!"
    echo ""
    echo "The shadow runner will execute every 15 minutes."
    echo "It logs what it WOULD do but takes NO ACTIONS."
    echo ""
    echo "Useful commands:"
    echo "  # Check timer status"
    echo "  systemctl --user status storypool-shadow.timer"
    echo ""
    echo "  # List timers"
    echo "  systemctl --user list-timers storypool-shadow.timer"
    echo ""
    echo "  # Run manually"
    echo "  cd $REPO_ROOT && PYTHONPATH=. python3 -m cluster.control_plane.shadow_runner --machine nucbox"
    echo ""
    echo "  # View today's log"
    echo "  cat $LOG_DIR/shadow-\$(date +%Y-%m-%d).jsonl"
    echo ""
    echo "  # Generate comparison report"
    echo "  cd $REPO_ROOT && PYTHONPATH=. python3 -m cluster.control_plane.shadow_compare"
    echo ""
    echo "  # View service logs"
    echo "  journalctl --user -u storypool-shadow.service"
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
