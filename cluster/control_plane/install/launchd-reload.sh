# Shared by every Mini installer. Validate, bootout, bootstrap, then verify.
storypool_reload_agent() {
    if PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m cluster.control_plane.guardian --reload "$@"; then
        return 0
    fi
    if [[ "${STORYPOOL_COLLECT_RELOAD_FAILURES:-0}" == 1 ]]; then
        # All-in-one updates must still refresh Karen and the remaining agents.
        # The caller reports every failed reload and exits nonzero at the end.
        STORYPOOL_RELOAD_FAILURES="${STORYPOOL_RELOAD_FAILURES:-}${1}"$'\n'
        printf 'Agent reload failed: %s\n' "$1" >&2
        return 0
    fi
    return 1
}
