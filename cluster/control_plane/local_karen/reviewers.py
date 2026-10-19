"""LLM reviewers for Local Karen gate.

Reviewer order (configurable):
1. Codex CLI (primary) - via CODEX_HOST=nuc, low effort default, high for security
2. Gemini API (fallback) - prepaid
3. MiniMax (second judge) - borderline/high-risk tiers
4. Claude (last resort) - off by default, capped

NEVER read or copy auth files. Use existing sign-ins only.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any, Type

from .config import KarenConfig
from .checks import PRInfo


logger = logging.getLogger(__name__)


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
        if self.findings:
            d["findings"] = self.findings.to_dict()
        return d


STRUCTURED_REVIEW_PROMPT = """You are Karen, a strict QA and security gate for {owner}/{repo}.

PR #{pr_number}: {title}
Author: {author}
Head SHA: {sha}

## PR Description
{pr_description}

## Changed files
{changed_files}

## Full Diff ({diff_len} chars)
{diff}

## Your task

Review this PR for:
1. **Security vulnerabilities** — injection, bypassed identity checks, hardcoded secrets, unsafe deserialization
2. **Functional correctness** — obvious bugs, unhandled edge cases, race conditions
3. **Test coverage** — are new code paths tested? Were tests weakened?
4. **Code quality** — dead code, obvious logic errors

## Rules (NEVER BREAK)
- You CANNOT approve removing or weakening tests
- You CANNOT approve PRs that add secrets to code
- You CANNOT approve PRs that use privileged operations (including `sudo`)
- If borderline, lean toward FAIL with a clear reason
- Break functional and security claims BEFORE approval

## Response format (STRICTLY JSON)

You MUST respond with ONLY a valid JSON object in this exact format:
{{
  "verdict": "PASS" | "FAIL" | "BORDERLINE",
  "reason": "One sentence summary of your decision",
  "confidence": 0.0 to 1.0,
  "findings": {{
    "security_issues": ["list of security concerns found, or empty"],
    "functional_issues": ["list of bugs/logic errors found, or empty"],
    "test_coverage_issues": ["list of test concerns, or empty"],
    "code_quality_issues": ["list of quality issues, or empty"],
    "risk_notes": ["any other risk factors to note"]
  }}
}}

IMPORTANT:
- For PASS verdicts, you MUST have at least one item in findings explaining what you checked
- confidence should reflect actual certainty, not always 1.0
- If unsure, use BORDERLINE with lower confidence

Your JSON response:"""


MIN_REASON_LENGTH = 20
MIN_FINDINGS_FOR_PASS = 1


def classify_risk_tier(pr: PRInfo) -> ReviewTier:
    """Classify the risk tier of a PR based on its contents."""
    high_risk_patterns = [
        "auth", "login", "password", "secret", "token", "credential",
        "encrypt", "decrypt", "hash", "sign", "verify",
        "sql", "query", "database", "migration",
        "permission", "role", "admin", "sudo",
        "payment", "billing", "credit", "charge",
        "api", "endpoint", "route", "webhook",
    ]

    security_patterns = [
        "security", "vulnerability", "cve", "exploit",
        "injection", "xss", "csrf", "ssrf",
        "certificate", "tls", "ssl", "oauth",
    ]

    diff_lower = pr.diff.lower()
    title_lower = pr.title.lower()
    files_lower = " ".join(pr.changed_files).lower()
    combined = "{} {} {}".format(diff_lower, title_lower, files_lower)

    for pattern in security_patterns:
        if pattern in combined:
            return ReviewTier.SECURITY

    risk_count = sum(1 for p in high_risk_patterns if p in combined)
    if risk_count >= 3:
        return ReviewTier.HIGH
    elif risk_count >= 1:
        return ReviewTier.MEDIUM

    if len(pr.changed_files) > 20 or len(pr.diff) > 50000:
        return ReviewTier.HIGH

    return ReviewTier.LOW


class BaseReviewer(ABC):
    """Abstract base class for LLM reviewers."""

    name = ""  # type: str

    @abstractmethod
    def is_available(self, config: KarenConfig) -> Tuple[bool, str]:
        """Check if this reviewer is available."""
        ...

    @abstractmethod
    def review(self, pr: PRInfo, tier: ReviewTier, config: KarenConfig) -> ReviewResult:
        """Perform the review."""
        ...

    def _parse_structured_verdict(
        self,
        response: str,
        pr: PRInfo,
        tier: ReviewTier,
        key_source: str = "",
    ) -> ReviewResult:
        """Parse a structured JSON verdict from an LLM response."""
        response = response.strip()

        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        response = response.strip()

        try:
            data = json.loads(response)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse JSON response: %s", e)
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.FAIL,
                reason="FAIL-CLOSED: Response was not valid JSON - cannot trust unstructured review",
                confidence=0.0,
                tier=tier,
                raw_response=response,
                key_source=key_source,
            )

        verdict_str = data.get("verdict", "").upper()
        reason = data.get("reason", "")
        confidence = data.get("confidence", 1.0)
        findings_data = data.get("findings", {})

        if verdict_str not in ("PASS", "FAIL", "BORDERLINE"):
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.FAIL,
                reason="FAIL-CLOSED: Invalid verdict '{}' in response".format(verdict_str),
                confidence=0.0,
                tier=tier,
                raw_response=response,
                key_source=key_source,
            )

        if len(reason) < MIN_REASON_LENGTH:
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.FAIL,
                reason="FAIL-CLOSED: Reason too short ({} chars) - insufficient justification".format(len(reason)),
                confidence=0.0,
                tier=tier,
                raw_response=response,
                key_source=key_source,
            )

        findings = ReviewFindings.from_dict(findings_data)

        if verdict_str == "PASS" and not findings.has_content():
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.FAIL,
                reason="FAIL-CLOSED: PASS verdict without findings - rubber-stamp rejected",
                confidence=0.0,
                tier=tier,
                raw_response=response,
                findings=findings,
                key_source=key_source,
            )

        if confidence == 1.0 and verdict_str == "PASS":
            confidence = 0.85
            logger.info("Clamped 1.0 confidence to 0.85 for PASS verdict")

        verdict_map = {
            "PASS": ReviewVerdict.PASS,
            "FAIL": ReviewVerdict.FAIL,
            "BORDERLINE": ReviewVerdict.BORDERLINE,
        }

        return ReviewResult(
            reviewer=self.name,
            verdict=verdict_map[verdict_str],
            reason=reason,
            confidence=confidence,
            tier=tier,
            raw_response=response,
            findings=findings,
            key_source=key_source,
        )

    def _parse_verdict(self, response: str, pr: PRInfo, tier: ReviewTier) -> ReviewResult:
        """Parse a verdict from an LLM response (legacy unstructured format)."""
        response_upper = response.upper()

        if response_upper.startswith("PASS:"):
            reason = response.split(":", 1)[1].strip() if ":" in response else "Approved"
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.PASS,
                reason=reason,
                tier=tier,
                raw_response=response,
            )
        elif response_upper.startswith("FAIL:"):
            reason = response.split(":", 1)[1].strip() if ":" in response else "Rejected"
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.FAIL,
                reason=reason,
                tier=tier,
                raw_response=response,
            )
        elif response_upper.startswith("BORDERLINE:"):
            reason = response.split(":", 1)[1].strip() if ":" in response else "Needs review"
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.BORDERLINE,
                reason=reason,
                confidence=0.5,
                tier=tier,
                raw_response=response,
            )
        else:
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.BORDERLINE,
                reason="Could not parse verdict from response: {}".format(response[:100]),
                confidence=0.3,
                tier=tier,
                raw_response=response,
            )


class CodexReviewer(BaseReviewer):
    """Codex CLI reviewer.

    Primary reviewer using Jeff's $200 Codex plan.
    Runs at low effort by default, high effort for security/high-risk.
    Stops at 75% weekly usage.

    When codex_host is set (e.g., "nuc"), attempts to run on that host via SSH.
    Falls back to local execution if SSH fails.
    """

    name = "codex"

    def is_available(self, config: KarenConfig) -> Tuple[bool, str]:
        """Check if Codex CLI is available and within budget."""
        if not self._codex_available_locally():
            if config.codex_host and config.codex_host != "local":
                if not self._can_ssh_to_host(config.codex_host):
                    return False, "codex CLI not local and SSH to {} failed".format(config.codex_host)
            else:
                return False, "codex CLI not found in PATH"

        usage_pct = self._get_weekly_usage_pct(config)
        if usage_pct >= config.codex_weekly_cap_pct:
            return False, "Codex at {:.1f}% weekly usage (cap: {}%)".format(usage_pct, config.codex_weekly_cap_pct)

        return True, "ok"

    def _codex_available_locally(self) -> bool:
        """Check if codex CLI is available locally."""
        try:
            result = subprocess.run(
                ["which", "codex"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _can_ssh_to_host(self, host: str) -> bool:
        """Check if we can SSH to a remote host."""
        try:
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "which", "codex"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _get_weekly_usage_pct(self, config: KarenConfig) -> float:
        """Get current weekly Codex usage percentage."""
        state_file = config.get_state_dir() / "codex_weekly_usage.json"
        if not state_file.is_file():
            return 0.0

        try:
            data = json.loads(state_file.read_text())
            week_start = self._week_start_ts()
            if data.get("week_start", 0) < week_start:
                return 0.0
            return data.get("usage_pct", 0.0)
        except (json.JSONDecodeError, KeyError):
            return 0.0

    def _week_start_ts(self) -> float:
        """Get timestamp of Monday 00:00 UTC this week."""
        now = datetime.now(timezone.utc)
        days_since_monday = now.weekday()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = start.replace(day=start.day - days_since_monday)
        return start.timestamp()

    def _record_usage(self, config: KarenConfig, added_pct: float) -> None:
        """Record Codex usage."""
        state_dir = config.get_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "codex_weekly_usage.json"

        current_pct = self._get_weekly_usage_pct(config)
        new_pct = current_pct + added_pct

        data = {
            "week_start": self._week_start_ts(),
            "usage_pct": new_pct,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        state_file.write_text(json.dumps(data, indent=2))

    def _build_codex_args(self, effort: str) -> List[str]:
        """Build the codex exec command arguments (prompt via stdin).

        Uses current codex CLI flags:
        - -c model_reasoning_effort="low"|"high" (NOT --effort)
        - --sandbox read-only
        - --skip-git-repo-check (no git repo needed)
        - -o (--output-last-message) for clean output
        - '-' to read prompt from stdin
        """
        return [
            "codex", "exec",
            "-c", 'model_reasoning_effort="{}"'.format(effort),
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "-o",
            "-",
        ]

    def review(self, pr: PRInfo, tier: ReviewTier, config: KarenConfig) -> ReviewResult:
        """Run Codex review via CLI."""
        start_time = time.time()

        effort = config.codex_effort_high_risk if tier in (
            ReviewTier.HIGH, ReviewTier.SECURITY
        ) else config.codex_effort_default

        pr_description = getattr(pr, 'description', '') or getattr(pr, 'body', '') or '(no description)'

        prompt = STRUCTURED_REVIEW_PROMPT.format(
            owner=pr.owner,
            repo=pr.repo,
            pr_number=pr.number,
            title=pr.title,
            author=pr.author,
            sha=pr.head_sha[:12],
            pr_description=pr_description[:2000],
            changed_files="\n".join("- {}".format(f) for f in pr.changed_files[:30]),
            diff=pr.diff[:15000],
            diff_len=min(len(pr.diff), 15000),
        )

        run_on_host = None
        use_ssh = False

        if config.codex_host and config.codex_host not in ("local", ""):
            if self._can_ssh_to_host(config.codex_host):
                use_ssh = True
                run_on_host = config.codex_host
            elif self._codex_available_locally():
                pass
            else:
                return ReviewResult(
                    reviewer=self.name,
                    verdict=ReviewVerdict.ERROR,
                    reason="Cannot reach {} via SSH and no local codex".format(config.codex_host),
                    tier=tier,
                    duration_sec=time.time() - start_time,
                )

        codex_args = self._build_codex_args(effort)

        try:
            if use_ssh:
                ssh_codex_args = [
                    "codex", "exec",
                    "-c", 'model_reasoning_effort="{}"'.format(effort),
                    "--sandbox", "read-only",
                    "--skip-git-repo-check",
                    "-o",
                    "-",
                ]
                full_cmd = ["ssh", "-o", "ConnectTimeout=30", run_on_host] + ssh_codex_args

                result = subprocess.run(
                    full_cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
            else:
                result = subprocess.run(
                    codex_args,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

            duration = time.time() - start_time

            if result.returncode != 0:
                return ReviewResult(
                    reviewer=self.name,
                    verdict=ReviewVerdict.ERROR,
                    reason="Codex CLI failed: {}".format(result.stderr[:200]),
                    tier=tier,
                    duration_sec=duration,
                    raw_response=result.stderr,
                )

            response = result.stdout.strip()
            review_result = self._parse_structured_verdict(response, pr, tier)
            review_result.duration_sec = duration

            self._record_usage(config, 0.5 if effort == "high" else 0.2)

            return review_result

        except subprocess.TimeoutExpired:
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.ERROR,
                reason="Codex CLI timed out after 5 minutes",
                tier=tier,
                duration_sec=300,
            )
        except Exception as e:
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.ERROR,
                reason="Codex CLI error: {}".format(e),
                tier=tier,
                duration_sec=time.time() - start_time,
            )


class GeminiReviewer(BaseReviewer):
    """Gemini API reviewer (prepaid fallback).

    Uses v1beta generateContent API with configurable model.
    Default model: gemini-2.5-flash (fast, cheap)
    """

    name = "gemini"

    DEFAULT_MODEL = "gemini-2.5-flash"

    def is_available(self, config: KarenConfig) -> Tuple[bool, str]:
        """Check if Gemini API key is configured.

        Missing key file is a SKIP (not an error) - skips to next reviewer
        WITHOUT making any HTTP call.
        """
        key_file = Path(config.gemini_api_key_file).expanduser()
        env_key = os.environ.get("GEMINI_API_KEY", "")

        if not key_file.is_file() and not env_key:
            return False, "SKIP: no Gemini key (missing {} and no GEMINI_API_KEY env)".format(key_file)

        key = self._load_key(config)
        if not key:
            return False, "SKIP: Gemini API key file exists but is empty"

        return True, "ok"

    def _load_key(self, config: KarenConfig) -> str:
        """Load Gemini API key from file or environment."""
        key_file = Path(config.gemini_api_key_file).expanduser()
        if key_file.is_file():
            return key_file.read_text().strip()
        return os.environ.get("GEMINI_API_KEY", "")

    def _get_key_source(self, config: KarenConfig) -> str:
        """Get the source of the Gemini API key (for logging, never the value)."""
        key_file = Path(config.gemini_api_key_file).expanduser()
        if key_file.is_file():
            return "file:{}".format(key_file)
        if os.environ.get("GEMINI_API_KEY", ""):
            return "env:GEMINI_API_KEY"
        return "none"

    def _get_model(self, config: KarenConfig) -> str:
        """Get the Gemini model to use."""
        return getattr(config, "gemini_model", None) or self.DEFAULT_MODEL

    def review(self, pr: PRInfo, tier: ReviewTier, config: KarenConfig) -> ReviewResult:
        """Run Gemini review via API.

        IMPORTANT: is_available() MUST be called first to check for key.
        This method assumes the key exists.
        """
        start_time = time.time()

        key_source = self._get_key_source(config)
        logger.info("Gemini using key from: %s", key_source)

        api_key = self._load_key(config)
        if not api_key:
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.ERROR,
                reason="Gemini API key not available (should have been caught by is_available)",
                tier=tier,
                key_source=key_source,
            )

        model = self._get_model(config)

        pr_description = getattr(pr, 'description', '') or getattr(pr, 'body', '') or '(no description)'

        prompt = STRUCTURED_REVIEW_PROMPT.format(
            owner=pr.owner,
            repo=pr.repo,
            pr_number=pr.number,
            title=pr.title,
            author=pr.author,
            sha=pr.head_sha[:12],
            pr_description=pr_description[:2000],
            changed_files="\n".join("- {}".format(f) for f in pr.changed_files[:30]),
            diff=pr.diff[:12000],
            diff_len=min(len(pr.diff), 12000),
        )

        try:
            import urllib.request
            import urllib.error

            url = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent?key={}".format(model, api_key)

            payload = json.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": 500,
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                },
            }).encode("utf-8")

            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            duration = time.time() - start_time

            response_text = result.get("candidates", [{}])[0].get(
                "content", {}
            ).get("parts", [{}])[0].get("text", "")

            if not response_text:
                return ReviewResult(
                    reviewer=self.name,
                    verdict=ReviewVerdict.ERROR,
                    reason="Empty response from Gemini",
                    tier=tier,
                    duration_sec=duration,
                    raw_response=json.dumps(result),
                    key_source=key_source,
                )

            review_result = self._parse_structured_verdict(response_text, pr, tier, key_source)
            review_result.duration_sec = duration
            review_result.cost_usd = 0.001

            return review_result

        except urllib.error.HTTPError as e:
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.ERROR,
                reason="Gemini API error: {} {}".format(e.code, e.reason),
                tier=tier,
                duration_sec=time.time() - start_time,
                key_source=key_source,
            )
        except Exception as e:
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.ERROR,
                reason="Gemini error: {}".format(e),
                tier=tier,
                duration_sec=time.time() - start_time,
                key_source=key_source,
            )


class MiniMaxReviewer(BaseReviewer):
    """MiniMax reviewer for second judge on borderline/high-risk."""

    name = "minimax"

    SECOND_JUDGE_PROMPT = """The primary reviewer (Karen) gave a {verdict} verdict on this PR.

Karen's reason: {reason}
Karen's findings: {findings}

PR #{pr_number}: {title}
Author: {author}
Changed files: {file_count}

Diff summary (first 5000 chars):
{diff_summary}

As a second judge, do you agree with Karen's assessment?
If Karen said BORDERLINE or FAIL, should this PR actually pass?
If Karen said PASS, are there concerns she missed?

Respond with ONLY a valid JSON object:
{{
  "decision": "AGREE" | "OVERRIDE_PASS" | "OVERRIDE_FAIL",
  "reason": "One sentence explanation",
  "additional_findings": ["any issues Karen missed"]
}}
"""

    def is_available(self, config: KarenConfig) -> Tuple[bool, str]:
        """Check if MiniMax API key is configured.

        Missing key file is a skip (not an error) - second judge is optional.
        """
        key_file = Path(config.minimax_api_key_file).expanduser()
        if not key_file.is_file():
            env_key = os.environ.get("MINIMAX_API_KEY", "")
            if not env_key:
                return False, "MiniMax API key not found (no {} or MINIMAX_API_KEY env)".format(key_file)
        key = self._load_key(config)
        if not key:
            return False, "MiniMax API key file exists but is empty"
        return True, "ok"

    def _load_key(self, config: KarenConfig) -> str:
        """Load MiniMax API key from file or environment."""
        key_file = Path(config.minimax_api_key_file).expanduser()
        if key_file.is_file():
            return key_file.read_text().strip()
        return os.environ.get("MINIMAX_API_KEY", "")

    def _get_key_source(self, config: KarenConfig) -> str:
        """Get the source of the MiniMax API key."""
        key_file = Path(config.minimax_api_key_file).expanduser()
        if key_file.is_file():
            return "file:{}".format(key_file)
        if os.environ.get("MINIMAX_API_KEY", ""):
            return "env:MINIMAX_API_KEY"
        return "none"

    def review_as_second_judge(
        self,
        pr: PRInfo,
        primary_result: ReviewResult,
        config: KarenConfig,
    ) -> ReviewResult:
        """Review as second judge after primary reviewer."""
        start_time = time.time()

        key_source = self._get_key_source(config)
        logger.info("MiniMax using key from: %s", key_source)

        api_key = self._load_key(config)
        if not api_key:
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.ERROR,
                reason="MiniMax API key not available",
                tier=primary_result.tier,
                key_source=key_source,
            )

        findings_str = ""
        if primary_result.findings:
            findings_str = json.dumps(primary_result.findings.to_dict(), indent=2)
        else:
            findings_str = "(no structured findings)"

        prompt = self.SECOND_JUDGE_PROMPT.format(
            verdict=primary_result.verdict.value,
            reason=primary_result.reason,
            findings=findings_str,
            pr_number=pr.number,
            title=pr.title,
            author=pr.author,
            file_count=len(pr.changed_files),
            diff_summary=pr.diff[:5000],
        )

        try:
            import urllib.request

            url = "https://api.minimax.chat/v1/text/chatcompletion_v2"

            payload = json.dumps({
                "model": "abab6.5s-chat",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.1,
            }).encode("utf-8")

            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer {}".format(api_key),
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            duration = time.time() - start_time

            response_text = result.get("choices", [{}])[0].get(
                "message", {}
            ).get("content", "")

            if not response_text:
                return ReviewResult(
                    reviewer=self.name,
                    verdict=ReviewVerdict.ERROR,
                    reason="Empty response from MiniMax",
                    tier=primary_result.tier,
                    duration_sec=duration,
                    key_source=key_source,
                )

            return self._parse_second_judge_verdict(
                response_text, pr, primary_result, duration, key_source
            )

        except Exception as e:
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.ERROR,
                reason="MiniMax error: {}".format(e),
                tier=primary_result.tier,
                duration_sec=time.time() - start_time,
                key_source=key_source,
            )

    def _parse_second_judge_verdict(
        self,
        response: str,
        pr: PRInfo,
        primary: ReviewResult,
        duration: float,
        key_source: str = "",
    ) -> ReviewResult:
        """Parse second judge verdict from JSON response."""
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        response = response.strip()

        try:
            data = json.loads(response)
            decision = data.get("decision", "").upper()
            reason = data.get("reason", "")
        except json.JSONDecodeError:
            decision = ""
            reason = response
            response_upper = response.upper()
            if "OVERRIDE_PASS" in response_upper:
                decision = "OVERRIDE_PASS"
            elif "OVERRIDE_FAIL" in response_upper:
                decision = "OVERRIDE_FAIL"
            elif "AGREE" in response_upper:
                decision = "AGREE"

        if decision == "OVERRIDE_PASS":
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.PASS,
                reason="Ken overrode Karen: {}".format(reason),
                confidence=0.7,
                tier=primary.tier,
                duration_sec=duration,
                raw_response=response,
                key_source=key_source,
            )
        elif decision == "OVERRIDE_FAIL":
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.FAIL,
                reason="Ken overrode Karen: {}".format(reason),
                confidence=0.7,
                tier=primary.tier,
                duration_sec=duration,
                raw_response=response,
                key_source=key_source,
            )
        elif decision == "AGREE":
            final_verdict = primary.verdict
            if final_verdict == ReviewVerdict.BORDERLINE:
                final_verdict = ReviewVerdict.FAIL
            return ReviewResult(
                reviewer=self.name,
                verdict=final_verdict,
                reason="Ken agreed with Karen: {}".format(reason),
                confidence=0.8,
                tier=primary.tier,
                duration_sec=duration,
                raw_response=response,
                key_source=key_source,
            )
        else:
            return ReviewResult(
                reviewer=self.name,
                verdict=primary.verdict if primary.verdict != ReviewVerdict.BORDERLINE else ReviewVerdict.FAIL,
                reason="Ken unclear, defaulting to Karen's verdict: {}".format(response[:100]),
                confidence=0.5,
                tier=primary.tier,
                duration_sec=duration,
                raw_response=response,
                key_source=key_source,
            )

    def review(self, pr: PRInfo, tier: ReviewTier, config: KarenConfig) -> ReviewResult:
        """MiniMax is only used as second judge, not primary."""
        return ReviewResult(
            reviewer=self.name,
            verdict=ReviewVerdict.ERROR,
            reason="MiniMax is only used as second judge, not primary reviewer",
            tier=tier,
        )


class ClaudeReviewer(BaseReviewer):
    """Claude reviewer (last resort, off by default, capped).

    Uses Claude Code CLI with existing sign-in.
    NEVER reads or copies auth files.
    """

    name = "claude"

    def is_available(self, config: KarenConfig) -> Tuple[bool, str]:
        """Check if Claude is enabled and within daily cap."""
        if not config.claude_enabled:
            return False, "Claude is disabled (set claude_enabled=true to enable)"

        daily_usage = self._get_daily_usage(config)
        if daily_usage >= config.claude_daily_cap:
            return False, "Claude at {}/{} daily cap".format(daily_usage, config.claude_daily_cap)

        try:
            result = subprocess.run(
                ["which", "claude"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return False, "claude CLI not found in PATH"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False, "claude CLI not accessible"

        return True, "ok"

    def _get_daily_usage(self, config: KarenConfig) -> int:
        """Get today's Claude usage count."""
        state_file = config.get_state_dir() / "claude_daily_usage.json"
        if not state_file.is_file():
            return 0

        try:
            data = json.loads(state_file.read_text())
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if data.get("date") != today:
                return 0
            return data.get("count", 0)
        except (json.JSONDecodeError, KeyError):
            return 0

    def _record_usage(self, config: KarenConfig) -> None:
        """Record Claude usage."""
        state_dir = config.get_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "claude_daily_usage.json"

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        current = self._get_daily_usage(config)

        data = {"date": today, "count": current + 1}
        state_file.write_text(json.dumps(data))

    def review(self, pr: PRInfo, tier: ReviewTier, config: KarenConfig) -> ReviewResult:
        """Run Claude review via CLI."""
        start_time = time.time()

        pr_description = getattr(pr, 'description', '') or getattr(pr, 'body', '') or '(no description)'

        prompt = STRUCTURED_REVIEW_PROMPT.format(
            owner=pr.owner,
            repo=pr.repo,
            pr_number=pr.number,
            title=pr.title,
            author=pr.author,
            sha=pr.head_sha[:12],
            pr_description=pr_description[:2000],
            changed_files="\n".join("- {}".format(f) for f in pr.changed_files[:30]),
            diff=pr.diff[:10000],
            diff_len=min(len(pr.diff), 10000),
        )

        try:
            result = subprocess.run(
                ["claude", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=180,
            )

            duration = time.time() - start_time

            if result.returncode != 0:
                return ReviewResult(
                    reviewer=self.name,
                    verdict=ReviewVerdict.ERROR,
                    reason="Claude CLI failed: {}".format(result.stderr[:200]),
                    tier=tier,
                    duration_sec=duration,
                    raw_response=result.stderr,
                )

            response = result.stdout.strip()
            review_result = self._parse_structured_verdict(response, pr, tier)
            review_result.duration_sec = duration

            self._record_usage(config)

            return review_result

        except subprocess.TimeoutExpired:
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.ERROR,
                reason="Claude CLI timed out after 3 minutes",
                tier=tier,
                duration_sec=180,
            )
        except Exception as e:
            return ReviewResult(
                reviewer=self.name,
                verdict=ReviewVerdict.ERROR,
                reason="Claude CLI error: {}".format(e),
                tier=tier,
                duration_sec=time.time() - start_time,
            )


REVIEWERS = {
    "codex": CodexReviewer,
    "gemini": GeminiReviewer,
    "minimax": MiniMaxReviewer,
    "claude": ClaudeReviewer,
}  # type: Dict[str, Type[BaseReviewer]]


def get_available_reviewers(config: KarenConfig) -> List[Tuple[str, BaseReviewer]]:
    """Get list of available reviewers in configured order."""
    available = []  # type: List[Tuple[str, BaseReviewer]]
    for name in config.reviewer_order:
        if name not in REVIEWERS:
            continue
        reviewer = REVIEWERS[name]()
        ok, reason = reviewer.is_available(config)
        if ok:
            available.append((name, reviewer))
    return available


def run_review_chain(
    pr,  # type: PRInfo
    config,  # type: KarenConfig
    require_two_pass=True,  # type: bool
):
    # type: (...) -> Tuple[ReviewResult, List[ReviewResult]]
    """Run the review chain and return final verdict plus all results.

    Returns (final_result, all_results).

    LIVE SAFETY REQUIREMENT (require_two_pass=True):
    Karen PASS requires TWO independent reviewer agreements with structured findings.
    This prevents rubber-stamp passes from a single model.

    The chain tries:
    1. Codex (primary) - if available
    2. Gemini (secondary) - if available
    3. MiniMax (confirming judge) - for PASS confirmation

    For a PASS verdict in live mode:
    - Need at least 2 reviewers to agree on PASS
    - Both must have structured findings (not empty)
    - If only one reviewer available, FAIL-CLOSED
    """
    tier = classify_risk_tier(pr)
    all_results = []  # type: List[ReviewResult]
    pass_votes = []  # type: List[ReviewResult]
    fail_votes = []  # type: List[ReviewResult]

    available = get_available_reviewers(config)
    if not available:
        return ReviewResult(
            reviewer="none",
            verdict=ReviewVerdict.ERROR,
            reason="No reviewers available",
            tier=tier,
        ), all_results

    for name, reviewer in available:
        if name == "minimax":
            continue

        result = reviewer.review(pr, tier, config)
        all_results.append(result)

        if result.verdict == ReviewVerdict.ERROR:
            logger.warning("Reviewer %s errored: %s", name, result.reason)
            continue

        if result.verdict == ReviewVerdict.PASS:
            if result.findings and result.findings.has_content():
                pass_votes.append(result)
                logger.info("Reviewer %s voted PASS with findings", name)
            else:
                logger.warning("Reviewer %s voted PASS but no structured findings - treating as FAIL", name)
                fail_votes.append(ReviewResult(
                    reviewer=result.reviewer,
                    verdict=ReviewVerdict.FAIL,
                    reason="FAIL-CLOSED: PASS without structured findings rejected",
                    tier=tier,
                    findings=result.findings,
                    raw_response=result.raw_response,
                ))
        elif result.verdict == ReviewVerdict.FAIL:
            fail_votes.append(result)
            logger.info("Reviewer %s voted FAIL: %s", name, result.reason)
        elif result.verdict == ReviewVerdict.BORDERLINE:
            fail_votes.append(result)
            logger.info("Reviewer %s voted BORDERLINE (treated as FAIL): %s", name, result.reason)

    if fail_votes:
        worst = fail_votes[0]
        return ReviewResult(
            reviewer="chain",
            verdict=ReviewVerdict.FAIL,
            reason="Reviewer {} rejected: {}".format(worst.reviewer, worst.reason),
            tier=tier,
            findings=worst.findings,
            raw_response=worst.raw_response,
        ), all_results

    if not pass_votes:
        return ReviewResult(
            reviewer="chain",
            verdict=ReviewVerdict.ERROR,
            reason="No reviewers provided valid verdicts",
            tier=tier,
        ), all_results

    if require_two_pass:
        if len(pass_votes) >= 2:
            logger.info("Two reviewers agreed on PASS: %s, %s",
                       pass_votes[0].reviewer, pass_votes[1].reviewer)
            return ReviewResult(
                reviewer="chain({},{})".format(pass_votes[0].reviewer, pass_votes[1].reviewer),
                verdict=ReviewVerdict.PASS,
                reason="Two reviewers agreed: {} AND {}".format(
                    pass_votes[0].reason[:50], pass_votes[1].reason[:50]),
                confidence=min(pass_votes[0].confidence, pass_votes[1].confidence),
                tier=tier,
                findings=pass_votes[0].findings,
            ), all_results

        if len(pass_votes) == 1:
            primary = pass_votes[0]
            minimax = MiniMaxReviewer()
            ok, reason = minimax.is_available(config)
            if ok:
                logger.info("Only one PASS vote from %s, asking MiniMax to confirm", primary.reviewer)
                second_result = minimax.review_as_second_judge(pr, primary, config)
                all_results.append(second_result)

                if second_result.verdict == ReviewVerdict.PASS:
                    logger.info("MiniMax confirmed PASS")
                    return ReviewResult(
                        reviewer="chain({},minimax)".format(primary.reviewer),
                        verdict=ReviewVerdict.PASS,
                        reason="Primary {} confirmed by MiniMax: {}".format(
                            primary.reviewer, second_result.reason[:50]),
                        confidence=min(primary.confidence, second_result.confidence),
                        tier=tier,
                        findings=primary.findings,
                    ), all_results
                else:
                    logger.info("MiniMax did not confirm PASS: %s", second_result.reason)
                    return ReviewResult(
                        reviewer="chain",
                        verdict=ReviewVerdict.FAIL,
                        reason="FAIL-CLOSED: Single PASS not confirmed by second reviewer",
                        tier=tier,
                        findings=primary.findings,
                    ), all_results
            else:
                logger.warning("Only one PASS vote and MiniMax unavailable: %s", reason)
                return ReviewResult(
                    reviewer="chain",
                    verdict=ReviewVerdict.FAIL,
                    reason="FAIL-CLOSED: Only one reviewer PASS, no second reviewer available to confirm",
                    tier=tier,
                    findings=primary.findings,
                ), all_results

    return pass_votes[0], all_results
