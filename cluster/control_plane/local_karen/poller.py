#!/usr/bin/env python3
"""Polling daemon for Local Karen gate.

Polls GitHub for PRs labeled `needs-karen` and runs the gate on them.
Designed to be run by launchd (macOS) or cron.

Each tick:
1. Self-update from git (fast-forward only, reinstall if HEAD changed)
2. Discover all rupret007 repos with open PRs (excluding blocked repos)
3. Label queued PRs with needs-karen
4. Run gate on PRs with needs-karen label
5. Write backlog report
"""

from __future__ import annotations

# Snapshot before loading any local review modules that an update could replace.
from .runtime_status import record_runtime

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Any

from .config import KarenConfig
from .review_queue import (Candidate, ReviewQueue, enrich, ADMISSION_SECONDS, MAX_REVIEWS_PER_CYCLE)
from .repo_scan import RepoScan, RepoScanError
from ..github_budget import GitHubDeferred
from ..github_reads import GitHubReadError
from .gate import run_gate
from .context_diagnostics import sanitize_context_diagnostics
from .github_client import (
    list_prs_with_label,
    remove_label,
    add_label,
    GitHubError,
)

try:
    from ..backlog import (
        discover_repos,
        label_queued_prs,
        undraft_green_prs,
        collect_backlog,
        write_backlog_report,
        ensure_label_exists,
        write_karen_fails_report,
        EXCLUDED_REPOS,
        MAX_UNDRAFTS_PER_TICK,
    )
    BACKLOG_AVAILABLE = True
except ImportError:
    BACKLOG_AVAILABLE = False
    MAX_UNDRAFTS_PER_TICK = 5
    ensure_label_exists = None  # type: ignore
    write_karen_fails_report = None  # type: ignore

# Self-update must remain available even if optional dispatch/backlog modules fail.
try:
    from ..self_update import self_update
except ImportError as exc:
    SELF_UPDATE_IMPORT_ERROR = str(exc)
    self_update = None

try:
    from ..fix_dispatcher import dispatch_fix, should_attempt_fix
    FIX_DISPATCH_AVAILABLE = True
except ImportError:
    FIX_DISPATCH_AVAILABLE = False
    dispatch_fix = None
    should_attempt_fix = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("karen-poller")

# Cache of repos where we've ensured result labels exist
_result_labels_ensured = set()  # type: set


def _ensure_result_label(repo, label):
    # type: (str, str) -> None
    """Ensure karen-pass or karen-fail label exists in repo."""
    cache_key = f"{repo}:{label}"
    if cache_key in _result_labels_ensured:
        return
    if ensure_label_exists is None:
        return

    color = "0e8a16" if label == "karen-pass" else "d93f0b"
    desc = "Karen gate passed" if label == "karen-pass" else "Karen gate failed"
    try:
        ensure_label_exists(repo, label, color=color, description=desc)
        _result_labels_ensured.add(cache_key)
    except Exception as e:
        logger.debug(f"Could not ensure label {label} in {repo}: {e}")


def _dispatch_fix_if_eligible(repo, pr_number, verdict):
    # type: (str, int, Any) -> None
    """Dispatch a fix attempt if eligible (low effort, not security, under cap)."""
    if not FIX_DISPATCH_AVAILABLE or dispatch_fix is None:
        return

    verdict_dict = verdict.to_dict() if hasattr(verdict, "to_dict") else {}

    try:
        should_fix, reason = should_attempt_fix(repo, pr_number, verdict_dict)
        if not should_fix:
            logger.debug(f"Skip fix for {repo}#{pr_number}: {reason}")
            return

        machine = os.environ.get("STORYPOOL_MACHINE", "mini")
        live_mode = os.environ.get("STORYPOOL_LIVE", "").lower() in ("1", "true", "yes")

        logger.info(f"Dispatching fix for {repo}#{pr_number}...")
        result = dispatch_fix(
            repo=repo,
            pr_number=pr_number,
            verdict=verdict_dict,
            dry_run=not live_mode,
            machine=machine,
        )

        if result.get("dispatched"):
            logger.info(f"Fix dispatched for {repo}#{pr_number}: {result.get('reason')}")
        elif result.get("left_for_jeff") or "left for Jeff" in result.get("reason", ""):
            logger.info(f"Left {repo}#{pr_number} for Jeff: {result.get('reason')}")
        else:
            logger.debug(f"Fix not dispatched for {repo}#{pr_number}: {result.get('reason')}")
    except Exception as e:
        logger.warning(f"Fix dispatch error for {repo}#{pr_number}: {e}")


@dataclass
class PollState:
    """State tracking for the poller."""

    last_poll: str
    prs_checked: list  # List[dict]

    def to_dict(self) -> dict:
        return {"last_poll": self.last_poll, "prs_checked": self.prs_checked}

    @classmethod
    def from_dict(cls, d: dict) -> "PollState":
        return cls(
            last_poll=d.get("last_poll", ""),
            prs_checked=d.get("prs_checked", []),
        )


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
    repos,  # type: List[str]
    config,  # type: KarenConfig
    post_comment=True,  # type: bool
    remove_label_on_complete=True,  # type: bool
    guard_machine=None,
    scan=None,
):
    # type: (...) -> List[dict]
    """Rank the whole labeled queue and admit a bounded set of complete reviews.

    Args:
        repos: List of repos to poll in owner/repo format
        config: Karen configuration
        post_comment: Whether to post verdict comments
        remove_label_on_complete: Whether to remove needs-karen label after gate

    Returns:
        List of results with PR info and verdicts
    """
    results = []
    candidates = []

    for repo_str in repos:
        if "/" not in repo_str:
            logger.warning(f"Invalid repo format: {repo_str}")
            continue

        owner, repo = repo_str.split("/", 1)
        if config.is_blocked_repo(owner, repo):
            if scan is not None:
                scan.observed(repo_str)
            continue

        try:
            prs = list_prs_with_label(owner, repo, config.poll_label)
        except GitHubDeferred:
            logger.warning("Remaining reviews deferred by GitHub API budget/backoff")
            raise
        except GitHubError as e:
            logger.error(f"Failed to list PRs for {owner}/{repo}: {e}")
            continue

        if scan is not None:
            scan.observed(repo_str)

        if not prs:
            logger.debug(f"No PRs with {config.poll_label} label in {owner}/{repo}")
            continue

        logger.info(f"Found {len(prs)} PR(s) with {config.poll_label} label in {owner}/{repo}")

        for pr_data in prs:
            pr_number = pr_data.get("number")
            if not pr_number:
                continue

            if pr_data.get("isDraft"):
                results.append({"repo": repo_str, "pr": pr_number, "skipped": True,
                                "reason": "Draft PR awaits author decision"})
                continue

            candidates.append(Candidate(repo_str, pr_data))

    try:
        queue = ReviewQueue(config)
        if scan is None:
            enrich(candidates)
        else:
            # One optional batch, aged work first. Unsampled candidates remain
            # unknown and still require the normal exact-head gate checks.
            metadata_order = sorted(candidates, key=lambda item: (-queue.deferred.get(item.key, 0), item.key))
            enrich(metadata_order, max_batches=1)
        ordered = queue.order(candidates)
        queue.begin(candidates, observed_repos=scan.observed_repos if scan is not None else None)
    except GitHubDeferred:
        logger.warning("Review queue deferred by shared GitHub API budget")
        raise
    except OSError:
        logger.error("Review scheduling state unavailable; cycle deferred")
        if scan is not None:
            raise RepoScanError('Review scheduling state unavailable') from None
        return results

    logger.info("Review queue: %d candidates, %d small CI-green, at most %d admissions",
                len(candidates), sum(candidate.ci == 'green' and candidate.lines is not None
                                     and candidate.lines < 300 for candidate in candidates),
                MAX_REVIEWS_PER_CYCLE)

    admission_deadline = time.monotonic() + ADMISSION_SECONDS
    attempted = 0
    for candidate in ordered:
        if attempted >= MAX_REVIEWS_PER_CYCLE or time.monotonic() >= admission_deadline:
            logger.info("Review admission window complete; remaining queue deferred")
            break
        repo_str, pr_data = candidate.repo, candidate.pr
        owner, repo = repo_str.split('/', 1)
        pr_number = pr_data.get('number')
        from ..node_control import is_draining
        if is_draining(guard_machine):
            logger.info("Remaining reviews deferred: node in maintenance")
            return results
        if guard_machine:
            from ..guards import MachineGuards
            guard = MachineGuards(guard_machine).check_all()
            if not guard.passed:
                logger.warning("Remaining reviews deferred: %s", guard.reason)
                return results
        try:
            queue.attempted(candidate)
        except OSError:
            logger.error("Review scheduling state unavailable; remaining queue deferred")
            if scan is not None:
                raise RepoScanError('Review scheduling state unavailable') from None
            return results
        attempted += 1
        logger.info(f"Running gate on {owner}/{repo}#{pr_number}")

        diagnostics = []
        try:
            result = run_gate(
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                config=config,
                post_comment=post_comment,
                expected_sha=pr_data.get("headRefOid", ""),
            )

            if result.error or not result.sha_verified or not result.verdict_path:
                diagnostics = sanitize_context_diagnostics(getattr(result, 'context_diagnostics', None))
                raise RuntimeError("Review did not produce a verified artifact")

            results.append({
                "repo": f"{owner}/{repo}",
                "pr": pr_number,
                "sha": result.verdict.head_sha,
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
                    _ensure_result_label(f"{owner}/{repo}", result_label)
                    add_label(owner, repo, pr_number, result_label)
                    remove_label(owner, repo, pr_number,
                                 "karen-fail" if result.verdict.passed else "karen-pass")
                except GitHubError as e:
                    logger.warning(f"Failed to update labels on {owner}/{repo}#{pr_number}: {e}")

            try:
                from .verdict_push import push
                logger.info("Verdict push: %s", push(config))
            except Exception as exc:
                logger.warning("Verdict push will retry: %s", type(exc).__name__)

            # Dispatch fix attempt for FAIL verdicts (max 2 attempts, never for security/high)
            if not result.verdict.passed and FIX_DISPATCH_AVAILABLE:
                _dispatch_fix_if_eligible(f"{owner}/{repo}", pr_number, result.verdict)

        except GitHubDeferred:
            if scan is not None and any('sha' in item and 'passed' in item for item in results):
                scan.deferred_after_review = True
                return results
            raise
        except Exception as e:
            logger.error(f"Failed to run gate on {owner}/{repo}#{pr_number}: {e}")
            error_record = {
                "repo": f"{owner}/{repo}",
                "pr": pr_number,
                "error": str(e),
            }
            if diagnostics:
                error_record['context_diagnostics'] = diagnostics
            results.append(error_record)

    return results


def _run_poll_once(config=None, auto_discover=True, auto_label=True, auto_update=True, post_comment=False, remove_label_on_complete=True):
    from .poller_runtime import _run_poll_once as implementation
    return implementation(sys.modules[__name__], config=config, auto_discover=auto_discover, auto_label=auto_label, auto_update=auto_update, post_comment=post_comment, remove_label_on_complete=remove_label_on_complete)


def run_poll_once(config=None, auto_discover=True, auto_label=True, auto_update=True, post_comment=False, remove_label_on_complete=True):
    from .poller_runtime import run_poll_once as implementation
    return implementation(sys.modules[__name__], config=config, auto_discover=auto_discover, auto_label=auto_label, auto_update=auto_update, post_comment=post_comment, remove_label_on_complete=remove_label_on_complete)


def run_poll_loop(config: Optional[KarenConfig]=None) -> None:
    from .poller_runtime import run_poll_loop as implementation
    return implementation(sys.modules[__name__], config=config)


def main() -> int:
    from .poller_runtime import main as implementation
    return implementation(sys.modules[__name__])


if __name__ == "__main__":
    sys.exit(main())
