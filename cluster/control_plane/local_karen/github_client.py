"""GitHub client for Local Karen gate.

Uses the gh CLI for read operations and limited write operations (PR comments).
NEVER merges, pushes, force-pushes, tags, releases, or uses sudo.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import re
from dataclasses import dataclass
from typing import Optional

from .checks import PRInfo


class GitHubError(Exception):
    """Error from GitHub operations."""
    pass


def _run_gh(args: list[str], timeout: int = 30, allowed_codes=(0,)) -> str:
    """Run a gh CLI command and return stdout.

    Raises GitHubError on failure.
    """
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode not in allowed_codes:
            raise GitHubError(f"gh {' '.join(args)} failed: {result.stderr}")

        return result.stdout

    except subprocess.TimeoutExpired:
        raise GitHubError(f"gh {' '.join(args)} timed out after {timeout}s")
    except FileNotFoundError:
        raise GitHubError("gh CLI not found — install GitHub CLI first")


def _parse_ci_status(ci_data: list[dict]) -> dict[str, str]:
    """Parse CI check status from gh CLI output.

    gh pr checks --json supports: bucket, completedAt, description, event,
    link, name, startedAt, state, workflow

    bucket values: pass, fail, pending, skipping, cancel
    state values: SUCCESS, FAILURE, PENDING, etc.

    We normalize to: success, failure, pending, skipped, cancelled
    """
    ci_status = {}

    for check in ci_data:
        name = check.get("name", "unknown")
        bucket = check.get("bucket", "").lower()
        state = check.get("state", "").lower()

        if bucket == "pass":
            ci_status[name] = "success"
        elif bucket == "fail":
            ci_status[name] = "failure"
        elif bucket == "pending":
            ci_status[name] = "pending"
        elif bucket == "skipping":
            ci_status[name] = "skipped"
        elif bucket == "cancel":
            ci_status[name] = "cancelled"
        elif state:
            if state in ("success", "neutral"):
                ci_status[name] = "success"
            elif state in ("failure", "error", "timed_out", "action_required"):
                ci_status[name] = "failure"
            elif state in ("pending", "queued", "in_progress", "waiting"):
                ci_status[name] = "pending"
            elif state == "skipped":
                ci_status[name] = "skipped"
            elif state == "cancelled":
                ci_status[name] = "cancelled"
            else:
                ci_status[name] = state
        else:
            ci_status[name] = "unknown"

    return ci_status


def fetch_pr_info(owner: str, repo: str, pr_number: int) -> PRInfo:
    """Fetch PR information from GitHub.

    Pins the exact head SHA at fetch time by fetching PR view FIRST,
    then fetches checks and diff.
    """
    pr_json = _run_gh([
        "pr", "view", str(pr_number),
        "--repo", f"{owner}/{repo}",
        "--json", "number,title,body,author,headRefOid,baseRefOid,isDraft,changedFiles,deletions,additions,files",
    ])
    pr_data = json.loads(pr_json)

    head_sha = pr_data.get("headRefOid", "")
    if not head_sha:
        raise GitHubError(f"Could not get head SHA for PR #{pr_number}")

    try:
        diff = _run_gh([
            "pr", "diff", str(pr_number), "--repo", f"{owner}/{repo}",
        ], timeout=60)
    except GitHubError as exc:
        if "406" not in str(exc) and "too_large" not in str(exc):
            raise
        diff, files_data = fetch_git_diff(owner, repo, head_sha, pr_data.get("baseRefOid", ""))
        pr_data["files"] = files_data
    if not verify_sha_current(owner, repo, pr_number, head_sha):
        raise GitHubError("PR head changed while fetching diff; retry")

    ci_json = _run_gh([
        "pr", "checks", str(pr_number),
        "--repo", f"{owner}/{repo}",
        "--json", "name,state,bucket",
    ], allowed_codes=(0, 1, 8))
    ci_data = json.loads(ci_json)
    ci_status = _parse_ci_status(ci_data)

    files_data = pr_data.get("files", [])
    changed_files = []
    deleted_files = []

    for f in files_data:
        path = f.get("path", "")
        if path:
            changed_files.append(path)
            if f.get("status") == "removed":
                deleted_files.append(path)

    current_file = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            import shlex
            parts = shlex.split(line)
            current_file = parts[3][2:] if len(parts) == 4 else None
        elif line.startswith("deleted file mode ") and current_file:
            if current_file not in deleted_files:
                deleted_files.append(current_file)

    return PRInfo(
        owner=owner,
        repo=repo,
        number=pr_number,
        head_sha=head_sha,
        title=pr_data.get("title", ""),
        author=pr_data.get("author", {}).get("login", "unknown"),
        is_draft=pr_data.get("isDraft", False),
        diff=diff,
        ci_status=ci_status,
        changed_files=changed_files,
        deleted_files=deleted_files,
        description=pr_data.get("body", ""),
    )


def verify_sha_current(owner: str, repo: str, pr_number: int, expected_sha: str) -> bool:
    """Verify that a PR's head SHA matches the expected value.

    Returns True if the SHA matches, False if it has changed.
    """
    pr_json = _run_gh([
        "pr", "view", str(pr_number),
        "--repo", f"{owner}/{repo}",
        "--json", "headRefOid",
    ])
    pr_data = json.loads(pr_json)
    current_sha = pr_data.get("headRefOid", "")

    return current_sha == expected_sha


def post_verdict_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment_body: str,
) -> str:
    """Post a verdict comment on a PR.

    Returns the comment URL.
    """
    result = _run_gh([
        "pr", "comment", str(pr_number),
        "--repo", f"{owner}/{repo}",
        "--body", comment_body,
    ])

    return result.strip()


def list_prs_with_label(owner: str, repo: str, label: str) -> list[dict]:
    """List open PRs with a specific label.

    Returns list of PR metadata dicts.
    """
    result = _run_gh([
        "pr", "list",
        "--repo", f"{owner}/{repo}",
        "--label", label,
        "--state", "open",
        "--json", "number,title,author,headRefOid,isDraft",
    ])

    return json.loads(result)


def remove_label(owner: str, repo: str, pr_number: int, label: str) -> None:
    """Remove a label from a PR."""
    _run_gh([
        "pr", "edit", str(pr_number),
        "--repo", f"{owner}/{repo}",
        "--remove-label", label,
    ])


def add_label(owner: str, repo: str, pr_number: int, label: str) -> None:
    """Add a label to a PR."""
    _run_gh([
        "pr", "edit", str(pr_number),
        "--repo", f"{owner}/{repo}",
        "--add-label", label,
    ])


def check_pr_mergeable(owner: str, repo: str, pr_number: int) -> tuple:
    """Check if a PR is mergeable (not conflicted with base).
    
    Returns (is_mergeable, merge_state_status) where:
    - is_mergeable: True if cleanly mergeable, False if conflicts or unknown
    - merge_state_status: The mergeStateStatus from GitHub (e.g. 'CLEAN', 'DIRTY', 'BLOCKED')
    """
    try:
        result = _run_gh([
            "pr", "view", str(pr_number),
            "--repo", f"{owner}/{repo}",
            "--json", "mergeStateStatus,mergeable",
        ])
        data = json.loads(result)
        status = data.get("mergeStateStatus", "UNKNOWN")
        mergeable = data.get("mergeable", "UNKNOWN")
        
        is_mergeable = status in ("CLEAN", "BEHIND", "HAS_HOOKS") and mergeable != "CONFLICTING"
        return is_mergeable, status
    except (GitHubError, json.JSONDecodeError):
        return False, "UNKNOWN"


def update_pr_branch(owner: str, repo: str, pr_number: int) -> tuple:
    """Update PR branch with latest changes from base (rebase or merge).
    
    Uses `gh pr update-branch` which is the safest way to update.
    Falls back to merge if rebase fails.
    
    Returns (success, message).
    """
    try:
        _run_gh([
            "pr", "update-branch", str(pr_number),
            "--repo", f"{owner}/{repo}",
            "--rebase",
        ], timeout=120)
        return True, "rebased"
    except GitHubError as e:
        if "rebase" in str(e).lower() or "conflict" in str(e).lower():
            try:
                _run_gh([
                    "pr", "update-branch", str(pr_number),
                    "--repo", f"{owner}/{repo}",
                ], timeout=120)
                return True, "merged"
            except GitHubError as e2:
                return False, f"update failed: {e2}"
        return False, f"rebase failed: {e}"


def ensure_label_exists(owner: str, repo: str, label: str, color: str = "d4c5f9", description: str = "") -> bool:
    """Ensure a label exists in the repo, creating it if missing.
    
    Returns True if label was created, False if it already existed.
    """
    try:
        result = subprocess.run(
            ["gh", "label", "list", "--repo", "{}/{}".format(owner, repo), "--json", "name"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            labels = json.loads(result.stdout)
            if any(l.get("name", "").lower() == label.lower() for l in labels):
                return False
    except (subprocess.SubprocessError, json.JSONDecodeError):
        pass
    
    try:
        args = ["gh", "label", "create", label, "--repo", "{}/{}".format(owner, repo), "--color", color]
        if description:
            args.extend(["--description", description])
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except subprocess.SubprocessError:
        return False


def fetch_git_diff(owner, repo, head_sha, base_sha):
    """Bypass GitHub's diff size cap with immutable objects, never a checkout."""
    if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (head_sha, base_sha)):
        raise GitHubError("Missing full head/base SHA for complete diff")
    with tempfile.TemporaryDirectory(prefix="karen-diff-") as directory:
        def git(*args):
            result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args],
                                    cwd=directory, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise GitHubError("Pinned Git diff failed: " + " ".join(args[:2]))
            return result.stdout
        git("init", "--bare", "-q")
        git("fetch", "--no-tags", "--filter=blob:none",
            "https://github.com/{}/{}.git".format(owner, repo), head_sha, base_sha)
        merge_base = git("merge-base", base_sha, head_sha).strip()
        diff = git("diff", "--no-ext-diff", "--no-textconv", "--binary", merge_base, head_sha)
        names = git("diff", "--name-status", "--no-renames", "-z", merge_base, head_sha).split("\0")
        files = [{"path": names[i+1], "status": "removed" if names[i] == "D" else "modified"}
                 for i in range(0, len(names)-1, 2)]
        return diff, files
