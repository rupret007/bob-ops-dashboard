"""Guarded writes used by the dashboard refresh process."""

import subprocess

from . import assert_not_never_touch


def close_decision_issue(number: int, decision: str) -> None:
    """Close a recorded decision only after repository policy permits it."""
    assert_not_never_touch("rupret007/bob-ops-dashboard", "close decision issue")
    try:
        subprocess.check_call(
            [
                "gh", "issue", "close", str(number),
                "-R", "rupret007/bob-ops-dashboard",
                "--comment", f"Recorded as {decision} for Bob.",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        # Retain the recorded decision if GitHub is temporarily unavailable.
        pass
