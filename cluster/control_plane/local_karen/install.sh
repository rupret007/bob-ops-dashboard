#!/bin/bash
# Install script for Local Karen gate
#
# NO SUDO REQUIRED - installs to user directories only
#
# Usage:
#   ./install.sh              # Install CLI and launchd agent
#   ./install.sh --cli-only   # Install CLI only (no launchd)
#   ./install.sh --uninstall  # Remove everything

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KAREN_DIR="$HOME/.local/share/karen"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/karen"
LAUNCHD_DIR="$HOME/Library/LaunchAgents"
PLIST_NAME="com.storypool.karen-poller.plist"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

check_dependencies() {
    log_info "Checking dependencies..."

    if ! command -v python3 &> /dev/null; then
        log_error "python3 not found"
        exit 1
    fi

    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    if [[ $(echo "$PYTHON_VERSION < 3.10" | bc -l 2>/dev/null || echo "1") == "1" ]]; then
        log_warn "Python 3.10+ recommended, found $PYTHON_VERSION"
    fi

    if ! command -v gh &> /dev/null; then
        log_error "GitHub CLI (gh) not found — install with: brew install gh"
        exit 1
    fi

    if ! gh auth status &> /dev/null; then
        log_error "GitHub CLI not authenticated — run: gh auth login"
        exit 1
    fi

    log_info "Dependencies OK"
}

setup_api_key_dirs() {
    log_info "Setting up API key directories..."

    STORYPOOL_DIR="$HOME/.config/storypool"

    if [[ ! -d "$STORYPOOL_DIR" ]]; then
        mkdir -p "$STORYPOOL_DIR"
        chmod 700 "$STORYPOOL_DIR"
        log_info "Created $STORYPOOL_DIR with mode 700"
    else
        chmod 700 "$STORYPOOL_DIR"
        log_info "Ensured $STORYPOOL_DIR has mode 700"
    fi

    echo ""
    log_info "API key files (create these to enable reviewers):"
    echo ""
    echo "  Gemini (fallback reviewer):"
    echo "    touch $STORYPOOL_DIR/gemini.key && chmod 600 $STORYPOOL_DIR/gemini.key"
    echo "    # Then open in editor and paste your key (don't echo it to shell)"
    echo "    nano $STORYPOOL_DIR/gemini.key"
    echo ""
    echo "  MiniMax (second judge):"
    echo "    touch $STORYPOOL_DIR/minimax.key && chmod 600 $STORYPOOL_DIR/minimax.key"
    echo "    nano $STORYPOOL_DIR/minimax.key"
    echo ""
    echo "  NEVER echo keys to the terminal or put them in shell history."
    echo ""
}

install_cli() {
    log_info "Installing karen-gate CLI..."

    mkdir -p "$BIN_DIR"
    mkdir -p "$KAREN_DIR"
    mkdir -p "$KAREN_DIR/verdicts"
    mkdir -p "$CONFIG_DIR"

    cat > "$BIN_DIR/karen-gate" << 'WRAPPER'
#!/bin/bash
# karen-gate CLI wrapper
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KAREN_MODULE="${KAREN_HOME:-$HOME/.local/share/karen}/module"

if [[ -d "$KAREN_MODULE" ]]; then
    PYTHONPATH="$KAREN_MODULE:$PYTHONPATH" exec python3 -m cluster.control_plane.local_karen.cli "$@"
else
    exec python3 -m cluster.control_plane.local_karen.cli "$@"
fi
WRAPPER
    chmod +x "$BIN_DIR/karen-gate"

    cat > "$BIN_DIR/karen-poller" << 'WRAPPER'
#!/bin/bash
# karen-poller CLI wrapper
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KAREN_MODULE="${KAREN_HOME:-$HOME/.local/share/karen}/module"

if [[ -d "$KAREN_MODULE" ]]; then
    PYTHONPATH="$KAREN_MODULE:$PYTHONPATH" exec python3 -m cluster.control_plane.local_karen.poller "$@"
else
    exec python3 -m cluster.control_plane.local_karen.poller "$@"
fi
WRAPPER
    chmod +x "$BIN_DIR/karen-poller"

    mkdir -p "$KAREN_DIR/module/cluster/control_plane"

    # Remove old module to avoid stale .pyc files
    rm -rf "$KAREN_DIR/module/cluster/control_plane/local_karen"
    rm -f "$KAREN_DIR/module/cluster/never_touch.py"

    # Copy fresh module (excluding __pycache__)
    cp -r "$SCRIPT_DIR" "$KAREN_DIR/module/cluster/control_plane/local_karen"
    find "$KAREN_DIR/module/cluster/control_plane/local_karen" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "$KAREN_DIR/module/cluster/control_plane/local_karen" -name "*.pyc" -delete 2>/dev/null || true

    # Copy never_touch.py from parent cluster directory if it exists
    CLUSTER_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
    if [[ -f "$CLUSTER_DIR/never_touch.py" ]]; then
        cp "$CLUSTER_DIR/never_touch.py" "$KAREN_DIR/module/cluster/never_touch.py"
        log_info "Copied never_touch.py to module"
    fi

    touch "$KAREN_DIR/module/cluster/__init__.py"
    touch "$KAREN_DIR/module/cluster/control_plane/__init__.py"
    local repo_root
    repo_root="$(cd "$SCRIPT_DIR/../../.." && pwd)"
    PYTHONPATH="$repo_root" python3 -c 'from pathlib import Path; import sys; from cluster.control_plane.self_update import copy_karen_module; sys.exit(0 if copy_karen_module(Path(sys.argv[1])) else 1)' "$repo_root"


    if [[ -f "$CONFIG_DIR/config.json" ]]; then
        log_info "Config already exists at $CONFIG_DIR/config.json"
    else
        cat > "$CONFIG_DIR/config.json" << 'CONFIG'
{
  "reviewer_order": ["codex", "gemini", "minimax"],
  "codex_host": "nuc",
  "codex_effort_default": "low",
  "codex_effort_high_risk": "high",
  "codex_weekly_cap_pct": 75.0,
  "gemini_api_key_file": "~/.config/storypool/gemini.key",
  "minimax_api_key_file": "~/.config/storypool/minimax.key",
  "claude_enabled": false,
  "claude_daily_cap": 3,
  "budget_per_run_usd": 0.50,
  "never_touch_repos": ["rupret007/rad-dad-show-night"],
  "verdict_dir": "~/.local/share/karen/verdicts",
  "state_dir": "~/.local/share/karen",
  "poll_interval_minutes": 10,
  "poll_label": "needs-karen"
}
CONFIG
        log_info "Created default config at $CONFIG_DIR/config.json"
    fi

    log_info "CLI installed to $BIN_DIR/karen-gate"

    if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
        log_warn "Add $BIN_DIR to your PATH:"
        log_warn "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc"
    fi
}

install_launchd() {
    if [[ "$(uname)" != "Darwin" ]]; then
        log_warn "launchd is macOS-only, skipping"
        return
    fi

    log_info "Installing launchd agent..."

    mkdir -p "$LAUNCHD_DIR"

    cat > "$LAUNCHD_DIR/$PLIST_NAME" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.storypool.karen-poller</string>

    <key>ProgramArguments</key>
    <array>
        <string>$BIN_DIR/karen-poller</string>
        <string>--once</string>
    </array>

    <key>StartInterval</key>
    <integer>600</integer>

    <key>RunAtLoad</key>
    <false/>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:$BIN_DIR</string>
        <key>KAREN_REPOS</key>
        <string>rupret007/webjam,rupret007/bob-ops-dashboard</string>
        <key>KAREN_CONFIG</key>
        <string>$CONFIG_DIR/config.json</string>
    </dict>

    <key>StandardOutPath</key>
    <string>$KAREN_DIR/poller.log</string>

    <key>StandardErrorPath</key>
    <string>$KAREN_DIR/poller.err</string>

    <key>WorkingDirectory</key>
    <string>$KAREN_DIR</string>
</dict>
</plist>
PLIST

    log_info "launchd plist installed to $LAUNCHD_DIR/$PLIST_NAME"
    log_info "To load the agent:"
    log_info "  launchctl load $LAUNCHD_DIR/$PLIST_NAME"
    log_info "To unload:"
    log_info "  launchctl unload $LAUNCHD_DIR/$PLIST_NAME"
    log_info "To run once manually:"
    log_info "  launchctl start com.storypool.karen-poller"
}

uninstall() {
    log_info "Uninstalling Local Karen..."

    if [[ "$(uname)" == "Darwin" ]] && [[ -f "$LAUNCHD_DIR/$PLIST_NAME" ]]; then
        launchctl unload "$LAUNCHD_DIR/$PLIST_NAME" 2>/dev/null || true
        rm -f "$LAUNCHD_DIR/$PLIST_NAME"
        log_info "Removed launchd agent"
    fi

    rm -f "$BIN_DIR/karen-gate"
    rm -f "$BIN_DIR/karen-poller"
    log_info "Removed CLI scripts"

    rm -rf "$KAREN_DIR/module"
    log_info "Removed module files"

    log_info "Kept config ($CONFIG_DIR) and verdicts ($KAREN_DIR/verdicts)"
    log_info "To remove all data: rm -rf $KAREN_DIR $CONFIG_DIR"
}

main() {
    case "${1:-}" in
        --uninstall)
            uninstall
            ;;
        --cli-only)
            check_dependencies
            setup_api_key_dirs
            install_cli
            log_info "Installation complete (CLI only)"
            ;;
        *)
            check_dependencies
            setup_api_key_dirs
            install_cli
            install_launchd
            log_info "Installation complete"
            log_info ""
            log_info "Next steps:"
            log_info "  1. Add ~/.local/bin to PATH (if not already)"
            log_info "  2. Test: karen-gate rupret007/webjam 1 --dry-run"
            log_info "  3. Load poller: launchctl load ~/Library/LaunchAgents/$PLIST_NAME"
            ;;
    esac
}

main "$@"
