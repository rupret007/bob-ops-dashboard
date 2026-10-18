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
from .gate import run_gate
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
):
    # type: (...) -> List[dict]
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
        if config.is_blocked_repo(owner, repo):
            continue

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

            if pr_data.get("isDraft"):
                results.append({"repo": repo_str, "pr": pr_number, "skipped": True,
                                "reason": "Draft PR awaits author decision"})
                continue

            if guard_machine:
                from ..guards import MachineGuards
                guard = MachineGuards(guard_machine).check_all()
                if not guard.passed:
                    logger.warning("Remaining reviews deferred: %s", guard.reason)
                    return results
            logger.info(f"Running gate on {owner}/{repo}#{pr_number}")

            try:
                result = run_gate(
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    config=config,
                    post_comment=post_comment,
                )

                if result.error or not result.sha_verified or not result.verdict_path:
                    raise RuntimeError(result.error or "Review did not produce a verified artifact")

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

            except Exception as e:
                logger.error(f"Failed to run gate on {owner}/{repo}#{pr_number}: {e}")
                results.append({
                    "repo": f"{owner}/{repo}",
                    "pr": pr_number,
                    "error": str(e),
                })
                

    return results


def _run_poll_once(config=None, auto_discover=True, auto_label=True, auto_update=True,
                  post_comment=False, remove_label_on_complete=True):
    # type: (Optional[KarenConfig], bool, bool, bool) -> List[dict]
    """Run a single poll cycle.

    Args:
        config: Karen configuration
        auto_discover: Whether to auto-discover repos (default True)
        auto_label: Whether to auto-label queued PRs with needs-karen (default True)
        auto_update: Whether to self-update from git (default True)

    Reads repos from environment or config, or auto-discovers if enabled.
    Auto-discovery wins over single-repo config (repos=[] or 'auto' triggers discovery).
    """
    machine = os.environ.get("STORYPOOL_MACHINE", "mini")
    install_dir_env = os.environ.get("STORYPOOL_INSTALL_DIR")
    install_dir = Path(install_dir_env).expanduser() if install_dir_env else None

    if auto_update:
        try:
            if self_update is None:
                raise RuntimeError(SELF_UPDATE_IMPORT_ERROR)
            update_result = self_update(machine=machine, install_dir=install_dir)
            outcome = update_result.get("outcome", "UNKNOWN")
            reason = update_result.get("reason", "no reason")
            
            if outcome == "UPDATED":
                logger.info("Self-update UPDATED: %s -> %s (module_copied=%s, reinstalled=%s)", 
                           update_result.get("old_head", "?"),
                           update_result.get("new_head", "?"),
                           update_result.get("module_copied", False),
                           update_result.get("reinstalled", False))
            elif outcome == "SKIPPED":
                logger.info("Self-update SKIPPED: %s", reason)
            elif outcome == "FAILED":
                logger.warning("Self-update FAILED: %s", reason)
            else:
                logger.info("Self-update %s: %s", outcome, reason)
        except Exception as e:
            logger.warning("Self-update FAILED: %s", e)

    KarenConfig.migrate_config()
    if config is None:
        config = KarenConfig.load()

    repos_env = os.environ.get("KAREN_REPOS", "")
    if repos_env:
        repos = [r.strip() for r in repos_env.split(",") if r.strip()]
    elif auto_discover and BACKLOG_AVAILABLE and config.should_auto_discover():
        logger.info("Auto-discovering repos (config.repos='%s')...", config.repos)
        repos = discover_repos(owner="rupret007")
        logger.info("Discovered %d repos", len(repos))
    elif config.get_repos_list():
        repos = config.get_repos_list()
        logger.info("Using %d repo(s) from config", len(repos))
    else:
        logger.info("Auto-discovering repos (fallback)...")
        if BACKLOG_AVAILABLE:
            repos = discover_repos(owner="rupret007")
            logger.info("Discovered %d repos", len(repos))
        else:
            repos = ["rupret007/webjam", "rupret007/bob-ops-dashboard"]

    undrafted = []  # type: list
    if auto_label and BACKLOG_AVAILABLE:
        logger.info("Auto-undrafting green draft PRs...")
        try:
            undrafted = undraft_green_prs(repos=repos, dry_run=False, max_undrafts=MAX_UNDRAFTS_PER_TICK)
            if undrafted:
                logger.info("Undrafted %d PR(s) with green CI", len(undrafted))
                for pr in undrafted:
                    logger.info("  Undrafted: %s#%d (%s)", pr["repo"], pr["pr"], pr["title"][:40])
        except Exception as e:
            logger.warning("Auto-undraft failed: %s", e)
        
        logger.info("Auto-labeling queued PRs...")
        try:
            labeled = label_queued_prs(repos=repos, dry_run=False)
            if labeled:
                logger.info("Labeled %d PR(s) with needs-karen", len(labeled))
        except Exception as e:
            logger.warning("Auto-label failed: %s", e)

    logger.info("Polling %d repo(s) for PRs with %s label", len(repos), config.poll_label)

    results = poll_and_run(repos, config, post_comment, remove_label_on_complete, machine)
    try:
        from .verdict_push import push
        receipt = push(config)
        logger.info("Verdict push: %s", receipt)
    except Exception as exc:
        logger.warning("Verdict push will retry: %s", type(exc).__name__)

    state = load_poll_state(config)
    state.last_poll = datetime.now(timezone.utc).isoformat()
    state.prs_checked.extend(results)
    state.prs_checked = state.prs_checked[-100:]
    save_poll_state(state, config)

    if BACKLOG_AVAILABLE:
        logger.info("Writing backlog report...")
        try:
            backlog = collect_backlog(repos=repos)
            backlog["undrafted_this_tick"] = undrafted
            repo_root_env = os.environ.get("STORYPOOL_INSTALL_DIR")
            repo_root = Path(repo_root_env).expanduser() if repo_root_env else None
            write_backlog_report(backlog, repo_root=repo_root)
            logger.info("Backlog: %d PRs queued, %d draft_green, %d undrafted this tick", 
                       backlog["summary"].get("queued", 0),
                       backlog["summary"].get("draft_green", 0),
                       len(undrafted))
        except Exception as e:
            logger.warning("Backlog report failed: %s", e)
        
        # Write Karen failures report
        if write_karen_fails_report is not None:
            try:
                fails_path = write_karen_fails_report()
                logger.info("Wrote Karen failures report to %s", fails_path)
            except Exception as e:
                logger.warning("Karen failures report failed: %s", e)

    return results


def run_poll_once(config=None, auto_discover=True, auto_label=True, auto_update=True,
                  post_comment=False, remove_label_on_complete=True):
    """One review process at a time; apply Mini guards before model work."""
    import fcntl
    from ..guards import MachineGuards
    config = config or KarenConfig.load()
    config.get_state_dir().mkdir(parents=True, exist_ok=True)
    with (config.get_state_dir() / "poller.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("Poll skipped: another review cycle is active")
            return []
        guard = MachineGuards(os.environ.get("STORYPOOL_MACHINE", "mini")).check_all()
        if not guard.passed:
            logger.warning("Poll deferred: %s", guard.reason)
            return []
        return _run_poll_once(config, auto_discover, auto_label, auto_update,
                              post_comment, remove_label_on_complete)


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
    parser.add_argument("--comment", action="store_true", help="Opt in to posting verdict comments")
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
    parser.add_argument(
        "--no-discover",
        action="store_true",
        help="Don't auto-discover repos (use KAREN_REPOS or defaults)",
    )
    parser.add_argument(
        "--no-label",
        action="store_true",
        help="Don't auto-label queued PRs with needs-karen",
    )
    parser.add_argument(
        "--no-update",
        action="store_true",
        help="Don't attempt self-update from git",
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
        results = run_poll_once(
            config,
            auto_discover=not args.no_discover,
            auto_label=not args.no_label,
            auto_update=not args.no_update,
            post_comment=args.comment and not args.no_comment,
            remove_label_on_complete=not args.keep_label,
        )
        for r in results:
            if "error" in r:
                print("ERROR {repo}#{pr}: {error}".format(**r))
            elif r.get("skipped"):
                print("SKIP {repo}#{pr}: {reason}".format(**r))
            elif r.get("passed"):
                print("PASS {repo}#{pr}@{sha}: {reason}".format(
                    **{**r, "reason": str(r.get("reason", ""))[:50]}))
            else:
                print("FAIL {repo}#{pr}@{sha}: {reason}".format(
                    **{**r, "reason": str(r.get("reason", ""))[:50]}))
        return 0
    else:
        run_poll_loop(config)
        return 0


if __name__ == "__main__":
    sys.exit(main())
