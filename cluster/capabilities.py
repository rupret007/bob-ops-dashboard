"""Capability catalog, local LLM CLI detection and the caps/v1 heartbeat.

Detection never reads credential files or prints CLI output: it only checks
whether a binary exists, whether a status command exits 0 with a "logged in"
marker, and whether a credential *file/env var exists*. Values never leave
this process.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from cluster.sanitize import sanitize_tail

CAPS_VERSION = "caps/v1"

# capability -> metadata. "provider" ties llm.* caps to the CLI/API that backs
# them; "member_only" caps are never granted to guests (subscriptions and keys
# are only used on their owner's own machines).
CAPABILITIES: dict[str, dict[str, Any]] = {
    "llm.codex.high": {"provider": "codex", "tier": "high", "member_only": True},
    "llm.codex": {"provider": "codex", "tier": "standard", "member_only": True},
    "llm.claude": {"provider": "claude", "tier": "standard", "member_only": True},
    "llm.composer": {"provider": "cursor", "tier": "cheap", "member_only": True},
    "llm.gemini": {"provider": "gemini", "tier": "cheap", "member_only": True},
    "llm.minimax": {"provider": "minimax", "tier": "cheap", "member_only": True},
    "shell.build": {"provider": None, "tier": "compute", "member_only": True},
    "shell.test": {"provider": None, "tier": "compute", "member_only": True},
    "sandbox.test": {"provider": None, "tier": "compute", "member_only": False},
}
GUEST_CAPS = frozenset(c for c, m in CAPABILITIES.items() if not m["member_only"])
CAP_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*){1,3}")
NODE_NAME_PATTERN = re.compile(r"[a-z][a-z0-9-]{1,30}")


class CapError(ValueError):
    pass


def cap_token(cap: str) -> str:
    """llm.codex.high -> llm_codex_high (one subject token, no overlap)."""
    if not isinstance(cap, str) or cap not in CAPABILITIES:
        raise CapError(f"unknown capability {cap!r}")
    return cap.replace(".", "_")


def cap_subject(cap: str, job_id: str) -> str:
    return f"cap.{cap_token(cap)}.{job_id}"


def node_token(node: str) -> str:
    if not isinstance(node, str) or not NODE_NAME_PATTERN.fullmatch(node):
        raise CapError("invalid node")
    return node


def cap_consumer(cap: str, node: str | None = None) -> str:
    return f"cap_{cap_token(cap)}" + (f"_n_{node_token(node)}" if node is not None else "")


def guest_consumer(node: str) -> str:
    return f"guests_n_{node_token(node)}"


def cap_filter(cap: str, node: str | None = None) -> str:
    return f"cap.{cap_token(cap)}." + (f"n.{node_token(node)}.*" if node is not None else "*")


def allowed_caps_for_role(role: str) -> frozenset[str]:
    if role == "member":
        # Sandbox work runs under a separate GUEST identity, never a member's.
        return frozenset(CAPABILITIES) - GUEST_CAPS
    if role == "guest":
        return GUEST_CAPS
    return frozenset()


def validate_grant(role: str, caps: Iterable[str]) -> list[str]:
    out = []
    allowed = allowed_caps_for_role(role)
    for c in caps:
        if c not in CAPABILITIES:
            raise CapError(f"unknown capability {c!r}")
        if c not in allowed:
            raise CapError(f"capability {c} is not allowed for role {role}")
        if c not in out:
            out.append(c)
    return sorted(out)


# ---------------------------------------------------------------- detection

Runner = Callable[[list[str]], tuple[int, str]]
Which = Callable[[str], "str | None"]


def _default_run(argv: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=8,
                           stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def _extra_path() -> list[str]:
    home = Path.home()
    return [str(home / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin",
            str(home / ".npm-global/bin"), str(home / ".bun/bin")]


def _default_which(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for d in _extra_path():
        cand = Path(d) / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


@dataclass
class ProviderStatus:
    installed: bool = False
    logged_in: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {"installed": self.installed, "logged_in": self.logged_in}


def _status_marker(rc: int, out: str, pattern: str) -> bool:
    return rc == 0 and re.search(pattern, out or "", re.I) is not None


def detect_providers(
    which: Which = _default_which,
    run: Runner = _default_run,
    env: dict[str, str] | None = None,
    home: Path | None = None,
) -> dict[str, ProviderStatus]:
    """Return provider -> installed/logged_in. Output text is never kept."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    st: dict[str, ProviderStatus] = {}

    codex = which("codex")
    s = ProviderStatus(installed=bool(codex))
    if codex:
        rc, out = run([codex, "login", "status"])
        s.logged_in = _status_marker(rc, out, r"logged in") and not re.search(r"not logged in", out or "", re.I)
    st["codex"] = s

    cursor = which("cursor-agent")
    s = ProviderStatus(installed=bool(cursor))
    if cursor:
        rc, out = run([cursor, "status"])
        s.logged_in = _status_marker(rc, out, r"logged in") and not re.search(r"not logged in", out or "", re.I)
    st["cursor"] = s

    claude = which("claude")
    s = ProviderStatus(installed=bool(claude))
    if claude:
        rc, out = run([claude, "auth", "status"])
        s.logged_in = _status_marker(rc, out, r'"loggedIn"\s*:\s*true')
    st["claude"] = s

    gemini = which("gemini")
    s = ProviderStatus(installed=bool(gemini))
    if gemini:
        # Presence checks only; the file is never opened.
        s.logged_in = (home / ".gemini" / "oauth_creds.json").is_file() or bool(env.get("GEMINI_API_KEY"))
    st["gemini"] = s

    # MiniMax is BYOK: "installed" and "logged in" both mean the key is
    # configured in this machine's own environment. The value is never read
    # beyond a truthiness check.
    has_key = bool(env.get("MINIMAX_API_KEY"))
    st["minimax"] = ProviderStatus(installed=has_key, logged_in=has_key)
    return st


def detected_caps(providers: dict[str, ProviderStatus],
                  which: Which = _default_which) -> set[str]:
    caps: set[str] = set()
    for cap, meta in CAPABILITIES.items():
        prov = meta["provider"]
        if prov is None:
            continue
        p = providers.get(prov)
        if p and p.installed and p.logged_in:
            caps.add(cap)
    if which("git") and (which("npm") or which("python3")):
        caps.update({"shell.build", "shell.test"})
    if sandbox_runtime(which):
        caps.add("sandbox.test")
    return caps


def sandbox_runtime(which: Which = _default_which) -> str | None:
    for rt in ("podman",):
        if which(rt):
            return rt
    return None


def effective_caps(role: str, granted: Iterable[str], detected: Iterable[str]) -> list[str]:
    """What this node will actually pull: granted by the hub AND present locally."""
    allowed = allowed_caps_for_role(role)
    g = set(granted) & allowed
    return sorted(g & set(detected))


# ------------------------------------------------------------------ quota

_QUOTA_FIELDS = ("remaining_pct", "used_pct", "reset_ct", "window")


def load_quota_hints(path: Path) -> dict[str, dict[str, Any]]:
    """Optional operator-maintained hints, e.g. {"codex": {"remaining_pct": 40}}."""
    import json

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for prov, info in raw.items():
        if prov not in {"codex", "cursor", "claude", "gemini", "minimax"} or not isinstance(info, dict):
            continue
        row: dict[str, Any] = {}
        for k in _QUOTA_FIELDS:
            v = info.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and k.endswith("_pct"):
                row[k] = max(0.0, min(100.0, float(v)))
            elif isinstance(v, str) and not k.endswith("_pct"):
                row[k] = sanitize_tail(v, max_bytes=40)
        out[prov] = row
    return out


# --------------------------------------------------------------- heartbeat

def _mem_total_gb() -> float:
    try:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 2)
    except (ValueError, OSError):
        pass
    return 0.0


def _load1() -> float:
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:
        return 0.0


def build_heartbeat(
    node: str,
    role: str,
    providers: dict[str, ProviderStatus],
    caps: list[str],
    max_lanes: int,
    active: int,
    quota: dict[str, dict[str, Any]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    if not NODE_NAME_PATTERN.fullmatch(node):
        raise CapError("bad node name")
    llm = {}
    for prov, s in providers.items():
        row: dict[str, Any] = s.as_dict()
        if quota and prov in quota:
            row["quota"] = quota[prov]
        llm[prov] = row
    if role == "guest":
        # Guests never advertise LLM logins into Jeff's cluster.
        llm = {}
        caps = [c for c in caps if c in GUEST_CAPS]
    return {
        "v": CAPS_VERSION,
        "node": node,
        "role": role,
        "ts": time.time() if now is None else now,
        "os": platform.system().lower(),
        "arch": platform.machine(),
        "cpu_count": os.cpu_count() or 0,
        "mem_total_gb": _mem_total_gb(),
        "load1": _load1(),
        "max_lanes": int(max_lanes),
        "active": int(active),
        "llm": llm,
        "caps": sorted(caps),
    }


def heartbeat_subject(node: str, role: str) -> str:
    return f"sandbox.caps.{node}" if role == "guest" else f"caps.{node}"
