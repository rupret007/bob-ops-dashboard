#!/usr/bin/env python3
"""Self-update logic for StoryPool services.

Each tick, the poller/dispatcher can:
1. git fetch origin cursor/storypool-all-in-one-47c7
2. Fast-forward if ahead (never if dirty or tests fail)
3. Reinstall only if HEAD changed
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

BRANCH = "cursor/storypool-all-in-one-47c7"
INSTALL_DIR = Path(os.environ.get("STORYPOOL_INSTALL_DIR", "~/storypool-install")).expanduser()


def get_current_head(repo_dir: Path) -> Optional[str]:
    """Get current HEAD sha."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return None


def is_working_tree_dirty(repo_dir: Path) -> bool:
    """Check if working tree has uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(result.stdout.strip())
    except (subprocess.SubprocessError, FileNotFoundError):
        return True


def fetch_branch(repo_dir: Path) -> bool:
    """Fetch the target branch from origin."""
    try:
        result = subprocess.run(
            ["git", "fetch", "origin", BRANCH],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def can_fast_forward(repo_dir: Path) -> Tuple[bool, str]:
    """Check if we can fast-forward to origin/BRANCH.
    
    Returns (can_ff, remote_sha).
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "origin/{}".format(BRANCH)],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False, ""
        remote_sha = result.stdout.strip()
        
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "HEAD", "origin/{}".format(BRANCH)],
            cwd=repo_dir,
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0, remote_sha
    except (subprocess.SubprocessError, FileNotFoundError):
        return False, ""


def fast_forward(repo_dir: Path) -> bool:
    """Fast-forward to origin/BRANCH."""
    try:
        result = subprocess.run(
            ["git", "checkout", BRANCH],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            subprocess.run(
                ["git", "checkout", "-B", BRANCH, "origin/{}".format(BRANCH)],
                cwd=repo_dir,
                capture_output=True,
                timeout=30,
            )
        
        result = subprocess.run(
            ["git", "merge", "--ff-only", "origin/{}".format(BRANCH)],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def run_tests(repo_dir: Path) -> bool:
    """Run quick tests to verify update is safe."""
    try:
        result = subprocess.run(
            ["python3", "-m", "pytest", 
             "cluster/tests/test_never_touch.py",
             "cluster/tests/test_install_scripts.py",
             "-q", "--tb=no"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONPATH": str(repo_dir)},
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def reinstall(repo_dir: Path, machine: str) -> bool:
    """Run the install script for the machine."""
    script = "install-all-mini.sh" if machine == "mini" else "install-all-nucbox.sh"
    script_path = repo_dir / "cluster" / "control_plane" / "install" / script
    
    if not script_path.is_file():
        logger.error("Install script not found: %s", script_path)
        return False
    
    try:
        result = subprocess.run(
            ["bash", str(script_path)],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def self_update(machine: str = "mini", install_dir: Optional[Path] = None) -> dict:
    """Attempt self-update if safe.
    
    Returns dict with update status.
    """
    repo_dir = install_dir or INSTALL_DIR
    
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repo_dir": str(repo_dir),
        "updated": False,
        "reinstalled": False,
        "reason": "",
    }
    
    if not repo_dir.is_dir():
        result["reason"] = "repo_dir does not exist"
        return result
    
    old_head = get_current_head(repo_dir)
    if not old_head:
        result["reason"] = "could not get current HEAD"
        return result
    result["old_head"] = old_head[:12]
    
    if is_working_tree_dirty(repo_dir):
        result["reason"] = "working tree is dirty"
        return result
    
    if not fetch_branch(repo_dir):
        result["reason"] = "fetch failed"
        return result
    
    can_ff, remote_sha = can_fast_forward(repo_dir)
    if not can_ff:
        result["reason"] = "cannot fast-forward (diverged or already up-to-date)"
        return result
    
    if remote_sha == old_head:
        result["reason"] = "already up-to-date"
        return result
    
    result["remote_head"] = remote_sha[:12]
    
    if not fast_forward(repo_dir):
        result["reason"] = "fast-forward failed"
        return result
    
    new_head = get_current_head(repo_dir)
    if new_head != remote_sha:
        result["reason"] = "HEAD mismatch after fast-forward"
        return result
    
    result["new_head"] = new_head[:12]
    result["updated"] = True
    
    if not run_tests(repo_dir):
        result["reason"] = "tests failed after update"
        result["updated"] = False
        return result
    
    if reinstall(repo_dir, machine):
        result["reinstalled"] = True
        result["reason"] = "updated and reinstalled"
    else:
        result["reason"] = "updated but reinstall failed"
    
    logger.info("Self-update: %s -> %s (%s)", 
                old_head[:8], new_head[:8], result["reason"])
    
    return result
