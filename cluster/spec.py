"""Job spec v1 validation (stdlib only)."""

from __future__ import annotations

import re
from typing import Any

SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{36,}"),
    re.compile(r"github_pat_[a-zA-Z0-9_]{22,}"),
]

ALLOWED_CLASSES = frozenset({"test", "build", "render"})


class SpecError(ValueError):
    pass


def _check_secrets_in_text(text: str, where: str) -> None:
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            raise SpecError(f"possible secret in {where}")


def _check_secrets_in_argv(argv: list[Any]) -> None:
    for i, item in enumerate(argv):
        if not isinstance(item, str):
            raise SpecError(f"cmd[{i}] must be a string")
        _check_secrets_in_text(item, f"cmd[{i}]")


def validate_job_spec(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SpecError("spec must be a JSON object")
    if data.get("v") != 1:
        raise SpecError("v must be 1")
    job_id = data.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise SpecError("id must be a non-empty string")
    if "/" in job_id or ".." in job_id:
        raise SpecError("id must not contain path segments")
    klass = data.get("class")
    if klass not in ALLOWED_CLASSES:
        raise SpecError(f"class must be one of {sorted(ALLOWED_CLASSES)}")
    repo = data.get("repo")
    if not isinstance(repo, str) or "/" not in repo:
        raise SpecError("repo must be owner/name")
    ref = data.get("ref")
    if not isinstance(ref, str) or not ref:
        raise SpecError("ref must be a non-empty string")
    cmd = data.get("cmd")
    if not isinstance(cmd, list) or len(cmd) == 0:
        raise SpecError("cmd must be a non-empty argv list")
    if any(not isinstance(x, str) for x in cmd):
        raise SpecError("cmd must contain only strings")
    _check_secrets_in_argv(cmd)
    env = data.get("env")
    if env is not None:
        if not isinstance(env, dict):
            raise SpecError("env must be an object")
        for k, v in env.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise SpecError("env keys and values must be strings")
            _check_secrets_in_text(k, f"env key {k}")
            _check_secrets_in_text(v, f"env[{k}]")
    timeout_s = data.get("timeout_s")
    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise SpecError("timeout_s must be a positive number")
    load_ceiling = data.get("load_ceiling")
    if not isinstance(load_ceiling, (int, float)) or load_ceiling <= 0:
        raise SpecError("load_ceiling must be a positive number")
    return {
        "v": 1,
        "id": job_id,
        "class": klass,
        "repo": repo,
        "ref": ref,
        "cmd": list(cmd),
        "timeout_s": float(timeout_s),
        "load_ceiling": float(load_ceiling),
        **({"env": dict(env)} if env else {}),
    }


def job_subject(spec: dict[str, Any]) -> str:
    return f"jobs.{spec['class']}.{spec['id']}"
