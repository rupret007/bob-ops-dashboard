"""Main gate logic for Local Karen.

Orchestrates deterministic checks, LLM review, and verdict generation.
Enforces all invariants: fail closed, never merge/push/sudo, SHA pinning.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from .config import KarenConfig
from .checks import PRInfo, run_all_checks, all_checks_pass, get_failed_checks, CheckResult
from .reviewers import (
    run_review_chain,
    ReviewResult,
    ReviewVerdict,
    classify_risk_tier,
)
from .verdict import Verdict, create_verdict, save_verdict
from .github_client import fetch_pr_info, verify_sha_current, GitHubError

try:
    from ..metrics import MetricsTracker
    _metrics = MetricsTracker()
except (ImportError, OSError):
    _metrics = None  # type: ignore


def _record_review_metrics(**kwargs) -> None:
    """Optional telemetry must not interrupt verdict saving or SHA checks."""
    if _metrics:
        try:
            _metrics.record_karen_review(**kwargs)
        except OSError:
            logging.getLogger(__name__).warning("Unable to persist Karen review metrics")


@dataclass
class GateRunResult:
    """Result of running the gate on a PR."""

    verdict: Verdict
    verdict_path: Optional[str]
    sha_verified: bool
    error: Optional[str] = None


class GateRunner:
    """Runs the Local Karen gate on a PR.

    Invariants:
    - NEVER merge, push, force-push, tag, release, or use sudo
    - Verdict for a stale SHA is invalid
    - Fail closed on any error
    """

    def __init__(self, config: Optional[KarenConfig] = None):
        self.config = config or KarenConfig.load()

    def run(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        post_comment: bool = False,
    ) -> GateRunResult:
        """Run the gate on a PR.

        Args:
            owner: Repository owner
            repo: Repository name
            pr_number: PR number
            post_comment: Whether to post verdict as PR comment

        Returns:
            GateRunResult with verdict and metadata
        """
        start_time = time.time()

        try:
            pr = fetch_pr_info(owner, repo, pr_number)
        except GitHubError as e:
            return self._fail_closed(
                owner, repo, pr_number, "unknown",
                f"Failed to fetch PR info: {e}",
            )
        except Exception as e:
            return self._fail_closed(
                owner, repo, pr_number, "unknown",
                f"Unexpected error fetching PR: {e}",
            )

        pinned_sha = pr.head_sha

        check_results = run_all_checks(pr, self.config)

        if not all_checks_pass(check_results):
            failed = get_failed_checks(check_results)
            reason = "; ".join(f.reason for f in failed[:3])
            verdict = create_verdict(
                pr=pr,
                check_results=check_results,
                review_results=[],
                final_passed=False,
                final_reason=f"Deterministic checks failed: {reason}",
                final_reviewer="checks",
            )
            _record_review_metrics(
                passed=False,
                first_try=True,
                caught_by_pre_llm=True,
            )
            return self._finalize(verdict, pr, post_comment, start_time)

        try:
            review_result, all_reviews = run_review_chain(pr, self.config)
        except Exception as e:
            return self._fail_closed(
                owner, repo, pr_number, pinned_sha,
                f"Review chain crashed: {e}",
                check_results=check_results,
            )

        if review_result.verdict == ReviewVerdict.ERROR:
            verdict = create_verdict(
                pr=pr,
                check_results=check_results,
                review_results=all_reviews,
                final_passed=False,
                final_reason=f"Review error (fail closed): {review_result.reason}",
                final_reviewer=review_result.reviewer,
            )
            return self._finalize(verdict, pr, post_comment, start_time)

        final_passed = review_result.verdict == ReviewVerdict.PASS

        if review_result.verdict == ReviewVerdict.BORDERLINE:
            final_passed = False

        verdict = create_verdict(
            pr=pr,
            check_results=check_results,
            review_results=all_reviews,
            final_passed=final_passed,
            final_reason=review_result.reason,
            final_reviewer=review_result.reviewer,
        )

        _record_review_metrics(
            passed=final_passed,
            first_try=True,
            caught_by_pre_llm=False,
            caught_by_llm=not final_passed,
        )

        return self._finalize(verdict, pr, post_comment, start_time)

    def _finalize(
        self,
        verdict: Verdict,
        pr: PRInfo,
        post_comment: bool,
        start_time: float,
    ) -> GateRunResult:
        """Finalize the verdict: save, optionally post comment, verify SHA."""
        verdict.duration_sec = time.time() - start_time

        try:
            verdict_path = save_verdict(verdict, self.config)
        except Exception as e:
            verdict_path = None

        try:
            sha_verified = verify_sha_current(
                pr.owner, pr.repo, pr.number, pr.head_sha
            )
        except GitHubError:
            sha_verified = False

        if not sha_verified:
            verdict.passed = False
            verdict.reason = f"SHA changed during gate run (was {pr.head_sha[:12]})"

            try:
                verdict_path = save_verdict(verdict, self.config)
            except Exception:
                pass

        if post_comment:
            try:
                from .verdict import format_verdict_comment
                from .github_client import post_verdict_comment

                comment = format_verdict_comment(verdict)
                post_verdict_comment(pr.owner, pr.repo, pr.number, comment)
            except Exception:
                pass

        return GateRunResult(
            verdict=verdict,
            verdict_path=str(verdict_path) if verdict_path else None,
            sha_verified=sha_verified,
        )

    def _fail_closed(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        sha: str,
        reason: str,
        check_results: Optional[list[CheckResult]] = None,
    ) -> GateRunResult:
        """Create a FAIL verdict for any error (fail closed)."""
        verdict = Verdict(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            head_sha=sha,
            passed=False,
            reason=f"Fail closed: {reason}",
            timestamp="",
            reviewer="gate_runner",
            tier="unknown",
            confidence=1.0,
            check_results=[
                {
                    "check": r.check_name,
                    "verdict": r.verdict.value,
                    "reason": r.reason,
                    "details": r.details,
                }
                for r in (check_results or [])
            ],
            review_results=[],
        )

        from datetime import datetime, timezone
        verdict.timestamp = datetime.now(timezone.utc).isoformat()

        try:
            verdict_path = save_verdict(verdict, self.config)
        except Exception:
            verdict_path = None

        return GateRunResult(
            verdict=verdict,
            verdict_path=str(verdict_path) if verdict_path else None,
            sha_verified=False,
            error=reason,
        )


def run_gate(
    owner: str,
    repo: str,
    pr_number: int,
    config: Optional[KarenConfig] = None,
    post_comment: bool = False,
) -> GateRunResult:
    """Convenience function to run the gate on a PR."""
    runner = GateRunner(config)
    return runner.run(owner, repo, pr_number, post_comment)
