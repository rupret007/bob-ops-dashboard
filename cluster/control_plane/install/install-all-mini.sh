#!/bin/bash
# StoryPool All-in-One Installer for Mac mini
#
# Installs ALL components in shadow/dry-run mode:
# - Shadow runner (every 15 min)
# - Lane dispatcher (every 15 min)
# - LLM conductor (hourly)
# - Karen gate CLI (karen-gate --dry-run)
# - Karen poller is NOT loaded (shadow mode only)
#
# NO SUDO REQUIRED - installs to user directories only
# Does NOT touch: ~/bob-ops-dashboard (may have local edits)
# Does NOT load: Karen poller (shadow mode default)
# Does NOT enable: --live mode on any component
#
# Jeff's Mini setup:
#   cd ~/bob-ops-dashboard
#   git worktree add ~/storypool-install origin/storypool/all-in-one
#   ~/storypool-install/cluster/control_plane/install/install-all-mini.sh
#
# Usage:
#   ./install-all-mini.sh              # Install all components
#   ./install-all-mini.sh --uninstall  # Remove all components

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

RESULTS=()

check_dependencies() {
    log_header "Checking Dependencies"

    if ! command -v python3 &> /dev/null; then
        log_error "python3 not found"
        exit 1
    fi
    log_info "python3: $(python3 --version)"

    if ! command -v gh &> /dev/null; then
        log_error "gh CLI not found — install with: brew install gh"
        exit 1
    fi
    log_info "gh CLI: $(gh --version | head -1)"

    if ! gh auth status &> /dev/null; then
        log_error "gh not authenticated — run: gh auth login"
        exit 1
    fi
    log_info "gh authenticated"
}

setup_config_dir() {
    log_header "Setting up ~/.config/storypool"

    STORYPOOL_DIR="$HOME/.config/storypool"

    if [[ ! -d "$STORYPOOL_DIR" ]]; then
        mkdir -p "$STORYPOOL_DIR"
        chmod 700 "$STORYPOOL_DIR"
        log_info "Created $STORYPOOL_DIR with mode 700"
    else
        chmod 700 "$STORYPOOL_DIR"
        log_info "$STORYPOOL_DIR exists with mode 700"
    fi
}

clear_pycache() {
    local dir="$1"
    if [[ -d "$dir" ]]; then
        find "$dir" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
        find "$dir" -name "*.pyc" -delete 2>/dev/null || true
    fi
}

install_shadow() {
    log_header "Installing Shadow Runner"

    local log_dir="$HOME/.local/share/storypool/shadow"
    mkdir -p "$log_dir"

    local plist_src="$SCRIPT_DIR/com.storypool.shadow.plist"
    local plist_dst="$HOME/Library/LaunchAgents/com.storypool.shadow.plist"

    if [[ ! -f "$plist_src" ]]; then
        log_warn "Shadow plist not found at $plist_src — skipping"
        return
    fi

    mkdir -p "$HOME/Library/LaunchAgents"

    sed -e "s|__REPO_PATH__|$REPO_ROOT|g" \
        -e "s|__HOME__|$HOME|g" \
        "$plist_src" > "$plist_dst"

    clear_pycache "$REPO_ROOT/cluster"

    if launchctl list com.storypool.shadow &>/dev/null; then
        launchctl unload "$plist_dst" 2>/dev/null || true
    fi
    launchctl load "$plist_dst"

    log_info "Shadow runner installed (15 min interval)"
}

install_dispatcher() {
    log_header "Installing Lane Dispatcher"

    local log_dir="$HOME/.local/share/storypool/dispatch"
    mkdir -p "$log_dir"

    local plist_src="$SCRIPT_DIR/com.storypool.dispatch.plist"
    local plist_dst="$HOME/Library/LaunchAgents/com.storypool.dispatch.plist"

    if [[ ! -f "$plist_src" ]]; then
        log_warn "Dispatch plist not found at $plist_src — skipping"
        return
    fi

    mkdir -p "$HOME/Library/LaunchAgents"

    sed -e "s|__REPO_PATH__|$REPO_ROOT|g" \
        -e "s|__HOME__|$HOME|g" \
        "$plist_src" > "$plist_dst"

    clear_pycache "$REPO_ROOT/cluster"

    if launchctl list com.storypool.dispatch &>/dev/null; then
        launchctl unload "$plist_dst" 2>/dev/null || true
    fi
    launchctl load "$plist_dst"

    log_info "Dispatcher installed (15 min interval, shadow mode)"
}

install_conductor() {
    log_header "Installing LLM Conductor"

    local log_dir="$HOME/.local/share/storypool/conductor"
    mkdir -p "$log_dir"
    mkdir -p "$HOME/.local/share/storypool/verdicts"

    local plist_src="$SCRIPT_DIR/com.storypool.conductor.plist"
    local plist_dst="$HOME/Library/LaunchAgents/com.storypool.conductor.plist"

    if [[ ! -f "$plist_src" ]]; then
        log_warn "Conductor plist not found at $plist_src — skipping"
        return
    fi

    mkdir -p "$HOME/Library/LaunchAgents"

    sed -e "s|__REPO_PATH__|$REPO_ROOT|g" \
        -e "s|__HOME__|$HOME|g" \
        "$plist_src" > "$plist_dst"

    clear_pycache "$REPO_ROOT/cluster"

    if launchctl list com.storypool.conductor &>/dev/null; then
        launchctl unload "$plist_dst" 2>/dev/null || true
    fi
    launchctl load "$plist_dst"

    log_info "Conductor installed (hourly, shadow mode)"
}

install_karen_cli() {
    log_header "Installing Karen Gate CLI"

    local karen_dir="$HOME/.local/share/karen"
    local bin_dir="$HOME/.local/bin"
    local config_dir="$HOME/.config/karen"

    mkdir -p "$bin_dir"
    mkdir -p "$karen_dir"
    mkdir -p "$karen_dir/verdicts"
    mkdir -p "$config_dir"

    cat > "$bin_dir/karen-gate" << 'WRAPPER'
#!/bin/bash
KAREN_MODULE="${KAREN_HOME:-$HOME/.local/share/karen}/module"
if [[ -d "$KAREN_MODULE" ]]; then
    PYTHONPATH="$KAREN_MODULE:$PYTHONPATH" exec python3 -m cluster.control_plane.local_karen.cli "$@"
else
    exec python3 -m cluster.control_plane.local_karen.cli "$@"
fi
WRAPPER
    chmod +x "$bin_dir/karen-gate"

    mkdir -p "$karen_dir/module/cluster/control_plane"

    rm -rf "$karen_dir/module/cluster/control_plane/local_karen"
    rm -f "$karen_dir/module/cluster/never_touch.py"

    local karen_src="$REPO_ROOT/cluster/control_plane/local_karen"
    cp -r "$karen_src" "$karen_dir/module/cluster/control_plane/local_karen"

    clear_pycache "$karen_dir/module"

    if [[ -f "$REPO_ROOT/cluster/never_touch.py" ]]; then
        cp "$REPO_ROOT/cluster/never_touch.py" "$karen_dir/module/cluster/never_touch.py"
    fi

    touch "$karen_dir/module/cluster/__init__.py"
    touch "$karen_dir/module/cluster/control_plane/__init__.py"

    if [[ ! -f "$config_dir/config.json" ]]; then
        cat > "$config_dir/config.json" << 'CONFIG'
{
  "reviewer_order": ["codex", "gemini", "minimax"],
  "codex_host": "nuc",
  "codex_effort_default": "low",
  "codex_effort_high_risk": "high",
  "codex_weekly_cap_pct": 75.0,
  "claude_enabled": false,
  "never_touch_repos": ["rupret007/rad-dad-show-night"],
  "verdict_dir": "~/.local/share/karen/verdicts",
  "state_dir": "~/.local/share/karen"
}
CONFIG
        log_info "Created default config at $config_dir/config.json"
    fi

    log_info "karen-gate CLI installed to $bin_dir/karen-gate"
    log_info "Karen poller NOT loaded (shadow mode — run karen-gate --dry-run manually)"

    if [[ ":$PATH:" != *":$bin_dir:"* ]]; then
        log_warn "Add $bin_dir to your PATH:"
        echo "    echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc"
    fi
}

run_self_check() {
    log_header "Self-Check"

    local all_pass=true

    if launchctl list com.storypool.shadow &>/dev/null; then
        log_pass "shadow runner: launchd loaded"
        RESULTS+=("shadow: PASS")
    else
        log_fail "shadow runner: not loaded"
        RESULTS+=("shadow: FAIL")
        all_pass=false
    fi

    if launchctl list com.storypool.dispatch &>/dev/null; then
        log_pass "dispatcher: launchd loaded"
        RESULTS+=("dispatcher: PASS")
    else
        log_fail "dispatcher: not loaded"
        RESULTS+=("dispatcher: FAIL")
        all_pass=false
    fi

    if launchctl list com.storypool.conductor &>/dev/null; then
        log_pass "conductor: launchd loaded"
        RESULTS+=("conductor: PASS")
    else
        log_fail "conductor: not loaded"
        RESULTS+=("conductor: FAIL")
        all_pass=false
    fi

    if command -v karen-gate &>/dev/null || [[ -x "$HOME/.local/bin/karen-gate" ]]; then
        log_info "Testing karen-gate --dry-run..."
        if PATH="$HOME/.local/bin:$PATH" karen-gate --help &>/dev/null; then
            log_pass "karen-gate CLI: available"
            RESULTS+=("karen-gate: PASS")
        else
            log_fail "karen-gate CLI: --help failed"
            RESULTS+=("karen-gate: FAIL")
            all_pass=false
        fi
    else
        log_fail "karen-gate CLI: not found"
        RESULTS+=("karen-gate: FAIL")
        all_pass=false
    fi

    log_info "Testing shadow tick..."
    local shadow_tmp
    shadow_tmp=$(mktemp)
    local shadow_exit
    (cd "$REPO_ROOT" && PYTHONPATH="$REPO_ROOT" python3 -m cluster.control_plane.shadow_runner --machine mini --json > "$shadow_tmp" 2>&1)
    shadow_exit=$?
    if [[ $shadow_exit -eq 0 ]] && python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$shadow_tmp" 2>/dev/null; then
        log_pass "shadow tick: ran successfully"
        RESULTS+=("shadow-tick: PASS")
    else
        log_fail "shadow tick: failed (exit code $shadow_exit)"
        echo "    Output: $(head -c 500 "$shadow_tmp")"
        RESULTS+=("shadow-tick: FAIL")
        all_pass=false
    fi
    rm -f "$shadow_tmp"

    echo ""
    log_header "Results Summary"
    for r in "${RESULTS[@]}"; do
        echo "  $r"
    done

    if $all_pass; then
        echo ""
        log_info "All components installed and working in SHADOW MODE"
        log_info "To enable LIVE mode, edit each plist and uncomment --live"
    else
        echo ""
        log_warn "Some components failed — check errors above"
    fi
}

uninstall_all() {
    log_header "Uninstalling All Components"

    for label in com.storypool.shadow com.storypool.dispatch com.storypool.conductor com.storypool.karen-poller; do
        local plist="$HOME/Library/LaunchAgents/$label.plist"
        if launchctl list "$label" &>/dev/null; then
            log_info "Unloading $label..."
            launchctl unload "$plist" 2>/dev/null || true
        fi
        if [[ -f "$plist" ]]; then
            rm -f "$plist"
            log_info "Removed $plist"
        fi
    done

    log_info "Preserved: ~/.local/share/storypool (logs)"
    log_info "Preserved: ~/.local/share/karen (verdicts)"
    log_info "Preserved: ~/.config/storypool (config)"

    echo ""
    log_info "To fully remove, manually delete:"
    echo "    rm -rf ~/.local/share/storypool"
    echo "    rm -rf ~/.local/share/karen"
    echo "    rm -rf ~/.config/storypool"
    echo "    rm -rf ~/.config/karen"
    echo "    rm -f ~/.local/bin/karen-gate"
}

main() {
    if [[ "$(uname)" != "Darwin" ]]; then
        log_error "This script is for macOS (Mac mini) only"
        log_error "For NucBox (Linux/WSL), use install-all-nucbox.sh"
        exit 1
    fi

    if [[ "${1:-}" == "--uninstall" ]]; then
        uninstall_all
        exit 0
    fi

    log_header "StoryPool All-in-One Installer (Mac mini)"
    echo "Repo: $REPO_ROOT"
    echo ""

    check_dependencies
    setup_config_dir
    install_shadow
    install_dispatcher
    install_conductor
    install_karen_cli
    run_self_check

    echo ""
    log_info "Installation complete!"
    echo ""
    echo "All components are in SHADOW/DRY-RUN mode."
    echo "Logs: ~/.local/share/storypool/"
    echo ""
    echo "To test karen-gate manually:"
    echo "    karen-gate --dry-run --repo rupret007/webjam --pr 123"
}

main "$@"
