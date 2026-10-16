"""Parse Karen gate lines from progress.md — structured fields only."""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")

_GATE_REQUEST = re.compile(
    r"KAREN_GATE_REQUEST\s+pr=(\d+)\s+sha=([0-9a-fA-F]+)",
    re.IGNORECASE,
)
_KAREN_LINE = re.compile(
    r"^-\s+#(\d+)\s+([0-9a-fA-F]{7,40})\s+(PASS|FAIL|REQUESTED)\b",
    re.IGNORECASE,
)
_REPO_IN_LINE = re.compile(r"\brepo=([^\s]+)", re.IGNORECASE)
_SECTION_HEADING = re.compile(
    r"^##\s+.*?(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})\s+CT",
    re.IGNORECASE,
)
_NOTE_TIME = re.compile(r"\bNOTE\s+(\d{1,2}):(\d{2})\s+CT\b", re.IGNORECASE)


def _display_ct(hour: int, minute: int, day: datetime | None = None) -> str:
    base = day or datetime.now(CT)
    dt = base.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=CT)
    return dt.strftime("%I:%M %p").lstrip("0")


def _heading_day(line: str) -> datetime | None:
    m = _SECTION_HEADING.match(line.strip())
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=CT)
    except ValueError:
        return None


def _mtime_display_ct(file_mtime: float | None) -> str | None:
    if file_mtime is None:
        return None
    dt = datetime.fromtimestamp(file_mtime, CT)
    return "~" + dt.strftime("%I:%M %p CT").lstrip("0")


def _resolve_at_display(
    line: str,
    section_day: datetime | None,
    last_note_display: str | None,
    section_display: str | None,
    file_mtime: float | None,
) -> str:
    at_display = (
        _parse_ct_time(line, section_day)
        or last_note_display
        or section_display
        or _mtime_display_ct(file_mtime)
    )
    return at_display or "time unknown"


def _parse_ct_time(line: str, section_day: datetime | None) -> str | None:
    hm = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})\s+CT", line, re.IGNORECASE)
    if hm:
        try:
            dt = datetime.strptime(
                f"{hm.group(1)} {hm.group(2)}:{hm.group(3)}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=CT)
            return dt.strftime("%I:%M %p").lstrip("0")
        except ValueError:
            pass
    note = _NOTE_TIME.search(line)
    if note and section_day:
        return _display_ct(int(note.group(1)), int(note.group(2)), section_day)
    iso = re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", line)
    if iso:
        try:
            raw = iso.group(0).replace(" ", "T")
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CT)
            return dt.astimezone(CT).strftime("%I:%M %p").lstrip("0")
        except ValueError:
            pass
    return None


def parse_progress_text(text: str, file_mtime: float | None = None) -> list[dict]:
    """
    Extract gate events from progress.md content.
    Only structured fields; never copies arbitrary free text from lines.
    """
    results: list[dict] = []
    in_karen = False
    section_day: datetime | None = None
    section_display: str | None = None
    last_note_display: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if "karen" in stripped.lower():
                in_karen = True
            elif in_karen:
                in_karen = False

            hm = _SECTION_HEADING.match(stripped)
            if hm:
                section_day = _heading_day(stripped) or section_day
                if section_day:
                    section_display = _display_ct(
                        int(hm.group(2)), int(hm.group(3)), section_day
                    )
                else:
                    section_display = _parse_ct_time(stripped, section_day)
            continue

        note_m = _NOTE_TIME.search(stripped)
        if note_m and section_day:
            last_note_display = _display_ct(
                int(note_m.group(1)), int(note_m.group(2)), section_day
            )

        m = _GATE_REQUEST.search(line)
        if m:
            pr, sha = m.group(1), m.group(2)
            repo_m = _REPO_IN_LINE.search(line)
            at_display = _resolve_at_display(
                line, section_day, last_note_display, section_display, file_mtime
            )
            entry = {
                "pr": int(pr),
                "sha": sha[:8].lower(),
                "verdict": "REQUESTED",
                "at_ct": at_display,
                "at_display": at_display,
                "repo": repo_m.group(1) if repo_m else None,
            }
            results.append(entry)
            continue

        if in_karen:
            km = _KAREN_LINE.match(stripped)
            if km:
                pr, sha, verdict = km.group(1), km.group(2), km.group(3).upper()
                repo_m = _REPO_IN_LINE.search(line)
                at_display = _resolve_at_display(
                    line, section_day, last_note_display, section_display, file_mtime
                )
                results.append(
                    {
                        "pr": int(pr),
                        "sha": sha[:8].lower(),
                        "verdict": verdict,
                        "at_ct": at_display,
                        "at_display": at_display,
                        "repo": repo_m.group(1) if repo_m else None,
                    }
                )
    return results


def latest_verdicts_ordered(gates: list[dict]) -> dict[tuple[int, str], str]:
    """Latest verdict per (pr, sha); file order wins."""
    out: dict[tuple[int, str], str] = {}
    for g in gates:
        key = (int(g["pr"]), str(g["sha"]).lower()[:8])
        out[key] = str(g.get("verdict", "")).upper()
    return out
