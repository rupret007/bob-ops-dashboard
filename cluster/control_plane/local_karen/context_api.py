"""Provider-independent types, grounding and bindings for bounded review context."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import logging
import re
from typing import Any, Dict, List, Optional

from .check_types import ReviewScope, ReviewMaterial

logger = logging.getLogger("cluster.control_plane.local_karen.reviewers")


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



def _diff_line_locations(pr):
    """Resolve evidence only against real hunk lines of the pinned supplied diff."""
    import shlex
    locations = set()
    path = None
    old_line = new_line = None
    for line in pr.diff.splitlines():
        if line.startswith("diff --git "):
            path = None
            old_line = new_line = None
        elif line.startswith("@@ "):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if match:
                old_line, new_line = int(match.group(1)), int(match.group(2))
        elif path and new_line is not None:
            if line.startswith("+"):
                locations.add((path, new_line))
                new_line += 1
            elif line.startswith("-"):
                locations.add((path, old_line))
                old_line += 1
            elif line.startswith(" "):
                locations.add((path, new_line))
                old_line += 1
                new_line += 1
        elif line.startswith(("+++ ", "--- ")):
            try:
                raw = line[4:]
                value = shlex.split(raw)[0] if raw.startswith('"') else raw
            except (ValueError, IndexError):
                continue
            if value.startswith("b/") or (path is None and value.startswith("a/")):
                path = value[2:]
    return locations



def _grounded_chunk_failure(result, original_pr):
    """A missing file or unsupported rejection cannot become an actionable FAIL."""
    if result.requested_files or result.requested_source_files:
        return False
    findings = result.findings
    if not findings:
        return False
    defects = (findings.security_issues + findings.functional_issues +
               findings.test_coverage_issues + findings.code_quality_issues)
    locations = _diff_line_locations(original_pr)
    for defect in defects:
        if not isinstance(defect, str):
            continue
        # Context absence is uncertainty even if a reviewer attaches a citation.
        if re.search(r"(?i)(?:insufficient|unavailable) (?:review )?context|"
                     r"missing (?:review|PR|cross-file) context|"
                     r"(?:omitted|absent|missing) from (?:this |the )?(?:chunk|diff)|"
                     r"(?:context|implementation|installer|test suite|tests|files?|diff)"
                     r"(?: (?:is|are|was|were))? "
                     r"(?:not provided|not included|not visible|not shown|not present)|"
                     r"(?:only (?:contains?|includes?)|(?:contains?|includes?) only)"
                     r".{0,50}(?:docs?|documentation|incident)|"
                     r"(?:implemented|provided|included|defined|located|available) "
                     r"(?:elsewhere|in (?:another|other) chunks?)|"
                     r"(?:cannot|can't|unable to) (?:verify|assess|review)", defect):
            continue
        for match in re.finditer(r"([A-Za-z0-9_./-]+)(?::|#L)([1-9][0-9]*)", defect):
            if (match.group(1), int(match.group(2))) in locations:
                return True
    return False



def _validate_chunk_result(result, original_pr):
    if original_pr is None and not (result.requested_files or result.requested_source_files):
        return result
    if result.requested_files or result.requested_source_files or (result.verdict in (ReviewVerdict.FAIL, ReviewVerdict.BORDERLINE)
                                   and not _grounded_chunk_failure(result, original_pr)):
        scope = dict(result.scope or {})
        scope["context_incomplete"] = True
        return replace(result, verdict=ReviewVerdict.BORDERLINE, scope=scope,
                       reason="Context or evidence incomplete; retry required: " + result.reason)
    return result


class ContextOwner:
    """Only the canonical context API; no provider implementation dependency."""
    ReviewScope, ReviewMaterial = ReviewScope, ReviewMaterial
    ReviewVerdict = ReviewVerdict
    replace = staticmethod(replace)
    logger = logger
    _validate_chunk_result = staticmethod(_validate_chunk_result)

    def __init__(self, context_part=None):
        # Preserve the public facade's observable packet-construction callback.
        self._context_part = context_part if context_part is not None else self._part

    def _part(self, part, entries, label):
        from . import review_context
        return review_context._context_part(self, part, entries, label)

    def _context_resolution(self, result, code, counts):
        from . import review_context
        return review_context._context_resolution(self, result, code, counts)


def _review_with_requested_context(invoke, part, original_pr, source_context=None,
                                   initial_context=None, *, context_part=None):
    from . import review_context
    return review_context._review_with_requested_context(
        ContextOwner(context_part), invoke, part, original_pr, source_context, initial_context)
