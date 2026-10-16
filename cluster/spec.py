"""Job spec v1 validation (stdlib only)."""

from __future__ import annotations

import math
import re
from typing import Any

from cluster.config import (
    ALLOWED_ARGV, ALLOWED_ENV, ALLOWED_REPOS, MAX_LOAD_CEILING, MAX_TIMEOUT_S,
)

SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-"),
    re.compile(r"sk-(?:(?:proj|ant)-)?[a-zA-Z0-9_-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35,}"),
    re.compile(r"\d+:[A-Za-z0-9_-]{35}"),
    re.compile(r"BSA[A-Za-z0-9_-]{20,}"),
    re.compile(r"[A-Za-z0-9+/_-]{80,}={0,2}"),
    re.compile(r"ghp_[a-zA-Z0-9]{36,}"),
    re.compile(r"github_pat_[a-zA-Z0-9_]{22,}"),
]

ALLOWED_CLASSES = frozenset(ALLOWED_ARGV)
JOB_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}")
REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*")


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
    # Catch credentials split at any boundary, including across several args.
    _check_secrets_in_text("".join(argv), "adjacent cmd elements")


def validate_job_spec(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SpecError("spec must be a JSON object")
    if data.get("v") != 1:
        raise SpecError("v must be 1")
    job_id = data.get("id")
    if not isinstance(job_id, str) or not JOB_ID_PATTERN.fullmatch(job_id):
        raise SpecError("id must match [A-Za-z0-9_-]{1,64}")
    klass = data.get("class")
    if not isinstance(klass, str) or klass not in ALLOWED_CLASSES:
        raise SpecError(f"class must be one of {sorted(ALLOWED_CLASSES)}")
    repo = data.get("repo")
    if not isinstance(repo, str) or repo not in ALLOWED_REPOS:
        raise SpecError("repo is not allowlisted")
    ref = data.get("ref")
    if not isinstance(ref, str) or not (
        SHA_PATTERN.fullmatch(ref) or (
            1 <= len(ref) <= 200 and REF_PATTERN.fullmatch(ref)
            and ".." not in ref
            and all(not part.endswith((".", ".lock")) for part in ref.split("/"))
        )
    ):
        raise SpecError("ref must be a full SHA or a strict branch/tag ref")
    cmd = data.get("cmd")
    if not isinstance(cmd, list) or len(cmd) == 0:
        raise SpecError("cmd must be a non-empty argv list")
    if any(not isinstance(x, str) for x in cmd):
        raise SpecError("cmd must contain only strings")
    _check_secrets_in_argv(cmd)
    if tuple(cmd) not in ALLOWED_ARGV[klass]:
        raise SpecError("cmd is not allowlisted for this job class")
    env = data.get("env")
    if env is not None:
        if not isinstance(env, dict):
            raise SpecError("env must be an object")
        for k, v in env.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise SpecError("env keys and values must be strings")
            _check_secrets_in_text(k, f"env key {k}")
            _check_secrets_in_text(v, f"env[{k}]")
            if k not in ALLOWED_ENV or v not in ALLOWED_ENV[k]:
                raise SpecError("env name or value is not allowlisted")
    timeout_s = data.get("timeout_s")
    if (type(timeout_s) not in (int, float)
            or not 0 < timeout_s <= MAX_TIMEOUT_S or not math.isfinite(timeout_s)):
        raise SpecError(f"timeout_s must be finite and in (0, {MAX_TIMEOUT_S}]")
    load_ceiling = data.get("load_ceiling")
    if (type(load_ceiling) not in (int, float)
            or not 0 < load_ceiling <= MAX_LOAD_CEILING or not math.isfinite(load_ceiling)):
        raise SpecError(f"load_ceiling must be finite and in (0, {MAX_LOAD_CEILING}]")
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
