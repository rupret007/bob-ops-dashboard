#!/usr/bin/env python3
"""Polling daemon for Local Karen gate.

Polls GitHub for PRs labeled `needs-karen` and runs the gate on them.
Designed to be run by launchd (macOS) or cron.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import KarenConfig
from .gate import run_gate
from .github_client import list_prs_with_label, remove_label, add_label, GitHubError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("karen-poller")


@dataclass
class PollState:
    """State tracking for the poller."""

    last_poll: str
    prs_checked: list[dict]

    def to_dict(self) -> dict:
        return {"last_poll": self.last_poll, "prs_checked": self.prs_checked}

    @classmethod
    def from_dict(cls, d: dict) -> "PollState":
        return cls(
            last_poll=d.get("last_poll", ""),
            prs_checked=d.get("prs_checked", []),
        )


from dataclasses import dataclass


def load_poll_state(config: KarenConfig) -> PollState:
    """Load poll state from disk."""
    state_file = config.get_state_dir() / "poll_state.json"
    if state_file.is_file():
        try:
            data = json.loads(state_file.read_text())
            return PollState.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            pass
    return PollState(last_poll="", prs_checked=[])


def save_poll_state(state: PollState, config: KarenConfig) -> None:
    """Save poll state to disk."""
    state_dir = config.get_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "poll_state.json"
    state_file.write_text(json.dumps(state.to_dict(), indent=2))


def poll_and_run(
    repos: list[str],
    config: KarenConfig,
    post_comment: bool = True,
    remove_label_on_complete: bool = True,
) -> list[dict]:
    """Poll for PRs with needs-karen label and run gate on them.

    Args:
        repos: List of repos to poll in owner/repo format
        config: Karen configuration
        post_comment: Whether to post verdict comments
        remove_label_on_complete: Whether to remove needs-karen label after gate

    Returns:
        List of results with PR info and verdicts
    """
    results = []

    for repo_str in repos:
        if "/" not in repo_str:
            logger.warning(f"Invalid repo format: {repo_str}")
            continue

        owner, repo = repo_str.split("/", 1)

        try:
            prs = list_prs_with_label(owner, repo, config.poll_label)
        except GitHubError as e:
            logger.error(f"Failed to list PRs for {owner}/{repo}: {e}")
            continue

        if not prs:
            logger.debug(f"No PRs with {config.poll_label} label in {owner}/{repo}")
            continue

        logger.info(f"Found {len(prs)} PR(s) with {config.poll_label} label in {owner}/{repo}")

        for pr_data in prs:
            pr_number = pr_data.get("number")
            if not pr_number:
                continue

            logger.info(f"Running gate on {owner}/{repo}#{pr_number}")

            try:
                result = run_gate(
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    config=config,
                    post_comment=post_comment,
                )

                results.append({
                    "repo": f"{owner}/{repo}",
                    "pr": pr_number,
                    "sha": result.verdict.head_sha[:12],
                    "passed": result.verdict.passed,
                    "reason": result.verdict.reason,
                })

                logger.info(
                    f"{'PASS' if result.verdict.passed else 'FAIL'}: "
                    f"{owner}/{repo}#{pr_number} - {result.verdict.reason[:50]}"
                )

                if remove_label_on_complete:
                    try:
                        remove_label(owner, repo, pr_number, config.poll_label)
                        logger.info(f"Removed {config.poll_label} label from {owner}/{repo}#{pr_number}")

                        result_label = "karen-pass" if result.verdict.passed else "karen-fail"
                        add_label(owner, repo, pr_number, result_label)
                    except GitHubError as e:
                        logger.warning(f"Failed to update labels on {owner}/{repo}#{pr_number}: {e}")

            except Exception as e:
                logger.error(f"Failed to run gate on {owner}/{repo}#{pr_number}: {e}")
                results.append({
                    "repo": f"{owner}/{repo}",
                    "pr": pr_number,
                    "error": str(e),
                })

    return results


def run_poll_once(config: Optional[KarenConfig] = None) -> list[dict]:
    """Run a single poll cycle.

    Reads repos from environment or config.
    """
    if config is None:
        config = KarenConfig.load()

    repos_env = os.environ.get("KAREN_REPOS", "")
    if repos_env:
        repos = [r.strip() for r in repos_env.split(",") if r.strip()]
    else:
        repos = ["rupret007/webjam", "rupret007/bob-ops-dashboard"]

    logger.info(f"Polling {len(repos)} repo(s) for PRs with {config.poll_label} label")

    results = poll_and_run(repos, config)

    state = load_poll_state(config)
    state.last_poll = datetime.now(timezone.utc).isoformat()
    state.prs_checked.extend(results)
    state.prs_checked = state.prs_checked[-100:]
    save_poll_state(state, config)

    return results


def run_poll_loop(config: Optional[KarenConfig] = None) -> None:
    """Run continuous polling loop.

    For development/testing only — production should use launchd.
    """
    if config is None:
        config = KarenConfig.load()

    interval_sec = config.poll_interval_minutes * 60

    logger.info(f"Starting poll loop with {config.poll_interval_minutes} minute interval")

    while True:
        try:
            run_poll_once(config)
        except KeyboardInterrupt:
            logger.info("Interrupted, stopping poll loop")
            break
        except Exception as e:
            logger.error(f"Poll cycle failed: {e}")

        logger.info(f"Sleeping for {config.poll_interval_minutes} minutes")
        time.sleep(interval_sec)


def main() -> int:
    """CLI entry point for poller."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="karen-poller",
        description="Poll for PRs with needs-karen label and run gate",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (for launchd/cron)",
    )
    parser.add_argument(
        "--repos",
        type=str,
        default=None,
        help="Comma-separated list of repos to poll (owner/repo)",
    )
    parser.add_argument(
        "--no-comment",
        action="store_true",
        help="Don't post verdict comments",
    )
    parser.add_argument(
        "--keep-label",
        action="store_true",
        help="Don't remove needs-karen label after gate",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config file",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.config:
        config = KarenConfig.load(args.config)
    else:
        config = KarenConfig.load()

    if args.repos:
        os.environ["KAREN_REPOS"] = args.repos

    if args.once:
        results = run_poll_once(config)
        for r in results:
            if "error" in r:
                print(f"❌ {r['repo']}#{r['pr']}: {r['error']}")
            elif r.get("passed"):
                print(f"✅ {r['repo']}#{r['pr']}@{r['sha']}: {r['reason'][:50]}")
            else:
                print(f"❌ {r['repo']}#{r['pr']}@{r['sha']}: {r['reason'][:50]}")
        return 0
    else:
        run_poll_loop(config)
        return 0


if __name__ == "__main__":
    sys.exit(main())
