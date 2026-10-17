#!/usr/bin/env python3
"""Aggregate usage + machine metrics for the Usage & machines dashboard panel.

Reads only public-safe local sources (Codex session jsonl, lane spend logs).
Never reads auth.json, chat.db, or credential files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

USAGE_V = "usage-machines/v1"
BOARD_TZ = ZoneInfo("America/Chicago")
HOST_LABELS = frozenset({"mini", "nucbox"})
OPTIONAL_SPEND_PROVIDERS = ("cursor", "claude", "minimax")

_PATH_RE = re.compile(
    r"(?i)(?:/users/|/home/|~[/\\]|file://|\\\\|"
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b|"
    r"\b[a-z0-9-]+\.(?:local|lan|tailnet\d*\.ts\.net)\b)"
)
_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|bearer|token|secret|password)\s*[=:]\s*\S+"
)

_GEMINI_SPEND_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}(?::\d{2})?)"
    r"(?:\s+(?P<rest>.*))?$"
)
_USD_RE = re.compile(r"\$?\s*(?P<usd>\d+(?:\.\d+)?)\s*$")
_INT_FIELDS = re.compile(r"\b(?:in|out|tokens?)=(?P<n>\d+)\b", re.I)


def _optional_token_int(raw: dict[str, Any], key: str) -> int | None:
    if key not in raw:
        return None
    val = raw.get(key)
    if val is None or isinstance(val, bool):
        return None
    try:
        n = int(val)
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None


def _sum_optional_tokens(a: int | None, b: int | None) -> int | None:
    if a is None or b is None:
        return None
    return a + b


@dataclass(frozen=True)
class TokenTotals:
    total_tokens: int | None = None
    cached_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None

    def to_dict(self) -> dict[str, int | None]:
        return {
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
        }

    def __add__(self, other: TokenTotals) -> TokenTotals:
        return TokenTotals(
            total_tokens=_sum_optional_tokens(self.total_tokens, other.total_tokens),
            cached_tokens=_sum_optional_tokens(self.cached_tokens, other.cached_tokens),
            output_tokens=_sum_optional_tokens(self.output_tokens, other.output_tokens),
            reasoning_tokens=_sum_optional_tokens(
                self.reasoning_tokens, other.reasoning_tokens
            ),
        )


def detect_host_label() -> str:
    raw = str(os.environ.get("USAGE_MACHINE_LABEL") or "").strip().lower()
    if raw in HOST_LABELS:
        return raw
    if sys.platform == "darwin":
        return "mini"
    return "nucbox"


def _parse_day_key(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return ""


def _day_from_timestamp(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        if text.endswith("Z"):
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        elif "T" in text:
            dt = datetime.fromisoformat(text)
        else:
            dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BOARD_TZ)
        return dt.astimezone(BOARD_TZ).date().isoformat()
    except ValueError:
        return _parse_day_key(text)


def last_codex_token_usage(session_path: Path) -> TokenTotals | None:
    if session_path.suffix != ".jsonl" or session_path.is_symlink() or "auth" in session_path.name.lower():
        return None
    last: dict[str, Any] | None = None
    try:
        with session_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict) or row.get("type") != "event_msg":
                    continue
                payload = row.get("payload") or {}
                if not isinstance(payload, dict) or payload.get("type") != "token_count":
                    continue
                info = payload.get("info") or {}
                usage = info.get("total_token_usage") or {} if isinstance(info, dict) else {}
                last = usage if isinstance(usage, dict) else {}
    except OSError:
        return None
    if last is None:
        return None
    return TokenTotals(
        total_tokens=_optional_token_int(last, "total_tokens"),
        cached_tokens=_optional_token_int(last, "cached_input_tokens"),
        output_tokens=_optional_token_int(last, "output_tokens"),
        reasoning_tokens=_optional_token_int(last, "reasoning_output_tokens"),
    )


def collect_codex_for_day(sessions_root: Path, day: str) -> TokenTotals:
    y, m, d = day.split("-")
    day_dir = sessions_root / y / m / d
    if not day_dir.is_dir():
        return TokenTotals()
    total = None
    for fp in sorted(day_dir.glob("*.jsonl")):
        if fp.name.startswith(".") or "auth" in fp.name.lower():
            continue
        row = last_codex_token_usage(fp)
        if row is None:
            row = TokenTotals()
        if row:
            # No observations is distinct from an observation with unknown fields.
            total = row if total is None else total + row
    return total if total is not None else TokenTotals()


def collect_codex_by_day(
    sessions_root: Path, days: list[str] | None = None
) -> dict[str, TokenTotals]:
    out: dict[str, TokenTotals] = {}
    if days:
        for day in days:
            out[day] = collect_codex_for_day(sessions_root, day)
        return out
    if not sessions_root.is_dir():
        return out
    for year_dir in sorted(sessions_root.iterdir()):
        if not year_dir.is_dir():
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            for day_dir in sorted(month_dir.iterdir()):
                if not day_dir.is_dir():
                    continue
                day = f"{year_dir.name}-{month_dir.name}-{day_dir.name}"
                out[day] = collect_codex_for_day(sessions_root, day)
    return out


def _parse_gemini_numbers(rest: str) -> tuple[int | None, float | None]:
    cost = re.search(r"\$(\d+(?:\.\d+)?)\s*$", rest)
    usd = float(cost[1]) if cost else None
    labels = dict(re.findall(r"\b(in|out|tokens)=(\d+)\b", rest))
    tokens = int(labels['tokens']) if 'tokens' in labels else (
        int(labels['in']) + int(labels['out']) if 'in' in labels and 'out' in labels else None)
    positional = re.fullmatch(r"\S+\s+\S+\s+(\d+)\s+(\d+)\s+\$\d+(?:\.\d+)?", rest)
    if positional and not labels:
        tokens = int(positional[1]) + int(positional[2])
    return tokens, usd


def parse_gemini_spend_line(line: str) -> tuple[str, int, float] | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    m = _GEMINI_SPEND_RE.match(text)
    if not m:
        return None
    day = _day_from_timestamp(m.group("ts"))
    if not day:
        return None
    rest = m.group("rest") or ""
    tokens, usd = _parse_gemini_numbers(rest)
    return day, tokens, usd


def aggregate_gemini_spend(path: Path) -> dict[str, dict[str, float | int]]:
    out: dict[str, dict[str, float | int]] = {}
    if not path.is_file():
        return out
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        parsed = parse_gemini_spend_line(line)
        if not parsed:
            continue
        day, tokens, usd = parsed
        bucket = out.setdefault(day, {"tokens": 0, "usd": 0.0})
        bucket["tokens"] = _sum_optional_tokens(bucket["tokens"], tokens)
        bucket["usd"] = round(bucket["usd"] + usd, 4) if bucket["usd"] is not None and usd is not None else None
    return out


def aggregate_optional_spend(path: Path) -> dict[str, dict[str, float | int]]:
    """Same line shape as gemini-spend.txt when a provider log exists."""
    return aggregate_gemini_spend(path)


def parse_provider_usage_line(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    parts = text.split("\t")
    if len(parts) < 4:
        return None
    day = _day_from_timestamp(parts[0])
    provider = str(parts[2] or "").strip().lower()
    if not day or not provider:
        return None
    return day, provider


def aggregate_provider_usage(path: Path) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    if not path.is_file():
        return out
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        parsed = parse_provider_usage_line(line)
        if not parsed:
            continue
        day, provider = parsed
        bucket = out.setdefault(day, {})
        bucket[provider] = int(bucket.get(provider, 0)) + 1
    return out


def _read_mem_linux() -> tuple[float | None, float | None]:
    mem_total_kb = mem_avail_kb = swap_total_kb = swap_free_kb = 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_avail_kb = int(line.split()[1])
                elif line.startswith("SwapTotal:"):
                    swap_total_kb = int(line.split()[1])
                elif line.startswith("SwapFree:"):
                    swap_free_kb = int(line.split()[1])
    except OSError:
        return None, None
    if mem_total_kb <= 0:
        return None, None
    divisor = 1024**2
    swap_used_gb = max(0.0, (swap_total_kb - swap_free_kb) / divisor)
    mem_used_gb = max(0.0, (mem_total_kb - mem_avail_kb) / divisor)
    return round(mem_used_gb, 3), round(swap_used_gb, 3)


def collect_machine_snapshot() -> dict[str, float | None]:
    load1, load5, _ = os.getloadavg()
    disk = shutil.disk_usage(os.path.expanduser("~"))
    mem_used_gb, swap_used_gb = _read_mem_linux()
    return {
        "load1": round(load1, 3),
        "load5": round(load5, 3),
        "mem_used_gb": mem_used_gb,
        "swap_used_gb": swap_used_gb,
        "disk_free_gb": round(disk.free / (1024**3), 3),
    }


GhSearchFn = Callable[[str, str], int | None]


def _default_gh_merged_count(owner: str, day: str) -> int:
    gh = os.environ.get("GH_PATH") or os.path.expanduser("~/.local/bin/gh")
    if not Path(gh).is_file():
        gh = "gh"
    query = f"org:{owner} is:pr is:merged merged:{day}"
    try:
        out = subprocess.check_output(
            [gh, "api", "search/issues", "-f", f"q={query}", "-f", "per_page=1"],
            text=True,
            timeout=60,
        )
        data = json.loads(out)
        return int(data.get("total_count") or 0)
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError, OSError):
        return None


def tokens_for_merged_prs(
    merged_count: int | None,
    provider_usage: dict[str, dict[str, int]],
    gemini_day: dict[str, float | int] | None,
    codex_day: TokenTotals | None,
) -> dict[str, int | float | None]:
    if merged_count is None:
        return {"count": None, "tokens_total": None, "tokens_per_pr": None}
    if merged_count <= 0:
        return {"count": 0, "tokens_total": None, "tokens_per_pr": None}
    tokens_total = 0
    if gemini_day:
        if gemini_day.get("tokens") is None:
            return {"count": merged_count, "tokens_total": None, "tokens_per_pr": None}
        tokens_total += int(gemini_day["tokens"])
    if codex_day:
        if codex_day.total_tokens is None:
            return {"count": merged_count, "tokens_total": None, "tokens_per_pr": None}
        tokens_total += codex_day.total_tokens
    for _prov, n in (provider_usage or {}).items():
        tokens_total += int(n) * 0  # lane rows are counts only; tokens from spend/codex
    if tokens_total <= 0:
        return {
            "count": merged_count,
            "tokens_total": None,
            "tokens_per_pr": None,
        }
    return {
        "count": merged_count,
        "tokens_total": tokens_total,
        "tokens_per_pr": round(tokens_total / merged_count),
    }


def collect_host_snapshot(
    *,
    host: str,
    sessions_root: Path,
    lane_logs: Path,
    days: list[str] | None = None,
    owner: str = "rupret007",
    gh_search: GhSearchFn | None = None,
    collect_merged: bool = True,
) -> dict[str, Any]:
    if host not in HOST_LABELS:
        raise ValueError(f"unknown host label {host!r}")
    gh_search = gh_search or _default_gh_merged_count
    codex = collect_codex_by_day(sessions_root, days=days)
    gemini = aggregate_gemini_spend(lane_logs / "gemini-spend.txt")
    lanes = aggregate_provider_usage(lane_logs / "provider-usage.tsv")
    optional: dict[str, dict[str, dict[str, float | int]]] = {}
    for prov in OPTIONAL_SPEND_PROVIDERS:
        agg = aggregate_optional_spend(lane_logs / f"{prov}-spend.txt")
        if agg:
            optional[prov] = agg
    machine = collect_machine_snapshot()
    day_keys = set(codex.keys()) | set(gemini.keys()) | set(lanes.keys())
    for prov_map in optional.values():
        day_keys |= set(prov_map.keys())
    if days:
        day_keys |= set(days)
    latest_day = max(day_keys) if day_keys else None
    day_rows: list[dict[str, Any]] = []
    for day in sorted(day_keys):
        codex_totals = codex.get(day, TokenTotals())
        gemini_day = gemini.get(day)
        lane_day = lanes.get(day, {})
        merged_count = gh_search(owner, day) if collect_merged else None
        merged = tokens_for_merged_prs(
            merged_count,
            lane_day,
            gemini_day,
            codex_totals,
        )
        row: dict[str, Any] = {
            "date": day,
            "codex": codex_totals.to_dict(),
            "gemini": gemini_day,
            # No direct source exists; lanes never consume this tracked budget.
            "grok_bot": {"status": "unknown", "source": None},
            "lanes": lane_day or None,
            "machines": {host: machine} if day == latest_day else None,
            "merged_prs": merged,
        }
        for prov in OPTIONAL_SPEND_PROVIDERS:
            prov_days = optional.get(prov) or {}
            row[prov] = prov_days.get(day)
        day_rows.append(row)
    stamp = datetime.now(tz=BOARD_TZ).strftime("%Y-%m-%d %H:%M CT")
    return sanitize_usage_payload(
        {
            "v": USAGE_V,
            "host": host,
            "generated_at": stamp,
            "timezone": "America/Chicago",
            "days": day_rows,
        }
    )


def _merge_codex(
    a: dict[str, int] | None, b: dict[str, int] | None
) -> dict[str, int] | None:
    if not a and not b:
        return None
    out = {k: 0 for k in TokenTotals().to_dict()}
    for src in (a, b):
        if not src:
            continue
        for k in out:
            out[k] += int(src.get(k) or 0)
    return out if any(out.values()) else None


def _merge_gemini(
    a: dict[str, float | int] | None, b: dict[str, float | int] | None
) -> dict[str, float | int] | None:
    if not a and not b:
        return None
    observed = [x for x in (a, b) if x is not None]
    return {k: (sum(x[k] for x in observed) if all(x.get(k) is not None for x in observed) else None)
            for k in ('tokens', 'usd')}


def _merge_lanes(
    a: dict[str, int] | None, b: dict[str, int] | None
) -> dict[str, int] | None:
    if not a and not b:
        return None
    out: dict[str, int] = {}
    for src in (a, b):
        if not src:
            continue
        for k, v in src.items():
            out[k] = int(out.get(k, 0)) + int(v)
    return out or None


def _merge_machines(
    a: dict[str, Any] | None, b: dict[str, Any] | None
) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    for src in (a, b):
        if not isinstance(src, dict):
            continue
        for host, metrics in src.items():
            if host in HOST_LABELS and isinstance(metrics, dict):
                out[host] = dict(metrics)
    return out or None


def merge_host_snapshots(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge per-host collector output into one public aggregate."""
    by_date: dict[str, dict[str, Any]] = {}
    hosts: list[str] = []
    for snap in snapshots:
        if not isinstance(snap, dict):
            continue
        host = str(snap.get("host") or "").strip().lower()
        if host in HOST_LABELS:
            hosts.append(host)
        for day_row in snap.get("days") or []:
            if not isinstance(day_row, dict):
                continue
            day = str(day_row.get("date") or "")
            if not day:
                continue
            bucket = by_date.setdefault(
                day,
                {
                    "date": day,
                    "codex_by_host": {},
                    "gemini": None,
                    "lanes": None,
                    "cursor": None,
                    "claude": None,
                    "minimax": None,
                    "machines": None,
                    "merged_prs": None,
                },
            )
            codex = day_row.get("codex")
            if isinstance(codex, dict) and host in HOST_LABELS:
                bucket["codex_by_host"][host] = dict(codex)
            bucket["gemini"] = _merge_gemini(
                bucket.get("gemini"), day_row.get("gemini")
            )
            bucket["lanes"] = _merge_lanes(bucket.get("lanes"), day_row.get("lanes"))
            for prov in OPTIONAL_SPEND_PROVIDERS:
                cur = bucket.get(prov)
                new = day_row.get(prov)
                if isinstance(new, dict):
                    bucket[prov] = _merge_gemini(cur, new)
            bucket["machines"] = _merge_machines(
                bucket.get("machines"), day_row.get("machines")
            )
            merged = day_row.get("merged_prs")
            if isinstance(merged, dict) and merged.get("count"):
                prev = bucket.get("merged_prs") or {}
                if int(merged.get("count") or 0) >= int(prev.get("count") or 0):
                    bucket["merged_prs"] = dict(merged)
    days_out: list[dict[str, Any]] = []
    for day in sorted(by_date):
        raw = by_date[day]
        codex_hosts = raw.get("codex_by_host") or {}
        codex_combined = None
        if codex_hosts:
            combined = None
            for metrics in codex_hosts.values():
                if isinstance(metrics, dict):
                    observed = TokenTotals(
                        total_tokens=_optional_token_int(metrics, "total_tokens"),
                        cached_tokens=_optional_token_int(metrics, "cached_tokens"),
                        output_tokens=_optional_token_int(metrics, "output_tokens"),
                        reasoning_tokens=_optional_token_int(metrics, "reasoning_tokens"),
                    )
                    combined = observed if combined is None else combined + observed
            codex_combined = combined.to_dict() if combined else None
        days_out.append(
            {
                "date": day,
                "codex": codex_combined,
                "codex_by_host": codex_hosts or None,
                "gemini": raw.get("gemini"),
                "grok_bot": {"status": "unknown", "source": None},
                "lanes": raw.get("lanes"),
                "cursor": raw.get("cursor"),
                "claude": raw.get("claude"),
                "minimax": raw.get("minimax"),
                "machines": raw.get("machines"),
                "merged_prs": raw.get("merged_prs"),
            }
        )
    if len(days_out) > 1:
        latest = days_out[-1]["date"]
        for row in days_out:
            if row.get("date") != latest:
                row["machines"] = None
    stamp = datetime.now(tz=BOARD_TZ).strftime("%Y-%m-%d %H:%M CT")
    return sanitize_usage_payload(
        {
            "v": USAGE_V,
            "generated_at": stamp,
            "timezone": "America/Chicago",
            "hosts": sorted(set(hosts)),
            "days": days_out,
        }
    )


def _scrub_string(value: str) -> str:
    out = _SECRET_RE.sub("[REDACTED]", value)
    out = _PATH_RE.sub("[host]", out)
    return out


def sanitize_usage_payload(data: Any) -> Any:
    if isinstance(data, dict):
        return {str(k): sanitize_usage_payload(v) for k, v in data.items()}
    if isinstance(data, list):
        return [sanitize_usage_payload(v) for v in data]
    if isinstance(data, str):
        return _scrub_string(data)
    return data


def load_host_partials(root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for label in HOST_LABELS:
        path = root / f"usage-host-{label}.json"
        if not path.is_file():
            continue
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(blob, dict):
            out.append(blob)
    return out


def _load_merged_usage(root: Path) -> dict[str, Any]:
    partials = load_host_partials(root)
    base_path = root / "usage-machines.json"
    base: dict[str, Any] = {}
    if base_path.is_file():
        try:
            raw = json.loads(base_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                base = raw
        except (OSError, json.JSONDecodeError):
            base = {}
    if partials:
        merged = merge_host_snapshots(partials)
        if base.get("days") and not merged.get("days"):
            return sanitize_usage_payload(base)
        return merged
    return sanitize_usage_payload(base) if base else {}


def default_paths(home: Path | None = None) -> tuple[Path, Path]:
    home = home or Path.home()
    sessions = home / ".codex" / "sessions"
    lane_logs = home / "lane-wt" / "logs"
    return sessions, lane_logs


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Collect usage & machine metrics.")
    parser.add_argument(
        "--host",
        default=detect_host_label(),
        choices=sorted(HOST_LABELS),
    )
    parser.add_argument("--out", type=Path, help="Write host partial JSON")
    parser.add_argument("--merge-dir", type=Path, help="Merge usage-host-*.json to stdout")
    parser.add_argument("--days", nargs="*", help="YYYY-MM-DD days to include")
    parser.add_argument("--owner", default="rupret007")
    parser.add_argument("--no-gh", action="store_true", help="Skip merged PR gh search")
    args = parser.parse_args(argv)
    if args.merge_dir:
        merged = merge_host_snapshots(load_host_partials(args.merge_dir))
        print(json.dumps(merged, indent=2))
        return 0
    sessions, lane_logs = default_paths()
    snap = collect_host_snapshot(
        host=args.host,
        sessions_root=sessions,
        lane_logs=lane_logs,
        days=args.days,
        owner=args.owner,
        collect_merged=not args.no_gh,
    )
    text = json.dumps(snap, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(text)
    return 0



def load_merged_usage(root: Path) -> dict[str, Any]:
    from scorecards import normalize_scorecards
    data = _load_merged_usage(root)
    path = root / "scorecards.json"
    if path.is_file() and not path.is_symlink():
        try:
            data["scorecards"] = normalize_scorecards(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, AttributeError):
            data["scorecards"] = normalize_scorecards(None)
    return data


if __name__ == "__main__":
    raise SystemExit(main())
