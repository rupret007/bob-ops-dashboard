"""Shared deterministic-check values; checks.py preserves public imports."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Any, Dict, Tuple


class CheckVerdict(Enum):
    """Verdict from a single check."""
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    ERROR = "ERROR"



@dataclass
class CheckResult:
    """Result of a deterministic check."""
    check_name: str
    verdict: CheckVerdict
    reason: str
    details: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.verdict == CheckVerdict.PASS

    @property
    def failed(self) -> bool:
        return self.verdict == CheckVerdict.FAIL



@dataclass(frozen=True)
class ReviewScope:
    """Server-derived pinned plan, never decoded from a PR body."""
    data: Dict[str, Any]



@dataclass(frozen=True)
class ReviewMaterial:
    """Already validated cumulative evidence packet, distinct from PR text."""
    entries: Tuple[Dict[str, Any], ...]
    phase: str



@dataclass
class PRInfo:
    """Information about a PR needed for checks."""
    owner: str
    repo: str
    number: int
    head_sha: str
    title: str
    author: str
    is_draft: bool
    diff: str
    ci_status: dict  # Dict[str, str]
    changed_files: list  # List[str]
    deleted_files: list  # List[str]
    description: str = ""  # PR body/description
    review_scope: Optional[ReviewScope] = None
    review_material: Optional[ReviewMaterial] = None
