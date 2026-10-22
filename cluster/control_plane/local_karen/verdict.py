"""Verdict file generation for Local Karen gate.

Verdicts are keyed to repo+pr+sha and stored as KARENPASS or KARENFAIL files.
A verdict for a stale SHA is invalid.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
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
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(verdict.to_json())
        os.replace(temp, str(path))
    finally:
        Path(temp).unlink(missing_ok=True)

    symlink_name = f"{base_name}_LATEST.json"
    symlink_path = verdict_dir / symlink_name
    # Readers must always see either the previous complete marker or the new
    # one. Never unlink LATEST: a crash in that gap strands a completed review.
    temporary_link = verdict_dir / (".verdict-latest-" + uuid.uuid4().hex)
    try:
        temporary_link.symlink_to(filename)
        os.replace(str(temporary_link), str(symlink_path))
    finally:
        temporary_link.unlink(missing_ok=True)

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
    """Load the most recent published verdict for a PR (any SHA).

    Embedded observation times survive copied files and coarse filesystem clocks.
    This reporting lookup never substitutes for an exact-head authority check.
    """
    verdict_dir = config.get_verdict_dir()
    base_pattern = f"{owner.replace('/', '_')}_{repo.replace('/', '_')}_{pr_number}_"

    def read(path):
        try:
            if path.resolve(strict=True).parent != verdict_dir.resolve():
                return None
            value = Verdict.from_json(path.read_text())
            if (value.owner != owner or value.repo != repo or value.pr_number != pr_number
                    or value.status != "COMPLETE" or type(value.passed) is not bool
                    or not isinstance(value.head_sha, str) or not value.head_sha
                    or not isinstance(value.timestamp, str)):
                return None
            base = _verdict_filename(owner, repo, pr_number, value.head_sha)
            if path.name not in (base + '_LATEST.json', base + '_KARENFAIL.json'):
                return None
            if path.is_symlink() and path.resolve().name != base + '_' + value.verdict_type + '.json':
                return None
            stamp = datetime.fromisoformat(value.timestamp.replace('Z', '+00:00'))
            return (stamp, value) if stamp.tzinfo is not None else None
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            return None

    candidates = []
    for marker in verdict_dir.glob(f"{base_pattern}*_LATEST.json"):
        published = read(marker)
        if published is None:
            continue
        candidates.append(published)
        # A failed pointer update cannot hide a newer blocking artifact, nor
        # can an unpublished PASS sibling supersede the published decision.
        failed = read(marker.with_name(marker.name.replace('_LATEST.json', '_KARENFAIL.json')))
        if (failed and failed[1].head_sha == published[1].head_sha
                and not failed[1].passed and failed[0] >= published[0]):
            candidates.append(failed)
    return max(candidates, key=lambda item: (item[0], not item[1].passed,
                                           item[1].head_sha))[1] if candidates else None


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
                **({"scope": r.scope} if r.scope is not None else {}),
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
