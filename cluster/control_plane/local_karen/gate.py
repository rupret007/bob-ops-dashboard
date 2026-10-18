"""Main gate logic for Local Karen.

Orchestrates deterministic checks, LLM review, and verdict generation.
Enforces all invariants: fail closed, never merge/push/sudo, SHA pinning.
"""

from __future__ import annotations

import os
import time
from contextvars import ContextVar

from dataclasses import dataclass
from typing import Optional

from ..coordination import ClaimClient, CoordinationError, Lease
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
except ImportError:
    _metrics = None  # type: ignore


_review_lease = ContextVar("karen_review_lease", default=None)


def review_checkpoint():
    """Fence provider calls and publication when a review owns a shared lease."""
    lease = _review_lease.get()
    if lease is not None:
        lease.renew()


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

        if self.config.is_blocked_repo(owner, repo):
            return self._fail_closed(owner, repo, pr_number, pinned_sha, "never-touch repository")
        if pr.is_draft:
            return self._fail_closed(owner, repo, pr_number, pinned_sha, "Draft PR awaits author")
        try:
            client = ClaimClient.from_env()
            claim = client.acquire(f"{owner}/{repo}", pr_number, pinned_sha,
                                   os.environ.get("STORYPOOL_MACHINE", "mini"))
            with Lease(client, claim) as lease:
                token = _review_lease.set(lease)
                try:
                    return self._run_claimed(pr, post_comment, start_time)
                finally:
                    _review_lease.reset(token)
        except CoordinationError as exc:
            return self._fail_closed(owner, repo, pr_number, pinned_sha, str(exc))

    def _run_claimed(self, pr, post_comment, start_time):
        owner, repo, pr_number, pinned_sha = pr.owner, pr.repo, pr.number, pr.head_sha
        check_results = run_all_checks(pr, self.config)

        errors = [r.reason for r in check_results if r.verdict.value == "ERROR"]
        if errors:
            return self._fail_closed(owner, repo, pr_number, pinned_sha, "; ".join(errors), check_results)

        failed = get_failed_checks(check_results)
        # Draft/blocked repositories remain deferred, without a published verdict.
        deferred = [r for r in failed if r.check_name in ("blocked_repo", "not_draft")]
        if deferred:
            return self._fail_closed(owner, repo, pr_number, pinned_sha,
                                     "; ".join(r.reason for r in deferred), check_results)

        secrets = [r for r in failed if r.check_name == "no_secrets"]
        if secrets:
            # A concrete security finding is a FAIL, not a provider outage. Never
            # send suspected credentials to models; publish only redacted locations.
            verdict = create_verdict(
                pr, [r for r in check_results if r.check_name == "no_secrets"], [],
                False, "; ".join(r.reason + ": " + (r.details or "") for r in secrets),
                "deterministic_security",
            )
            verdict.tier = "high"
            return self._finalize(verdict, pr, post_comment, start_time)

        try:
            review_checkpoint()
            review_result, all_reviews = run_review_chain(pr, self.config)
            review_checkpoint()
        except Exception as e:
            return self._fail_closed(
                owner, repo, pr_number, pinned_sha,
                f"Review chain crashed: {e}",
                check_results=check_results,
            )

        if review_result.verdict == ReviewVerdict.ERROR:
            return self._fail_closed(owner, repo, pr_number, pinned_sha,
                                     "Review error: " + review_result.reason, check_results)

        final_passed = review_result.verdict == ReviewVerdict.PASS and not failed

        if review_result.verdict == ReviewVerdict.BORDERLINE:
            final_passed = False

        verdict = create_verdict(
            pr=pr,
            check_results=check_results,
            review_results=all_reviews,
            final_passed=final_passed,
            final_reason=("Deterministic checks failed: " + "; ".join(r.reason for r in failed)
                          + "; Review: " + review_result.reason) if failed else review_result.reason,
            final_reviewer=review_result.reviewer,
        )

        if _metrics:
            _metrics.record_karen_review(
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
            sha_verified = verify_sha_current(pr.owner, pr.repo, pr.number, pr.head_sha)
        except GitHubError as exc:
            return self._fail_closed(pr.owner, pr.repo, pr.number, pr.head_sha,
                                     "SHA verification unavailable: " + str(exc))
        if not sha_verified:
            return self._fail_closed(pr.owner, pr.repo, pr.number, pr.head_sha,
                                     "SHA changed during gate run; retry current head")
        try:
            review_checkpoint()
            verdict_path = save_verdict(verdict, self.config)
        except Exception as exc:
            return self._fail_closed(pr.owner, pr.repo, pr.number, pr.head_sha,
                                     "Verdict persistence failed: " + type(exc).__name__)

        if post_comment:
            try:
                from .verdict import format_verdict_comment
                from .github_client import post_verdict_comment

                review_checkpoint()
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
        """Return a fail-closed diagnostic; errors never become durable verdicts."""
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

        verdict.status = "ERROR"
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
