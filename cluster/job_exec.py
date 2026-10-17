"""Git checkout and command execution for cluster jobs."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from cluster.config import WORKER_PATH
from cluster.spec import JOB_ID_PATTERN, SpecError, validate_job_spec

WORK_ROOT = Path.home() / "work" / "cluster-jobs" / "jobs"
MIRROR_ROOT = WORK_ROOT.parent / "_mirrors"


def minimal_env(home: Path) -> dict[str, str]:
    # Never inherit worker credentials, loader flags, or Git configuration.
    return {"PATH": WORKER_PATH, "HOME": str(home), "LANG": "C", "LC_ALL": "C", "TZ": "UTC"}


def mirror_path(repo: str) -> Path:
    return MIRROR_ROOT / repo.replace("/", "_")


def job_workdir(job_id: str) -> Path:
    if not isinstance(job_id, str) or not JOB_ID_PATTERN.fullmatch(job_id):
        raise SpecError("invalid job id")
    root = WORK_ROOT.resolve()
    candidate = root / job_id
    wd = candidate.resolve()
    # Each check is separate (with its own reason) so tests can prove every
    # guard is load-bearing on its own.
    if wd == root:
        raise SpecError("job work directory must not alias the jobs root")
    if candidate.is_symlink():
        raise SpecError("job work directory must not be a symlink")
    if root not in wd.parents or wd != candidate:
        raise SpecError("job work directory must be strictly under jobs root")
    return wd


def ensure_mirror(repo: str, dry_run: bool) -> Path:
    mp = mirror_path(repo)
    env = minimal_env(WORK_ROOT.parent / "_git-home")
    if dry_run:
        mp.mkdir(parents=True, exist_ok=True)
        return mp
    mp.parent.mkdir(parents=True, exist_ok=True)
    remote = f"https://github.com/{repo}.git"
    if not (mp / "objects").is_dir():
        subprocess.run(
            # Full mirror: job clones use --shared and cannot lazily fetch missing
            # blobs from a partial (promisor) mirror, so checkout would fail.
            ["git", "clone", "--mirror", remote, str(mp)],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
    else:
        subprocess.run(["git", "remote", "update", "--prune"], cwd=mp, check=True, capture_output=True, text=True, env=env)
    return mp


def prepare_worktree(spec: dict[str, Any], dry_run: bool) -> Path:
    spec = validate_job_spec(spec)
    job_id = spec["id"]
    wd = job_workdir(job_id)
    if dry_run:
        wd.mkdir(parents=True, exist_ok=True)
        return wd
    if wd.exists():
        # Resolve and check again immediately before recursive deletion.
        shutil.rmtree(job_workdir(job_id))
    mp = ensure_mirror(spec["repo"], dry_run=False)
    wd.parent.mkdir(parents=True, exist_ok=True)
    env = minimal_env(WORK_ROOT.parent / "_git-home")
    subprocess.run(
        ["git", "clone", "--shared", "--", str(mp), str(wd)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    subprocess.run(["git", "fetch", "--", "origin", spec["ref"]], cwd=wd, check=True, capture_output=True, text=True, env=env)
    subprocess.run(["git", "checkout", "--detach", "FETCH_HEAD", "--"], cwd=wd, check=True, capture_output=True, text=True, env=env)
    return wd


def run_cmd(
    spec: dict[str, Any],
    workdir: Path,
    dry_run: bool,
) -> tuple[int, str, bool]:
    spec = validate_job_spec(spec)
    if dry_run:
        time.sleep(0.01)
        return 0, "dry-run\n", False
    home = workdir / ".job-home"
    if home.is_symlink():
        raise SpecError("job HOME must not be a symlink")
    home.mkdir(mode=0o700, exist_ok=True)
    env = minimal_env(home.resolve())
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
