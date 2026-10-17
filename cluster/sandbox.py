"""Container sandbox for guest jobs: no network, read-only root, no capabilities."""

from __future__ import annotations

from pathlib import Path

from cluster.spec import SpecError

DEFAULT_IMAGE = "docker.io/library/node:22-bookworm-slim"
PY_IMAGE = "docker.io/library/python:3.12-slim"


def image_for(cmd: list[str]) -> str:
    return PY_IMAGE if cmd and cmd[0] == "python3" else DEFAULT_IMAGE


def sandbox_argv(runtime: str, workdir: Path, cmd: list[str], job_id: str,
                 mem: str = "2g", cpus: str = "2", pids: int = 512) -> list[str]:
    if runtime not in ("docker", "podman"):
        raise SpecError("sandbox runtime must be docker or podman")
    wd = Path(workdir).resolve()
    if ":" in str(wd) or "," in str(wd):
        raise SpecError("workdir path not safe for a bind mount")
    return [
        runtime, "run", "--rm", "--name", f"bobsbx-{job_id}"[:63],
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(pids), "--memory", mem, "--cpus", cpus,
        "--user", "1000:1000",
        "--tmpfs", "/tmp:rw,size=512m",
        "--tmpfs", "/home/job:rw,size=256m",
        "-e", "HOME=/home/job", "-e", "CI=true", "-e", "LANG=C.UTF-8",
        "--mount", f"type=bind,src={wd},dst=/work",
        "-w", "/work",
        image_for(cmd), *cmd,
    ]
