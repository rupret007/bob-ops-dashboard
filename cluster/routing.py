"""Capability-routed job spec (v2), Jeff's cost-tier routing and runner argv."""

from __future__ import annotations

import math
from typing import Any

from cluster.capabilities import CAPABILITIES, GUEST_CAPS, cap_subject
from cluster.config import ALLOWED_ARGV, ALLOWED_REPOS, MAX_LOAD_CEILING, MAX_TIMEOUT_S
from cluster.spec import (
    JOB_ID_PATTERN, REF_PATTERN, SHA_PATTERN, SpecError, _check_secrets_in_argv,
    _check_secrets_in_text,
)

MAX_PROMPT_CHARS = 16000

# Jeff's routing: Codex high for hard logic, security and Karen-fail fixes;
# cheaper models for routine work. First available capability wins.
ROUTES: dict[str, tuple[str, ...]] = {
    "hard": ("llm.codex.high", "llm.claude", "llm.codex"),
    "security": ("llm.codex.high",),
    "karen_fail": ("llm.codex.high", "llm.claude"),
    "routine": ("llm.composer", "llm.minimax", "llm.gemini", "llm.codex"),
    "docs": ("llm.composer", "llm.gemini", "llm.minimax"),
    "build": ("shell.build",),
    "test": ("shell.test",),
    "untrusted": ("sandbox.test",),
}

# Non-LLM capabilities reuse the existing exact argv allowlists.
SHELL_ARGV = {
    "shell.build": ALLOWED_ARGV["build"],
    "shell.test": ALLOWED_ARGV["test"],
    "sandbox.test": ALLOWED_ARGV["test"],
}


def pick_capability(kind: str, available: set[str] | None = None) -> str:
    """Return the capability a job of `kind` should request.

    With `available` (caps currently advertised by online nodes) the first
    route entry that some node can serve is chosen; security work never falls
    back to a cheaper tier.
    """
    route = ROUTES.get(kind)
    if not route:
        raise SpecError(f"unknown job kind {kind!r}")
    if available is None:
        return route[0]
    for cap in route:
        if cap in available:
            return cap
    raise SpecError(f"no online node offers {route} for kind {kind}")


def _check_ref(ref: Any) -> str:
    if not isinstance(ref, str) or not (
        SHA_PATTERN.fullmatch(ref) or (
            1 <= len(ref) <= 200 and REF_PATTERN.fullmatch(ref)
            and ".." not in ref
            and all(not part.endswith((".", ".lock")) for part in ref.split("/"))
        )
    ):
        raise SpecError("ref must be a full SHA or a strict branch/tag ref")
    return ref


def _num(data: dict, key: str, cap: float) -> float:
    v = data.get(key)
    if type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= cap:
        raise SpecError(f"{key} must be finite and in (0, {cap}]")
    return float(v)


def validate_cap_spec(data: Any, guest: bool = False) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SpecError("spec must be a JSON object")
    if data.get("v") != 2:
        raise SpecError("v must be 2")
    allowed_keys = {"v", "id", "cap", "repo", "ref", "prompt", "cmd", "timeout_s", "load_ceiling"}
    extra = set(data) - allowed_keys
    if extra:
        raise SpecError(f"unknown fields {sorted(extra)}")
    job_id = data.get("id")
    if not isinstance(job_id, str) or not JOB_ID_PATTERN.fullmatch(job_id):
        raise SpecError("id must match [A-Za-z0-9_-]{1,64}")
    cap = data.get("cap")
    if cap not in CAPABILITIES:
        raise SpecError("cap is not a known capability")
    if guest and cap not in GUEST_CAPS:
        raise SpecError("guests only run sandbox capabilities")
    repo = data.get("repo")
    if repo not in ALLOWED_REPOS:
        raise SpecError("repo is not allowlisted")
    out: dict[str, Any] = {
        "v": 2, "id": job_id, "cap": cap, "repo": repo, "ref": _check_ref(data.get("ref")),
        "timeout_s": _num(data, "timeout_s", MAX_TIMEOUT_S),
        "load_ceiling": _num(data, "load_ceiling", MAX_LOAD_CEILING),
    }
    if cap.startswith("llm."):
        prompt = data.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
            raise SpecError(f"llm jobs need a prompt of 1..{MAX_PROMPT_CHARS} chars")
        _check_secrets_in_text(prompt, "prompt")
        if "cmd" in data:
            raise SpecError("llm jobs must not carry cmd")
        out["prompt"] = prompt
    else:
        cmd = data.get("cmd")
        if not isinstance(cmd, list) or not cmd or any(not isinstance(x, str) for x in cmd):
            raise SpecError("cmd must be a non-empty argv list of strings")
        _check_secrets_in_argv(cmd)
        if tuple(cmd) not in SHELL_ARGV[cap]:
            raise SpecError("cmd is not allowlisted for this capability")
        if "prompt" in data:
            raise SpecError("shell jobs must not carry a prompt")
        out["cmd"] = list(cmd)
    return out


def checkout_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """A v1 spec used only to reuse job_exec.prepare_worktree's guarded checkout."""
    return {
        "v": 1, "id": spec["id"], "class": "test", "repo": spec["repo"], "ref": spec["ref"],
        "cmd": ["npm", "test"], "timeout_s": spec["timeout_s"], "load_ceiling": spec["load_ceiling"],
    }


def subject_for(spec: dict[str, Any]) -> str:
    if spec["cap"] in GUEST_CAPS:
        return f"sandbox.jobs.{spec['id']}"
    return cap_subject(spec["cap"], spec["id"])


def llm_argv(cap: str, cli: str) -> tuple[list[str], bool]:
    """(argv, prompt_via_stdin). The prompt is appended when not via stdin."""
    if cap == "llm.codex.high":
        return [cli, "exec", "--skip-git-repo-check", "-s", "workspace-write",
                "-c", "model_reasoning_effort=high", "-"], True
    if cap == "llm.codex":
        return [cli, "exec", "--skip-git-repo-check", "-s", "workspace-write", "-"], True
    if cap == "llm.claude":
        return [cli, "-p", "--permission-mode", "acceptEdits"], True
    if cap == "llm.composer":
        return [cli, "agent", "--model", "composer-2.5", "-f", "--trust", "-p"], False
    if cap == "llm.gemini":
        return [cli, "-p"], False
    raise SpecError(f"no local runner for {cap} in v1")


CLI_FOR_PROVIDER = {"codex": "codex", "claude": "claude", "cursor": "cursor-agent", "gemini": "gemini"}
