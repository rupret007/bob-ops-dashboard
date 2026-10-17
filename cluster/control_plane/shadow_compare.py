#!/usr/bin/env python3
"""Daily comparison script for shadow mode decisions.

Reads the JSONL shadow logs and produces a summary comparing:
- What the shadow runner WOULD have done
- Patterns over time (dispatch frequency, gate pass/fail rates)
- Anomalies or unexpected decisions

Run daily to validate shadow mode before cutover.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SHADOW_LOG_DIR = Path(
    os.environ.get("STORYPOOL_SHADOW_LOG_DIR", "~/.local/share/storypool/shadow")
).expanduser()


@dataclass
class DailySummary:
    """Summary of one day's shadow decisions."""

    date: str
    total_runs: int
    dispatch_would: int
    dispatch_skip: int
    gate_pass: int
    gate_fail: int
    gate_needs_llm: int
    skip_reasons: Counter
    fail_reasons: Counter
    repos_touched: set
    machines: set


def parse_log_file(path: Path) -> list[dict]:
    """Parse a JSONL shadow log file."""
    decisions = []
    if not path.is_file():
        return decisions
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                decisions.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return decisions


def summarize_day(decisions: list[dict]) -> DailySummary:
    """Summarize a day's worth of decisions."""
    dispatch_would = 0
    dispatch_skip = 0
    gate_pass = 0
    gate_fail = 0
    gate_needs_llm = 0
    skip_reasons: Counter = Counter()
    fail_reasons: Counter = Counter()
    repos: set = set()
    machines: set = set()
    runs: set = set()

    for d in decisions:
        ts = d.get("timestamp", "")
        if ts:
            runs.add(ts[:16])
        machine = d.get("machine", "unknown")
        machines.add(machine)

        dtype = d.get("decision_type")
        action = d.get("action")
        reason = d.get("reason", "")
        repo = d.get("repo")

        if repo:
            repos.add(repo)

        if dtype == "dispatch":
            if action == "would_dispatch":
                dispatch_would += 1
            elif action == "would_skip":
                dispatch_skip += 1
                skip_reasons[reason[:60]] += 1

        elif dtype == "gate":
            if action == "would_pass":
                gate_pass += 1
            elif action == "would_fail":
                gate_fail += 1
                fail_reasons[reason[:60]] += 1
            elif action == "would_need_llm":
                gate_needs_llm += 1

    return DailySummary(
        date="",
        total_runs=len(runs),
        dispatch_would=dispatch_would,
        dispatch_skip=dispatch_skip,
        gate_pass=gate_pass,
        gate_fail=gate_fail,
        gate_needs_llm=gate_needs_llm,
        skip_reasons=skip_reasons,
        fail_reasons=fail_reasons,
        repos_touched=repos,
        machines=machines,
    )


def load_days(log_dir: Path, days: int = 7) -> dict[str, DailySummary]:
    """Load summaries for the last N days."""
    summaries = {}
    today = datetime.now(timezone.utc).date()

    for i in range(days):
        date = today - timedelta(days=i)
        date_str = date.strftime("%Y-%m-%d")
        log_file = log_dir / f"shadow-{date_str}.jsonl"
        decisions = parse_log_file(log_file)
        if decisions:
            summary = summarize_day(decisions)
            summary.date = date_str
            summaries[date_str] = summary

    return summaries


def format_counter(counter: Counter, limit: int = 5) -> str:
    """Format a counter as a string."""
    if not counter:
        return "(none)"
    items = counter.most_common(limit)
    return "; ".join(f"{k}: {v}" for k, v in items)


def compare_report(log_dir: Optional[Path] = None, days: int = 7) -> str:
    """Generate a comparison report."""
    log_dir = log_dir or SHADOW_LOG_DIR
    summaries = load_days(log_dir, days)

    if not summaries:
        return f"No shadow logs found in {log_dir} for the last {days} days."

    lines = [
        "# StoryPool Shadow Mode Comparison Report",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Log directory: {log_dir}",
        "",
        "## Daily Summary",
        "",
        "| Date | Runs | Dispatch | Skip | Gate Pass | Gate Fail | Need LLM |",
        "|------|------|----------|------|-----------|-----------|----------|",
    ]

    for date_str in sorted(summaries.keys(), reverse=True):
        s = summaries[date_str]
        lines.append(
            f"| {s.date} | {s.total_runs} | {s.dispatch_would} | "
            f"{s.dispatch_skip} | {s.gate_pass} | {s.gate_fail} | {s.gate_needs_llm} |"
        )

    totals = DailySummary(
        date="TOTAL",
        total_runs=sum(s.total_runs for s in summaries.values()),
        dispatch_would=sum(s.dispatch_would for s in summaries.values()),
        dispatch_skip=sum(s.dispatch_skip for s in summaries.values()),
        gate_pass=sum(s.gate_pass for s in summaries.values()),
        gate_fail=sum(s.gate_fail for s in summaries.values()),
        gate_needs_llm=sum(s.gate_needs_llm for s in summaries.values()),
        skip_reasons=Counter(),
        fail_reasons=Counter(),
        repos_touched=set(),
        machines=set(),
    )

    for s in summaries.values():
        totals.skip_reasons.update(s.skip_reasons)
        totals.fail_reasons.update(s.fail_reasons)
        totals.repos_touched.update(s.repos_touched)
        totals.machines.update(s.machines)

    lines.extend([
        "",
        "## Totals",
        "",
        f"- **Total runs:** {totals.total_runs}",
        f"- **Would dispatch:** {totals.dispatch_would}",
        f"- **Dispatch skipped:** {totals.dispatch_skip}",
        f"- **Gate pass:** {totals.gate_pass}",
        f"- **Gate fail:** {totals.gate_fail}",
        f"- **Gate needs LLM:** {totals.gate_needs_llm}",
        "",
        "## Top Skip Reasons",
        "",
        format_counter(totals.skip_reasons, 10),
        "",
        "## Top Fail Reasons",
        "",
        format_counter(totals.fail_reasons, 10),
        "",
        "## Repositories Touched",
        "",
        ", ".join(sorted(totals.repos_touched)) or "(none)",
        "",
        "## Machines",
        "",
        ", ".join(sorted(totals.machines)) or "(none)",
    ])

    if totals.dispatch_would > 0:
        dispatch_rate = (
            totals.dispatch_would
            / (totals.dispatch_would + totals.dispatch_skip)
            * 100
        )
        lines.extend([
            "",
            "## Metrics",
            "",
            f"- **Dispatch success rate:** {dispatch_rate:.1f}%",
        ])

    if totals.gate_pass + totals.gate_fail > 0:
        gate_pass_rate = (
            totals.gate_pass
            / (totals.gate_pass + totals.gate_fail)
            * 100
        )
        lines.append(f"- **Gate pass rate (deterministic):** {gate_pass_rate:.1f}%")

    lines.extend([
        "",
        "## Validation Checklist",
        "",
        "- [ ] Dispatch decisions match expected capacity patterns",
        "- [ ] Gate failures are for legitimate reasons (draft, CI fail, no approval)",
        "- [ ] No unexpected skips due to budget caps",
        "- [ ] Machine guards trigger only when expected",
        "",
        "---",
        "*This report is generated by `shadow_compare.py` for Phase 1 validation.*",
    ])

    return "\n".join(lines)


def main() -> int:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare shadow mode decisions"
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=SHADOW_LOG_DIR,
        help="Directory containing shadow logs",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to include in report",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output file (default: stdout)",
    )
    args = parser.parse_args()

    report = compare_report(args.log_dir, args.days)

    if args.output:
        args.output.write_text(report)
        print(f"Report written to {args.output}")
    else:
        print(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
