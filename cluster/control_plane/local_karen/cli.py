#!/usr/bin/env python3
"""CLI for Local Karen gate.

Usage:
    karen-gate <owner>/<repo> <pr_number>
    karen-gate rupret007/webjam 123
    karen-gate rupret007/webjam 123 --comment
    karen-gate rupret007/webjam 123 --budget 1.00

Options:
    --comment       Post verdict as PR comment
    --budget USD    Budget cap per run (default: $0.50)
    --config PATH   Path to config file
    --dry-run       Show what would be done without running
    --json          Output verdict as JSON
    --verbose       Verbose output
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import KarenConfig
from .gate import run_gate, GateRunResult


def parse_repo_arg(arg: str) -> tuple[str, str]:
    """Parse owner/repo argument."""
    if "/" not in arg:
        print(f"Error: repository must be in owner/repo format, got: {arg}", file=sys.stderr)
        sys.exit(1)

    parts = arg.split("/", 1)
    return parts[0], parts[1]


def print_verdict(result: GateRunResult, verbose: bool = False) -> None:
    """Print verdict to stdout."""
    v = result.verdict

    if v.passed:
        print(f"✅ KARENPASS: {v.owner}/{v.repo}#{v.pr_number}@{v.head_sha[:12]}")
    else:
        print(f"❌ KARENFAIL: {v.owner}/{v.repo}#{v.pr_number}@{v.head_sha[:12]}")

    print(f"   Reason: {v.reason}")
    print(f"   Reviewer: {v.reviewer} | Tier: {v.tier} | Confidence: {v.confidence:.0%}")

    if not result.sha_verified:
        print(f"   ⚠️  WARNING: SHA could not be verified (may have changed)")

    if result.verdict_path:
        print(f"   Verdict file: {result.verdict_path}")

    if result.error:
        print(f"   Error: {result.error}")

    if verbose:
        print("\n   Checks:")
        for check in v.check_results:
            emoji = "✅" if check["verdict"] == "PASS" else "❌"
            print(f"      {emoji} {check['check']}: {check['reason']}")

        if v.review_results:
            print("\n   Reviews:")
            for review in v.review_results:
                emoji = "✅" if review["verdict"] == "PASS" else "❌" if review["verdict"] == "FAIL" else "⚠️"
                print(f"      {emoji} {review['reviewer']}: {review['reason']}")
                print(f"         Cost: ${review['cost_usd']:.4f} | Time: {review['duration_sec']:.1f}s")

    print(f"\n   Budget used: ${v.budget_used_usd:.4f} | Total time: {v.duration_sec:.1f}s")


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="karen-gate",
        description="Local Karen ship gate — deterministic checks + LLM review",
    )
    parser.add_argument(
        "repo",
        help="Repository in owner/repo format (e.g., rupret007/webjam)",
    )
    parser.add_argument(
        "pr",
        type=int,
        help="PR number",
    )
    parser.add_argument(
        "--comment",
        action="store_true",
        help="Post verdict as PR comment",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=None,
        help="Budget cap per run in USD (default: $0.50)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without running",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output verdict as JSON",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    owner, repo = parse_repo_arg(args.repo)

    if args.config:
        config = KarenConfig.load(args.config)
    else:
        config = KarenConfig.load()

    if args.budget is not None:
        config.budget_per_run_usd = args.budget

    if args.dry_run:
        print(f"Would run gate on {owner}/{repo}#{args.pr}")
        print(f"Config: {config}")
        print(f"Comment: {args.comment}")
        return 0

    if args.verbose:
        print(f"Running gate on {owner}/{repo}#{args.pr}...")
        print(f"Reviewer order: {config.reviewer_order}")

    result = run_gate(
        owner=owner,
        repo=repo,
        pr_number=args.pr,
        config=config,
        post_comment=args.comment,
    )

    if args.json_output:
        print(result.verdict.to_json())
    else:
        print_verdict(result, verbose=args.verbose)

    return 0 if result.verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
