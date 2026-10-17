"""Never-touch repository list for automated merge protection.

Repositories listed here must NEVER be auto-merged by the conductor.
Human review and explicit approval is required for all merges to these repos.

This list exists because:
1. Some repos have special branch protection requirements
2. Some repos serve infrastructure that must be carefully coordinated
3. Some repos contain sensitive configuration

To add a repo: add its full "owner/name" to NEVER_TOUCH_REPOS below.
To allow auto-merge for a repo: remove it from the list (requires code review).
"""

from __future__ import annotations

NEVER_TOUCH_REPOS: frozenset[str] = frozenset({
    # Infrastructure and automation repos - require human coordination
    "rupret007/bob-ops-dashboard",
    
    # Private/sensitive repos that should never be auto-merged
    # (add repos here as needed)
})


def is_never_touch(repo: str) -> bool:
    """Check if a repository should never be auto-merged.
    
    Args:
        repo: Repository in "owner/name" format
        
    Returns:
        True if the repo must not be auto-merged
    """
    return repo in NEVER_TOUCH_REPOS


def check_merge_allowed(repo: str) -> tuple[bool, str]:
    """Check if auto-merge is allowed for a repository.
    
    Args:
        repo: Repository in "owner/name" format
        
    Returns:
        Tuple of (allowed, reason)
    """
    if is_never_touch(repo):
        return False, f"repo {repo} is in NEVER_TOUCH_REPOS - requires human merge"
    return True, "ok"
