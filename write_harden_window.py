#!/usr/bin/env python3
"""Tiny writer for harden-window.json (stress / Bob runners).

Keeps llm_work chips honest: this file drives the separate Harden strip only.
Never set llm-work-now.json status=running for one-shot harden lane fires.

Examples:
  ./write_harden_window.py open --round 8 --lanes Codex,Claude  # fresh started_at; lanes only from args
  ./write_harden_window.py fire --lanes LocalCursor,Codex
  ./write_harden_window.py score --line 'Stress FAIL | Eff FAIL | CQ PASS'
  ./write_harden_window.py close --round 7 --line 'Stress PASS | Eff PASS'
  ./write_harden_window.py idle
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from board_meta import (  # noqa: E402
    BOARD_TZ,
    HARDEN_WINDOW_LANE_IDS,
    build_harden_window_payload,
    canonicalize_harden_lane,
    closed_harden_window,
    normalize_harden_window,
)

DEFAULT_PATHS = (
    ROOT / "harden-window.json",
    Path("/home/box/conductor/llm-work-now/harden-window.json"),
)


def _now() -> str:
    return datetime.now(tz=ZoneInfo(BOARD_TZ)).isoformat(timespec="seconds")


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        return blob if isinstance(blob, dict) else {}
    except Exception:
        return {}


def _lanes(raw: str | None, prior: list | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in list(prior or []) + [
        x.strip() for x in (raw or "").replace("|", ",").split(",") if x.strip()
    ]:
        canon = canonicalize_harden_lane(item)
        if canon and canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Store writer shape (no display_status); refresh normalizes fail-closed.
    store = {
        "round": payload.get("round"),
        "status": payload.get("status") or "closed",
        "started_at": payload.get("started_at") or "",
        "ended_at": payload.get("ended_at") or "",
        "lanes_fired": payload.get("lanes_fired") or [],
        "scorecard_line": payload.get("scorecard_line") or "",
        "updated_at": payload.get("updated_at") or _now(),
    }
    path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    print(json.dumps(normalize_harden_window(store), indent=2))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "action",
        choices=("open", "fire", "score", "close", "idle", "show"),
        help="open=start round; fire=add lanes; score=set one-liner; close=end; idle=clear",
    )
    ap.add_argument("--round", "-r", default=None, help="Round number (e.g. 7 or R7)")
    ap.add_argument(
        "--lanes",
        "-l",
        default="",
        help=f"Comma lanes: {','.join(HARDEN_WINDOW_LANE_IDS)}",
    )
    ap.add_argument("--line", default="", help="Scorecard one-liner (Stress/Eff verdict)")
    ap.add_argument(
        "--path",
        default="",
        help="Primary harden-window.json (also mirrors to conductor when default)",
    )
    ap.add_argument(
        "--started-at",
        default="",
        help="Optional ISO start stamp (defaults to now on open)",
    )
    args = ap.parse_args(argv)

    primary = Path(args.path) if args.path else DEFAULT_PATHS[0]
    prior = _read(primary)
    if not prior and primary != DEFAULT_PATHS[1]:
        prior = _read(DEFAULT_PATHS[1])

    if args.action == "show":
        print(json.dumps(normalize_harden_window(prior or None), indent=2))
        return 0

    if args.action == "idle":
        payload = build_harden_window_payload(
            round=None,
            status="closed",
            started_at="",
            ended_at=_now(),
            lanes_fired=[],
            scorecard_line="",
            updated_at=_now(),
        )
    elif args.action == "open":
        # New round: always fresh started_at (unless --started-at) and ONLY the
        # lanes passed on this open — do not inherit prior round clock/lanes.
        # (R8 measured hole: open preserved R7 started_at + merged lanes.)
        started = args.started_at or _now()
        payload = build_harden_window_payload(
            round=args.round if args.round is not None else prior.get("round"),
            status="open",
            started_at=started,
            ended_at="",
            lanes_fired=_lanes(args.lanes),
            scorecard_line="",
            updated_at=_now(),
        )
        if not payload.get("round"):
            print("error: --round required for open", file=sys.stderr)
            return 2
    elif args.action == "fire":
        if not args.lanes:
            print("error: --lanes required for fire", file=sys.stderr)
            return 2
        payload = build_harden_window_payload(
            round=args.round if args.round is not None else prior.get("round"),
            status="open",
            started_at=prior.get("started_at") or _now(),
            ended_at="",
            lanes_fired=_lanes(args.lanes, prior.get("lanes_fired")),
            scorecard_line=prior.get("scorecard_line") or "",
            updated_at=_now(),
        )
    elif args.action == "score":
        if not args.line:
            print("error: --line required for score", file=sys.stderr)
            return 2
        payload = build_harden_window_payload(
            round=args.round if args.round is not None else prior.get("round"),
            status=prior.get("status") or "open",
            started_at=prior.get("started_at") or "",
            ended_at=prior.get("ended_at") or "",
            lanes_fired=prior.get("lanes_fired") or _lanes(args.lanes),
            scorecard_line=args.line,
            updated_at=_now(),
        )
    else:  # close
        payload = build_harden_window_payload(
            round=args.round if args.round is not None else prior.get("round"),
            status="closed",
            started_at=prior.get("started_at") or "",
            ended_at=_now(),
            lanes_fired=_lanes(args.lanes, prior.get("lanes_fired")),
            scorecard_line=args.line or prior.get("scorecard_line") or "",
            updated_at=_now(),
        )

    targets = [primary]
    if not args.path:
        targets.append(DEFAULT_PATHS[1])
    for t in targets:
        _write(t, payload)
    # Touch closed_harden_window so import stays used under lint.
    _ = closed_harden_window
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
