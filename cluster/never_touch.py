"""Fail-closed guard for never-touch repositories.

Never-touch repos require explicit human (Jeff) approval for any write operation.
This module provides a central authoritative list and guards that MUST be called
before any merge, push, or PR-create operation.

Incident reference: docs/incidents/2026-10-17-show-night-leak.md
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

NEVER_TOUCH_REPOS: frozenset[str] = frozenset({
    "rupret007/rad-dad-show-night",
    "rupret007/hang-out",
    "rupret007/Hang-Out",
    "rupret007/HangOut",
})

CONDUCTOR_NEVER_MERGE_REPOS: frozenset[str] = frozenset({
    "rupret007/rad-dad-show-night",
    "rupret007/bob-ops-dashboard",
    "rupret007/hang-out",
    "rupret007/Hang-Out",
    "rupret007/HangOut",
})

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class NeverTouchViolation(PermissionError):
    """Operation blocked: target repository is on the never-touch list."""

    def __init__(self, repo: str, operation: str, detail: str = ""):
        msg = f"BLOCKED: {operation} on never-touch repo {repo!r}"
        if detail:
            msg = f"{msg} — {detail}"
        super().__init__(msg)
        self.repo = repo
        self.operation = operation


@dataclass
class GatePassReceipt:
    """Proof that a ship-gate PASS exists at an exact commit SHA."""
    repo: str
    sha: str
    reviewer: str
    expires_at: float


def normalize_repo(repo: str) -> str:
    """Normalize repository to lowercase owner/name format."""
    repo = (repo or "").strip().lower()
    if repo.startswith("https://github.com/"):
        repo = repo[len("https://github.com/"):]
    repo = repo.rstrip("/")
    if not _REPO_RE.fullmatch(repo):
        raise ValueError(f"invalid repository format: {repo!r}")
    return repo


def is_never_touch(repo: str) -> bool:
    """Check if a repository is on the never-touch list."""
    try:
        return normalize_repo(repo) in NEVER_TOUCH_REPOS
    except ValueError:
        return False


def check_merge_allowed(repo: str) -> tuple[bool, str]:
    """Check if auto-merge is allowed for a repository.
    
    Used by the conductor service to determine if a PR can be auto-merged.
    This checks the broader CONDUCTOR_NEVER_MERGE_REPOS list which includes
    infrastructure repos that shouldn't be auto-merged even if they're not
    in the strict never-touch list.
    
    Args:
        repo: Repository in "owner/name" format
        
    Returns:
        Tuple of (allowed, reason)
    """
    try:
        normalized = normalize_repo(repo)
    except ValueError:
        normalized = repo.lower()
    
    if normalized in CONDUCTOR_NEVER_MERGE_REPOS:
        return False, f"repo {repo} is in NEVER_TOUCH_REPOS - requires human merge"
    return True, "ok"


def assert_not_never_touch(repo: str, operation: str) -> None:
    """Raise NeverTouchViolation if repo is on the never-touch list.

    This is a fail-closed guard that MUST be called before:
    - git push to any branch on the repo
    - creating a PR on the repo
    - merging a PR on the repo
    - any other write operation to the repo
    """
    try:
        normalized = normalize_repo(repo)
    except ValueError as e:
        raise NeverTouchViolation(repo, operation, f"invalid repo: {e}") from e
    if normalized in NEVER_TOUCH_REPOS:
        raise NeverTouchViolation(
            normalized,
            operation,
            "human approval required; automation blocked",
        )


def check_gate_pass_at_sha(
    repo: str,
    sha: str,
    gate_pass: Optional[GatePassReceipt],
    operation: str,
) -> None:
    """Verify that a gate PASS exists at the exact SHA being operated on.

    For never-touch repos, this check is REQUIRED before merge.
    For other repos, this check is advisory but recommended.

    Args:
        repo: The repository (owner/name format)
        sha: The exact commit SHA being merged/pushed
        gate_pass: The gate pass receipt, or None if no pass exists
        operation: Description of the operation (for error messages)

    Raises:
        NeverTouchViolation: If the repo is never-touch and gate_pass is missing/invalid
    """
    normalized = normalize_repo(repo)
    if normalized not in NEVER_TOUCH_REPOS:
        return

    if gate_pass is None:
        raise NeverTouchViolation(
            normalized,
            operation,
            f"no gate PASS at SHA {sha[:12]}",
        )

    if normalize_repo(gate_pass.repo) != normalized:
        raise NeverTouchViolation(
            normalized,
            operation,
            f"gate PASS is for different repo {gate_pass.repo!r}",
        )

    if gate_pass.sha != sha:
        raise NeverTouchViolation(
            normalized,
            operation,
            f"gate PASS at {gate_pass.sha[:12]} but HEAD is {sha[:12]}",
        )

    import time
    if gate_pass.expires_at <= time.time():
        raise NeverTouchViolation(
            normalized,
            operation,
            f"gate PASS expired at {gate_pass.expires_at}",
        )


def guard_push(repo: str, branch: str, sha: str) -> None:
    """Fail-closed guard for git push operations.

    Call this BEFORE any git push to a remote branch.
    """
    assert_not_never_touch(repo, f"push to {branch}")


def guard_pr_create(repo: str, head_branch: str, base_branch: str) -> None:
    """Fail-closed guard for PR creation.

    Call this BEFORE creating a pull request.
    """
    assert_not_never_touch(repo, f"create PR from {head_branch} to {base_branch}")


def guard_pr_merge(
    repo: str,
    pr_number: int,
    head_sha: str,
    gate_pass: Optional[GatePassReceipt] = None,
) -> None:
    """Fail-closed guard for PR merge operations.

    Call this BEFORE merging a pull request.
    For never-touch repos, ALWAYS blocks - human must merge manually.
    For other repos with gate_required=True (future), requires valid gate_pass.
    """
    assert_not_never_touch(repo, f"merge PR #{pr_number}")
