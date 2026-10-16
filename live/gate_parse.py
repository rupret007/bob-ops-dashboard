"""Parse Karen gate lines from progress.md — structured fields only."""
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


def _parse_ct_time(line: str) -> str | None:
    """Best-effort CT timestamp from line if present (ISO or common formats)."""
    iso = re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", line)
    if iso:
        try:
            raw = iso.group(0).replace(" ", "T")
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CT)
            return dt.astimezone(CT).strftime("%Y-%m-%d %H:%M %Z")
        except ValueError:
            pass
    return None


def parse_progress_text(text: str) -> list[dict]:
    """
    Extract gate events from progress.md content.
    Only structured fields; never copies arbitrary free text from lines.
    """
    results: list[dict] = []
    in_karen = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and "karen" in stripped.lower():
            in_karen = True
            continue
        if stripped.startswith("## ") and in_karen:
            in_karen = False

        m = _GATE_REQUEST.search(line)
        if m:
            pr, sha = m.group(1), m.group(2)
            repo_m = _REPO_IN_LINE.search(line)
            entry = {
                "pr": int(pr),
                "sha": sha[:8].lower(),
                "verdict": "REQUESTED",
                "at_ct": _parse_ct_time(line),
                "repo": repo_m.group(1) if repo_m else None,
            }
            results.append(entry)
            continue

        if in_karen:
            km = _KAREN_LINE.match(stripped)
            if km:
                pr, sha, verdict = km.group(1), km.group(2), km.group(3).upper()
                repo_m = _REPO_IN_LINE.search(line)
                results.append(
                    {
                        "pr": int(pr),
                        "sha": sha[:8].lower(),
                        "verdict": verdict,
                        "at_ct": _parse_ct_time(line),
                        "repo": repo_m.group(1) if repo_m else None,
                    }
                )
    return results
