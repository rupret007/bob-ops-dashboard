# Shared by every Mini installer. Validate, bootout, bootstrap, then verify.
storypool_reload_agent() {
    PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m cluster.control_plane.guardian --reload "$@"
}
