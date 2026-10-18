#!/usr/bin/env python3
"""Self-update logic for StoryPool services.

Each tick, the poller/dispatcher can:
1. git fetch origin cursor/storypool-all-in-one-47c7
2. Fast-forward if ahead (never if dirty or tests fail)
3. Reinstall only if HEAD changed
4. Re-copy karen module to ~/.local/share/karen/module
5. Clear __pycache__ directories
6. Migrate config to enable auto-discovery
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

BRANCH = "cursor/storypool-all-in-one-47c7"
INSTALL_DIR = Path(os.environ.get("STORYPOOL_INSTALL_DIR", "~/storypool-install")).expanduser()
KAREN_MODULE_DIR = Path("~/.local/share/karen/module").expanduser()


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
    - cluster/control_plane/backlog.py -> module/cluster/control_plane/backlog.py
    - cluster/control_plane/self_update.py -> module/cluster/control_plane/self_update.py
    - cluster/never_touch.py -> module/cluster/never_touch.py
    """
    if module_dir is None:
        module_dir = KAREN_MODULE_DIR
    
    src_karen = repo_dir / "cluster" / "control_plane" / "local_karen"
    src_backlog = repo_dir / "cluster" / "control_plane" / "backlog.py"
    src_self_update = repo_dir / "cluster" / "control_plane" / "self_update.py"
    src_never_touch = repo_dir / "cluster" / "never_touch.py"
    
    if not src_karen.is_dir():
        logger.error("Source karen module not found: %s", src_karen)
        return False
    
    dst_control_plane = module_dir / "cluster" / "control_plane"
    dst_karen = dst_control_plane / "local_karen"
    
    try:
        dst_control_plane.mkdir(parents=True, exist_ok=True)
        
        if dst_karen.exists():
            shutil.rmtree(dst_karen)
        shutil.copytree(src_karen, dst_karen)
        
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
    }  # type: dict
    
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
    
    result["pycache_cleared"] = clear_pycache(repo_dir / "cluster")
    result["pycache_cleared"] += clear_pycache(KAREN_MODULE_DIR)
    
    if copy_karen_module(repo_dir):
        result["module_copied"] = True
    
    if migrate_config():
        result["config_migrated"] = True
    
    if reinstall(repo_dir, machine):
        result["reinstalled"] = True
        result["reason"] = "updated and reinstalled"
    else:
        result["reason"] = "updated but reinstall failed"
    
    logger.info("Self-update: %s -> %s (%s)", 
                old_head[:8], new_head[:8], result["reason"])
    
    return result
