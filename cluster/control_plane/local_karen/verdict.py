"""Verdict file generation for Local Karen gate.

Verdicts are keyed to repo+pr+sha and stored as KARENPASS or KARENFAIL files.
A verdict for a stale SHA is invalid.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import KarenConfig
from .checks import CheckResult, PRInfo
from .reviewers import ReviewResult, ReviewVerdict


@dataclass
class Verdict:
    """Final verdict for a PR gate check."""

    owner: str
    repo: str
    pr_number: int
    head_sha: str
    passed: bool
    reason: str
    timestamp: str
    reviewer: str
    tier: str
    confidence: float

    check_results: list[dict]
    review_results: list[dict]

    status: str = "COMPLETE"
    budget_used_usd: float = 0.0
    duration_sec: float = 0.0

    @property
    def verdict_type(self) -> str:
        if self.status != "COMPLETE":
            return "ERROR"
        return "KARENPASS" if self.passed else "KARENFAIL"

    @property
    def key(self) -> str:
        """Unique key for this verdict: owner/repo#pr@sha."""
        return f"{self.owner}/{self.repo}#{self.pr_number}@{self.head_sha[:12]}"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "Verdict":
        return cls(**d)

    @classmethod
    def from_json(cls, s: str) -> "Verdict":
        return cls.from_dict(json.loads(s))


def _verdict_filename(owner: str, repo: str, pr_number: int, sha: str) -> str:
    """Generate a filename for a verdict.

    Format: {owner}_{repo}_{pr}_{sha12}_{verdict}.json
    """
    sha12 = sha[:12]
    safe_owner = owner.replace("/", "_").replace("\\", "_")
    safe_repo = repo.replace("/", "_").replace("\\", "_")
    return f"{safe_owner}_{safe_repo}_{pr_number}_{sha12}"


def save_verdict(verdict: Verdict, config: KarenConfig) -> Path:
    """Save a verdict to the verdict directory.

    Returns the path to the saved file.
    """
    if verdict.status != "COMPLETE" or verdict.reason.strip().lower() in ("", "unknown"):
        raise ValueError("An error or unknown reason is not a verdict")
    verdict_dir = config.get_verdict_dir()
    verdict_dir.mkdir(parents=True, exist_ok=True)

    base_name = _verdict_filename(
        verdict.owner, verdict.repo, verdict.pr_number, verdict.head_sha
    )
    filename = f"{base_name}_{verdict.verdict_type}.json"
    path = verdict_dir / filename

    import tempfile
    fd, temp = tempfile.mkstemp(dir=str(verdict_dir), prefix=".verdict-")
    with os.fdopen(fd, "w") as stream:
        stream.write(verdict.to_json())
    os.replace(temp, str(path))

    symlink_name = f"{base_name}_LATEST.json"
    symlink_path = verdict_dir / symlink_name
    if symlink_path.is_symlink() or symlink_path.exists():
        symlink_path.unlink()
    symlink_path.symlink_to(filename)

    return path


def load_verdict(
    owner: str,
    repo: str,
    pr_number: int,
    sha: str,
    config: KarenConfig,
) -> Optional[Verdict]:
    """Load a verdict for a specific SHA.

    Returns None if no verdict exists for this exact SHA.
    """
    verdict_dir = config.get_verdict_dir()
    base_name = _verdict_filename(owner, repo, pr_number, sha)

    candidates = []
    for verdict_type in ["KARENPASS", "KARENFAIL"]:
        path = verdict_dir / f"{base_name}_{verdict_type}.json"
        if path.is_file():
            try:
                candidate = Verdict.from_json(path.read_text())
                if (candidate.head_sha == sha and candidate.owner == owner
                        and candidate.repo == repo and candidate.pr_number == pr_number
                        and candidate.status == "COMPLETE"):
                    candidates.append(candidate)
            except (json.JSONDecodeError, TypeError, KeyError):
                pass

    return max(candidates, key=lambda v: v.timestamp) if candidates else None


def load_latest_verdict(
    owner: str,
    repo: str,
    pr_number: int,
    config: KarenConfig,
) -> Optional[Verdict]:
    """Load the most recent verdict for a PR (any SHA).

    Returns None if no verdict exists.
    """
    verdict_dir = config.get_verdict_dir()
    base_pattern = f"{owner.replace('/', '_')}_{repo.replace('/', '_')}_{pr_number}_"

    latest_path = None
    latest_time = 0.0

    if verdict_dir.is_dir():
        for path in verdict_dir.glob(f"{base_pattern}*_KAREN*.json"):
            if path.name.endswith("_LATEST.json"):
                continue
            try:
                mtime = path.stat().st_mtime
                if mtime > latest_time:
                    latest_time = mtime
                    latest_path = path
            except OSError:
                pass

    if latest_path:
        try:
            return Verdict.from_json(latest_path.read_text())
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    return None


def is_verdict_stale(verdict: Verdict, current_sha: str) -> bool:
    """Check if a verdict is stale (SHA doesn't match)."""
    return verdict.head_sha != current_sha


def create_verdict(
    pr: PRInfo,
    check_results: list[CheckResult],
    review_results: list[ReviewResult],
    final_passed: bool,
    final_reason: str,
    final_reviewer: str,
) -> Verdict:
    """Create a verdict from check and review results."""
    total_cost = sum(r.cost_usd for r in review_results)
    total_duration = sum(r.duration_sec for r in review_results)

    tier = "unknown"
    confidence = 1.0
    if review_results:
        final_review = review_results[-1]
        tier = final_review.tier.value
        confidence = final_review.confidence

    return Verdict(
        owner=pr.owner,
        repo=pr.repo,
        pr_number=pr.number,
        head_sha=pr.head_sha,
        passed=final_passed,
        reason=final_reason,
        timestamp=datetime.now(timezone.utc).isoformat(),
        reviewer=final_reviewer,
        tier=tier,
        confidence=confidence,
        check_results=[
            {
                "check": r.check_name,
                "verdict": r.verdict.value,
                "reason": r.reason,
                "details": r.details,
            }
            for r in check_results
        ],
        review_results=[
            {
                "reviewer": r.reviewer,
                "verdict": r.verdict.value,
                "reason": r.reason,
                "confidence": r.confidence,
                "cost_usd": r.cost_usd,
                "duration_sec": r.duration_sec,
                "findings": r.findings.to_dict() if r.findings else {},
                "model": r.model,
                "effort": r.effort,
            }
            for r in review_results
        ],
        budget_used_usd=total_cost,
        duration_sec=total_duration,
    )


def format_verdict_comment(verdict: Verdict) -> str:
    """Format a verdict as a GitHub PR comment."""
    emoji = "✅" if verdict.passed else "❌"
    header = f"## {emoji} {verdict.verdict_type}\n\n"

    meta = f"""**PR:** {verdict.owner}/{verdict.repo}#{verdict.pr_number}
**SHA:** `{verdict.head_sha[:12]}`
**Reviewer:** {verdict.reviewer}
**Tier:** {verdict.tier}
**Confidence:** {verdict.confidence:.0%}
**Time:** {verdict.timestamp}

"""

    reason = f"**Reason:** {verdict.reason}\n\n"

    checks_section = "### Deterministic Checks\n\n"
    for check in verdict.check_results:
        check_emoji = "✅" if check["verdict"] == "PASS" else "❌" if check["verdict"] == "FAIL" else "⏭️"
        checks_section += f"- {check_emoji} **{check['check']}**: {check['reason']}\n"
        if check.get("details"):
            checks_section += f"  ```\n  {check['details'][:200]}\n  ```\n"

    reviews_section = "\n### LLM Reviews\n\n"
    for review in verdict.review_results:
        review_emoji = "✅" if review["verdict"] == "PASS" else "❌" if review["verdict"] == "FAIL" else "⚠️"
        reviews_section += f"- {review_emoji} **{review['reviewer']}**: {review['reason']}\n"
        reviews_section += f"  - Confidence: {review['confidence']:.0%}, Cost: ${review['cost_usd']:.4f}, Time: {review['duration_sec']:.1f}s\n"

    footer = f"""
---
*Local Karen gate • Budget used: ${verdict.budget_used_usd:.4f} • Total time: {verdict.duration_sec:.1f}s*
"""

    return header + meta + reason + checks_section + reviews_section + footer
