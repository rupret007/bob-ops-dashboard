#!/usr/bin/env python3
"""Self-update logic for StoryPool services.

Each tick, the poller/dispatcher can:
1. git fetch origin cursor/storypool-all-in-one-47c7
2. Fast-forward if ahead (never if dirty or tests fail)
3. Reinstall only if HEAD changed
4. Re-copy karen module to ~/.local/share/karen/module
5. Clear __pycache__ directories
6. Migrate config to enable auto-discovery

OUTCOME-VERIFIED: Every self-update call logs a concrete outcome:
- "UPDATED: X -> Y" when HEAD changes
- "SKIPPED: reason" when no update needed (with specific reason)
- "FAILED: error" when something went wrong
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

BRANCH = "cursor/storypool-all-in-one-47c7"
INSTALL_DIR = Path(os.environ.get("STORYPOOL_INSTALL_DIR", "~/storypool-install")).expanduser()
KAREN_MODULE_DIR = Path("~/.local/share/karen/module").expanduser()
UPDATE_LOG_FILE = Path(os.environ.get(
    "STORYPOOL_UPDATE_LOG", "~/.local/share/storypool/self-update.log")).expanduser()


def _log_update_outcome(outcome: str, details: dict) -> None:
    """Log update outcome to both logger and persistent log file.
    
    ALWAYS logs at INFO level so the outcome is visible.
    """
    logger.info("Self-update %s", outcome)
    
    try:
        UPDATE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "outcome": outcome,
            **details,
        }
        with open(UPDATE_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def get_remote_head(repo_dir: Path) -> Optional[str]:
    """Get remote HEAD sha for origin/BRANCH."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "origin/{}".format(BRANCH)],
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


def get_current_branch(repo_dir: Path) -> Optional[str]:
    """Get current branch name."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
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


def check_head_lag(repo_dir: Optional[Path] = None) -> dict:
    """Check if HEAD lags behind origin.
    
    Returns dict with:
    - lagging: bool
    - local_head: str
    - remote_head: str
    - commits_behind: int
    - current_branch: str
    """
    repo_dir = repo_dir or INSTALL_DIR
    result = {
        "lagging": False,
        "local_head": "",
        "remote_head": "",
        "commits_behind": 0,
        "current_branch": "",
        "error": "",
    }
    
    if not repo_dir.is_dir():
        result["error"] = "repo_dir does not exist"
        return result
    
    local_head = get_current_head(repo_dir)
    if not local_head:
        result["error"] = "could not get local HEAD"
        return result
    result["local_head"] = local_head[:12]
    
    result["current_branch"] = get_current_branch(repo_dir) or "detached"
    
    if not fetch_branch(repo_dir):
        result["error"] = "fetch failed"
        return result
    
    remote_head = get_remote_head(repo_dir)
    if not remote_head:
        result["error"] = "could not get remote HEAD"
        return result
    result["remote_head"] = remote_head[:12]
    
    if local_head == remote_head:
        result["lagging"] = False
        return result
    
    try:
        count_result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/{}".format(BRANCH)],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if count_result.returncode == 0:
            result["commits_behind"] = int(count_result.stdout.strip())
            result["lagging"] = result["commits_behind"] > 0
    except (subprocess.SubprocessError, ValueError):
        result["lagging"] = local_head != remote_head
    
    return result


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
    """Check if working tree has uncommitted changes to tracked files.
    
    Ignores untracked files (like a stray file named '-') because they don't
    affect fast-forward safety. Only blocks on modified/staged tracked files.
    """
    try:
        # Use git diff to check for modifications to tracked files
        # (staged or unstaged), ignoring untracked files entirely
        result = subprocess.run(
            ["git", "diff", "--quiet", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            return True  # Has uncommitted changes to tracked files
        
        # Also check for staged changes
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_dir,
            capture_output=True,
            timeout=10,
        )
        return result.returncode != 0
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


def fast_forward(repo_dir: Path) -> Tuple[bool, str]:
    """Fast-forward to origin/BRANCH.
    
    Returns (success, old_head_for_rollback).
    On failure, the repo should still be at old_head.
    """
    old_head = get_current_head(repo_dir)
    
    try:
        result = subprocess.run(
            ["git", "checkout", BRANCH],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return False, old_head or ""

        result = subprocess.run(
            ["git", "merge", "--ff-only", "origin/{}".format(BRANCH)],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0, old_head or ""
    except (subprocess.SubprocessError, FileNotFoundError):
        return False, old_head or ""


def rollback_to_sha(repo_dir: Path, sha: str) -> bool:
    """Rollback to a specific SHA after a failed update.
    
    This ensures the last good version keeps running.
    """
    if not sha:
        return False
    
    try:
        result = subprocess.run(
            ["git", "checkout", sha],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            logger.info("Rolled back to %s after failed update", sha[:12])
            return True
        
        result = subprocess.run(
            ["git", "reset", "--hard", sha],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            logger.info("Hard reset to %s after failed update", sha[:12])
            return True
        
        return False
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def run_tests(repo_dir: Path) -> bool:
    """Run quick tests to verify update is safe."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", 
             "cluster/tests/test_never_touch.py",
             "cluster/tests/test_install_scripts.py",
             "cluster/tests/test_python39.py",
             "cluster/control_plane/tests/test_guardian.py",
             "-q", "--tb=short", "-r", "fE"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONPATH": str(repo_dir)},
        )
        if result.returncode != 0:
            # Persist test identifiers only; assertion output may contain credentials.
            failures = [line for line in (result.stdout or "").splitlines()
                        if line.startswith(("FAILED ", "ERROR "))]
            names = [line.split(" - ", 1)[0] for line in failures]
            _log_update_outcome("FAILED: update validation", {
                "failing_tests": names or ["pytest collection/startup failed"],
                "test_exit_code": result.returncode,
            })
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        _log_update_outcome("FAILED: update validation", {"test_error": type(exc).__name__})
        return False


def clear_pycache(directory: Path) -> int:
    """Clear all __pycache__ directories and .pyc files.
    
    Returns count of items removed.
    """
    count = 0
    if not directory.is_dir():
        return count
    
    for pycache in directory.rglob("__pycache__"):
        if pycache.is_dir():
            try:
                shutil.rmtree(pycache)
                count += 1
            except OSError:
                pass

    for pyc in directory.rglob("*.pyc"):
        try:
            pyc.unlink()
            count += 1
        except OSError:
            pass

    return count


def copy_karen_module(repo_dir: Path, module_dir: Optional[Path] = None) -> bool:
    """Copy karen module from repo to installed location.
    
    Copies:
    - cluster/control_plane/local_karen -> module/cluster/control_plane/local_karen
    - cluster/control_plane/dashboard -> module/cluster/control_plane/dashboard
    - cluster/control_plane/backlog.py -> module/cluster/control_plane/backlog.py
    - cluster/control_plane/self_update.py -> module/cluster/control_plane/self_update.py
    - cluster/never_touch.py -> module/cluster/never_touch.py
    """
    if module_dir is None:
        module_dir = KAREN_MODULE_DIR
    
    src_karen = repo_dir / "cluster" / "control_plane" / "local_karen"
    src_dashboard = repo_dir / "cluster" / "control_plane" / "dashboard"
    src_backlog = repo_dir / "cluster" / "control_plane" / "backlog.py"
    src_self_update = repo_dir / "cluster" / "control_plane" / "self_update.py"
    src_never_touch = repo_dir / "cluster" / "never_touch.py"
    
    if not src_karen.is_dir():
        logger.error("Source karen module not found: %s", src_karen)
        return False
    
    dst_control_plane = module_dir / "cluster" / "control_plane"
    dst_karen = dst_control_plane / "local_karen"
    dst_dashboard = dst_control_plane / "dashboard"
    
    try:
        dst_control_plane.mkdir(parents=True, exist_ok=True)
        # Poller optional imports depend on the complete control plane, not just
        # local_karen. Keep the installed package self-contained.
        shutil.copytree(repo_dir / "cluster" / "control_plane", dst_control_plane,
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))

        
        if dst_karen.exists():
            shutil.rmtree(dst_karen)
        shutil.copytree(src_karen, dst_karen)
        
        if src_dashboard.is_dir():
            if dst_dashboard.exists():
                shutil.rmtree(dst_dashboard)
            shutil.copytree(src_dashboard, dst_dashboard)
        
        if src_backlog.is_file():
            shutil.copy2(src_backlog, dst_control_plane / "backlog.py")
        
        if src_self_update.is_file():
            shutil.copy2(src_self_update, dst_control_plane / "self_update.py")
        
        if src_never_touch.is_file():
            shutil.copy2(src_never_touch, module_dir / "cluster" / "never_touch.py")
        
        (module_dir / "cluster" / "__init__.py").touch()
        (dst_control_plane / "__init__.py").touch()
        
        clear_pycache(module_dir)
        
        logger.info("Copied karen module to %s", module_dir)
        return True
    except OSError as e:
        logger.error("Failed to copy karen module: %s", e)
        return False


def migrate_config() -> bool:
    """Migrate karen config to enable auto-discovery.
    
    Returns True if config was migrated.
    """
    try:
        from cluster.control_plane.local_karen.config import KarenConfig
        return KarenConfig.migrate_config()
    except ImportError:
        pass
    
    config_path = Path("~/.config/karen/config.json").expanduser()
    if not config_path.is_file():
        return False
    
    try:
        data = json.loads(config_path.read_text())
    except json.JSONDecodeError:
        return False
    
    migrated = False
    
    if "repos" not in data:
        data["repos"] = "auto"
        migrated = True
    elif isinstance(data["repos"], list):
        if not data["repos"] or len(data["repos"]) <= 1:
            data["repos"] = "auto"
            migrated = True
    elif data["repos"] is None or data["repos"] == "":
        data["repos"] = "auto"
        migrated = True
    
    if migrated:
        config_path.write_text(json.dumps(data, indent=2))
        logger.info("Migrated config: repos set to 'auto'")
    
    return migrated


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
    
    OUTCOME-VERIFIED: Always logs one of:
    - "UPDATED: old_head -> new_head"
    - "SKIPPED: specific reason"
    - "FAILED: specific error"
    
    Steps:
    1. Fetch and fast-forward git
    2. Run quick tests
    3. Re-copy karen module to ~/.local/share/karen/module
    4. Clear __pycache__ everywhere
    5. Migrate config to enable auto-discovery
    6. Run full reinstall script
    
    Returns dict with update status.
    """
    repo_dir = install_dir or INSTALL_DIR
    
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repo_dir": str(repo_dir),
        "updated": False,
        "reinstalled": False,
        "module_copied": False,
        "config_migrated": False,
        "pycache_cleared": 0,
        "reason": "",
        "outcome": "",  # UPDATED, SKIPPED, or FAILED
    }  # type: dict
    
    if not repo_dir.is_dir():
        result["reason"] = "repo_dir does not exist"
        result["outcome"] = "FAILED"
        _log_update_outcome("FAILED: repo_dir does not exist", result)
        return result
    
    old_head = get_current_head(repo_dir)
    if not old_head:
        result["reason"] = "could not get current HEAD"
        result["outcome"] = "FAILED"
        _log_update_outcome("FAILED: could not get current HEAD", result)
        return result
    result["old_head"] = old_head[:12]
    
    current_branch = get_current_branch(repo_dir)
    result["current_branch"] = current_branch or "detached"
    
    if current_branch and current_branch != BRANCH and current_branch != "HEAD":
        result["reason"] = "on wrong branch: {} (expected {})".format(current_branch, BRANCH)
        result["outcome"] = "SKIPPED"
        _log_update_outcome("SKIPPED: on wrong branch {} (expected {})".format(current_branch, BRANCH), result)
        return result
    
    if is_working_tree_dirty(repo_dir):
        result["reason"] = "working tree has uncommitted changes to tracked files"
        result["outcome"] = "SKIPPED"
        _log_update_outcome("SKIPPED: working tree has uncommitted changes", result)
        return result
    
    if not fetch_branch(repo_dir):
        result["reason"] = "git fetch failed"
        result["outcome"] = "FAILED"
        _log_update_outcome("FAILED: git fetch failed", result)
        return result
    
    remote_sha = get_remote_head(repo_dir)
    if not remote_sha:
        result["reason"] = "could not get remote HEAD after fetch"
        result["outcome"] = "FAILED"
        _log_update_outcome("FAILED: could not get remote HEAD", result)
        return result
    result["remote_head"] = remote_sha[:12]
    
    if remote_sha == old_head:
        result["reason"] = "already at latest: {}".format(old_head[:12])
        result["outcome"] = "SKIPPED"
        _log_update_outcome("SKIPPED: already at latest {}".format(old_head[:12]), result)
        return result
    
    can_ff, _ = can_fast_forward(repo_dir)
    if not can_ff:
        result["reason"] = "cannot fast-forward (HEAD diverged from origin)"
        result["outcome"] = "SKIPPED"
        _log_update_outcome("SKIPPED: cannot fast-forward, HEAD diverged", result)
        return result
    
    ff_success, rollback_sha = fast_forward(repo_dir)
    if not ff_success:
        result["reason"] = "fast-forward merge failed"
        result["outcome"] = "FAILED"
        _log_update_outcome("FAILED: fast-forward merge failed", result)
        return result
    
    new_head = get_current_head(repo_dir)
    if new_head != remote_sha:
        result["reason"] = "HEAD mismatch after fast-forward"
        result["outcome"] = "FAILED"
        rollback_to_sha(repo_dir, rollback_sha)
        _log_update_outcome("FAILED: HEAD mismatch after fast-forward", result)
        return result
    
    result["new_head"] = new_head[:12]
    
    if not run_tests(repo_dir):
        result["reason"] = "tests failed after update - rolled back"
        result["outcome"] = "FAILED"
        result["rolled_back"] = rollback_to_sha(repo_dir, rollback_sha)
        _log_update_outcome("FAILED: tests failed, rolled back to {}".format(rollback_sha[:12] if rollback_sha else "unknown"), result)
        return result
    
    result["updated"] = True
    result["pycache_cleared"] = clear_pycache(repo_dir / "cluster")
    result["pycache_cleared"] += clear_pycache(KAREN_MODULE_DIR)
    
    if copy_karen_module(repo_dir):
        result["module_copied"] = True
    
    if migrate_config():
        result["config_migrated"] = True
    
    if reinstall(repo_dir, machine):
        result["reinstalled"] = True
        result["reason"] = "updated {} -> {} and reinstalled".format(old_head[:8], new_head[:8])
    else:
        result["reason"] = "updated {} -> {} but reinstall failed".format(old_head[:8], new_head[:8])
    
    result["outcome"] = "UPDATED"
    _log_update_outcome("UPDATED: {} -> {}".format(old_head[:8], new_head[:8]), result)
    
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fast-forward and reinstall StoryPool")
    parser.add_argument('--machine', choices=['mini', 'nucbox'], default=os.environ.get('STORYPOOL_MACHINE', 'mini'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        result = self_update(machine=args.machine)
    except Exception as exc:
        _log_update_outcome('FAILED: ' + type(exc).__name__, {'reason': type(exc).__name__})
        return 1
    print(f"{result['outcome']}: {result['reason']}", flush=True)
    return 1 if result['outcome'] == 'FAILED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
