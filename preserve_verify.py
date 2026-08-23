#!/usr/bin/env python3
"""Preserve status.json.verify across dashboard rebuilds.

Refresh must NEVER drop a Jeff unlock challenge that is still within
issued_at + TTL (plus a small rebuild/clock-skew grace). Unlock still
fails closed on the client when Date.now() > exp.

Repro of the iPhone Unlock wipe (2026-08-23):
  1. Bob issued a 15m OTP and pushed status.json.verify (sha + exp + issued_at).
  2. Actions cron ``Refresh Bob Ops Dashboard`` (every 15m) started near end of TTL.
  3. Rebuild took ~20s. Preserve used ``exp > now`` with no grace.
  4. By preserve-time, exp was in the past → verify omitted from the new JSON.
  5. Live Pages served verify=null → Unlock: "No code active yet. Ask Bob for one."

Root cause: 15m OTP + 15m refresh + preserve-only-if-exp>now = race wipe.
Issuer TTL is now 2h (keep that). This module is the refresh-side lock so a
rebuild that overruns exp by seconds cannot erase a still-in-window code.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

JEFF_EMAIL = "jeffstory007@gmail.com"
TTL_MS = 2 * 60 * 60 * 1000  # 2h; keep in sync with issuer scripts
GRACE_MS = 5 * 60 * 1000  # rebuild duration + clock skew; not a longer OTP
SHA_HEX_LEN = 64
_HEX = frozenset("0123456789abcdef")

LogFn = Callable[[str], None]


def _log(log: Optional[LogFn], msg: str) -> None:
    (log or print)(msg)


def normalize_verify(raw: Any) -> Optional[dict[str, Any]]:
    """Fail-closed: allowlisted email, 64-hex sha256, integer exp required."""
    if not isinstance(raw, dict):
        return None
    email = str(raw.get("email") or "").strip().lower()
    sha = str(raw.get("sha256") or "").strip().lower()
    if email != JEFF_EMAIL:
        return None
    if len(sha) != SHA_HEX_LEN or any(c not in _HEX for c in sha):
        return None
    try:
        exp = int(raw["exp"])
    except (KeyError, TypeError, ValueError):
        return None
    issued_raw = raw.get("issued_at")
    issued_at: Optional[int]
    try:
        issued_at = int(issued_raw) if issued_raw is not None else None
    except (TypeError, ValueError):
        issued_at = None
    if issued_at is not None and issued_at <= 0:
        issued_at = None
    return {
        "email": email,
        "sha256": sha,
        "exp": exp,
        "issued_at": issued_at,
    }


def should_preserve(v: dict[str, Any], now_ms: int) -> tuple[bool, str]:
    """Keep iff unexpired at the refresh clock, or still within issued_at+TTL.

    ``now_ms`` must be refresh_started_ms (start of rebuild), not preserve-time.
    That is the 20s wipe: exp can lapse during gh fetches if the clock is ``time.time()``
    at the end of the script.
    """
    exp = int(v["exp"])
    issued_at = v.get("issued_at")
    if exp > now_ms:
        return True, (
            "preserve verify: unexpired at refresh_started_ms "
            f"(exp={exp} clock={now_ms})"
        )
    if issued_at is not None:
        window_end = int(issued_at) + TTL_MS
        if now_ms < window_end:
            return True, (
                "preserve verify: issued_at+TTL still live "
                f"(issued_at={issued_at} ttl_ms={TTL_MS} now={now_ms} exp={exp})"
            )
        if now_ms < window_end + GRACE_MS:
            return True, (
                "preserve verify: issued_at+TTL+grace (rebuild/clock-skew) "
                f"(issued_at={issued_at} ttl_ms={TTL_MS} grace_ms={GRACE_MS} "
                f"now={now_ms} exp={exp})"
            )
        return False, (
            "drop verify: issued_at+TTL+grace expired "
            f"(issued_at={issued_at} ttl_ms={TTL_MS} grace_ms={GRACE_MS} "
            f"now={now_ms} exp={exp})"
        )
    if exp + GRACE_MS > now_ms:
        return True, (
            "preserve verify: no issued_at; exp+grace still live "
            f"(exp={exp} grace_ms={GRACE_MS} now={now_ms})"
        )
    return False, (
        "drop verify: no issued_at and exp+grace expired "
        f"(exp={exp} grace_ms={GRACE_MS} now={now_ms})"
    )


def apply_preserved_verify(
    status: dict[str, Any],
    prev: Any,
    *,
    now_ms: Optional[int] = None,
    refresh_started_ms: Optional[int] = None,
    log: Optional[LogFn] = None,
) -> Optional[dict[str, Any]]:
    """Copy prev.verify onto status when the fail-closed window is still open.

    Prefer refresh_started_ms as the clock so a rebuild that overruns exp
    cannot drop a challenge that was still live when refresh began.
    """
    if refresh_started_ms is not None:
        now = int(refresh_started_ms)
    elif now_ms is not None:
        now = int(now_ms)
    else:
        now = int(time.time() * 1000)
    raw = prev.get("verify") if isinstance(prev, dict) else None
    if raw is None:
        _log(log, "preserve verify: none present")
        return None
    v = normalize_verify(raw)
    if v is None:
        _log(
            log,
            "drop verify: fail-closed (need jeffstory007@gmail.com + 64-hex sha256 + exp)",
        )
        return None
    keep, reason = should_preserve(v, now)
    if refresh_started_ms is not None:
        reason = f"{reason} refresh_started_ms={int(refresh_started_ms)}"
    _log(log, reason)
    if not keep:
        return None
    out = {
        "email": v["email"],
        "sha256": v["sha256"],
        "exp": int(v["exp"]),
        "issued_at": v.get("issued_at"),
    }
    status["verify"] = out
    return out


def apply_status_file(path: str, now_ms: Optional[int] = None) -> Optional[dict[str, Any]]:
    """Read status.json, preserve verify onto a fresh object, write verify back.

    Used by qa-claim-smoke. Does not rebuild the board; only the verify field.
    """
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("status.json must be an object")
    fresh: dict[str, Any] = {k: v for k, v in data.items() if k != "verify"}
    kept = apply_preserved_verify(fresh, data, now_ms=now_ms)
    if kept is not None:
        data["verify"] = kept
    else:
        data.pop("verify", None)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return kept


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print("usage: preserve_verify.py STATUS.json [now_ms]", file=sys.stderr)
        return 2
    path = args[0]
    now_ms = int(args[1]) if len(args) > 1 else None
    kept = apply_status_file(path, now_ms=now_ms)
    print("verify=kept" if kept else "verify=dropped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
