"""Git checkout and command execution for cluster jobs."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

WORK_ROOT = Path.home() / "work" / "cluster-jobs"
MIRROR_ROOT = WORK_ROOT / "_mirrors"


def mirror_path(repo: str) -> Path:
    return MIRROR_ROOT / repo.replace("/", "_")


def job_workdir(job_id: str) -> Path:
    return WORK_ROOT / job_id


def ensure_mirror(repo: str, dry_run: bool) -> Path:
    mp = mirror_path(repo)
    if dry_run:
        mp.mkdir(parents=True, exist_ok=True)
        return mp
    mp.parent.mkdir(parents=True, exist_ok=True)
    remote = f"https://github.com/{repo}.git"
    if not (mp / "objects").is_dir():
        subprocess.run(
            ["git", "clone", "--mirror", "--filter=blob:none", remote, str(mp)],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        subprocess.run(["git", "remote", "update", "--prune"], cwd=mp, check=True, capture_output=True, text=True)
    return mp


def prepare_worktree(spec: dict[str, Any], dry_run: bool) -> Path:
    job_id = spec["id"]
    wd = job_workdir(job_id)
    if dry_run:
        wd.mkdir(parents=True, exist_ok=True)
        return wd
    if wd.exists():
        subprocess.run(["rm", "-rf", str(wd)], check=True)
    mp = ensure_mirror(spec["repo"], dry_run=False)
    wd.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--shared", str(mp), str(wd)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "fetch", "origin", spec["ref"]], cwd=wd, check=True, capture_output=True, text=True)
    subprocess.run(["git", "checkout", "FETCH_HEAD"], cwd=wd, check=True, capture_output=True, text=True)
    return wd


def run_cmd(
    spec: dict[str, Any],
    workdir: Path,
    dry_run: bool,
) -> tuple[int, str, bool]:
    if dry_run:
        time.sleep(0.01)
        return 0, "dry-run\n", False
    env = os.environ.copy()
    env.update(spec.get("env") or {})
    timeout = spec["timeout_s"]
    try:
        proc = subprocess.run(
            spec["cmd"],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        tail = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, tail, False
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else (e.stdout or b"").decode()
        err = (e.stderr or "") if isinstance(e.stderr, str) else (e.stderr or b"").decode()
        return -1, out + err, True
