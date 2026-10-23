"""Shared review results with no provider implementation dependency."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ReviewTier(Enum):
    """Risk tier for a PR."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    SECURITY = "security"


class ReviewVerdict(Enum):
    """Verdict from a reviewer."""
    PASS = "PASS"
    FAIL = "FAIL"
    BORDERLINE = "BORDERLINE"
    ERROR = "ERROR"


@dataclass
class ReviewFindings:
    """Structured findings from a review."""
    security_issues: List[str] = field(default_factory=list)
    functional_issues: List[str] = field(default_factory=list)
    test_coverage_issues: List[str] = field(default_factory=list)
    code_quality_issues: List[str] = field(default_factory=list)
    risk_notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "security_issues": self.security_issues,
            "functional_issues": self.functional_issues,
            "test_coverage_issues": self.test_coverage_issues,
            "code_quality_issues": self.code_quality_issues,
            "risk_notes": self.risk_notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReviewFindings":
        return cls(
            security_issues=data.get("security_issues", []),
            functional_issues=data.get("functional_issues", []),
            test_coverage_issues=data.get("test_coverage_issues", []),
            code_quality_issues=data.get("code_quality_issues", []),
            risk_notes=data.get("risk_notes", []),
        )

    def has_content(self) -> bool:
        """Check if findings have any meaningful content."""
        return bool(
            self.security_issues or
            self.functional_issues or
            self.test_coverage_issues or
            self.code_quality_issues or
            self.risk_notes
        )


@dataclass
class ReviewResult:
    """Result of an LLM review."""
    reviewer: str
    verdict: ReviewVerdict
    reason: str
    confidence: float = 1.0
    tier: ReviewTier = ReviewTier.MEDIUM
    cost_usd: float = 0.0
    duration_sec: float = 0.0
    raw_response: str = ""
    findings: Optional[ReviewFindings] = None
    key_source: str = ""
    model: str = ""
    effort: str = ""
    scope: Optional[dict] = None
    requested_files: List[str] = field(default_factory=list)
    requested_source_files: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == ReviewVerdict.PASS

    @property
    def failed(self) -> bool:
        return self.verdict == ReviewVerdict.FAIL

    @property
    def needs_second_judge(self) -> bool:
        return self.verdict == ReviewVerdict.BORDERLINE or self.tier in (
            ReviewTier.HIGH, ReviewTier.SECURITY
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for verdict file storage."""
        d = {
            "reviewer": self.reviewer,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "confidence": self.confidence,
            "tier": self.tier.value,
            "cost_usd": self.cost_usd,
            "duration_sec": self.duration_sec,
            "raw_response": self.raw_response,
            "key_source": self.key_source,
        }
        if self.requested_files:
            d["requested_files"] = self.requested_files
        if self.requested_source_files:
            d["requested_source_files"] = self.requested_source_files
        if self.scope is not None:
            d["scope"] = self.scope
        if self.findings:
            d["findings"] = self.findings.to_dict()
        return d
