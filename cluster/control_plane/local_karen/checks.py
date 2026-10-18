"""Deterministic checks for Local Karen gate.

These checks run BEFORE any LLM review and must all pass.
Fail closed: any error = FAIL verdict.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .config import KarenConfig

try:
    from cluster.never_touch import is_never_touch, NeverTouchViolation
    _HAS_NEVER_TOUCH = True
except ImportError:
    _HAS_NEVER_TOUCH = False


class CheckVerdict(Enum):
    """Verdict from a single check."""
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


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
    ci_status: dict[str, str]
    changed_files: list[str]
    deleted_files: list[str]


SECRET_PATTERNS = [
    (r"(?i)(api[_-]?key|apikey)\s*[=:]\s*['\"][^'\"]{20,}['\"]", "API key"),
    (r"(?i)(secret|password|passwd|pwd)\s*[=:]\s*['\"][^'\"]{8,}['\"]", "Secret/password"),
    (r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}", "Bearer token"),
    (r"ghp_[a-zA-Z0-9]{36,}", "GitHub PAT"),
    (r"gho_[a-zA-Z0-9]{36,}", "GitHub OAuth token"),
    (r"github_pat_[a-zA-Z0-9]{22}_[a-zA-Z0-9]{59}", "GitHub fine-grained PAT"),
    (r"sk-[a-zA-Z0-9]{48,}", "OpenAI API key"),
    (r"sk-ant-[a-zA-Z0-9\-]{90,}", "Anthropic API key"),
    (r"AIza[a-zA-Z0-9_\-]{35}", "Google API key"),
    (r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", "Private key"),
    (r"(?i)aws[_-]?(access[_-]?key|secret)[_-]?id?\s*[=:]\s*['\"]?[A-Z0-9]{16,}['\"]?", "AWS credential"),
    (r"AKIA[A-Z0-9]{16}", "AWS access key ID"),
]

TEST_FILE_PATTERNS = [
    r"test[_\-].*\.py$",
    r".*[_\-]test\.py$",
    r"tests?/.*\.py$",
    r"spec[_\-].*\.(js|ts)$",
    r".*\.spec\.(js|ts)$",
    r".*\.test\.(js|ts)$",
    r"__tests__/.*\.(js|ts)$",
]


def check_blocked_repo(pr: PRInfo, config: KarenConfig) -> CheckResult:
    """Check if repo is in the never-touch list.

    Uses both:
    1. cluster.never_touch module (authoritative, if available)
    2. Local config never_touch_repos (backup)
    """
    full_repo = f"{pr.owner}/{pr.repo}"

    if _HAS_NEVER_TOUCH:
        if is_never_touch(full_repo):
            return CheckResult(
                check_name="blocked_repo",
                verdict=CheckVerdict.FAIL,
                reason=f"Repository {full_repo} is on the authoritative never-touch list",
                details="Human approval required; automation blocked",
            )

    if config.is_blocked_repo(pr.owner, pr.repo):
        return CheckResult(
            check_name="blocked_repo",
            verdict=CheckVerdict.FAIL,
            reason=f"Repository {full_repo} is in the local never-touch list",
        )

    return CheckResult(
        check_name="blocked_repo",
        verdict=CheckVerdict.PASS,
        reason="Repository is not blocked",
    )


def check_not_draft(pr: PRInfo, config: KarenConfig) -> CheckResult:
    """Check that PR is not a draft."""
    if pr.is_draft:
        return CheckResult(
            check_name="not_draft",
            verdict=CheckVerdict.FAIL,
            reason="PR is still a draft — undraft before requesting gate",
        )
    return CheckResult(
        check_name="not_draft",
        verdict=CheckVerdict.PASS,
        reason="PR is not a draft",
    )


def check_ci_green(pr: PRInfo, config: KarenConfig) -> CheckResult:
    """Check that all CI checks are green at the pinned SHA."""
    passing = ("success", "skipped", "neutral")

    for check_name, status in pr.ci_status.items():
        if status in ("pending", "in_progress", "queued"):
            return CheckResult(
                check_name="ci_green",
                verdict=CheckVerdict.FAIL,
                reason=f"CI incomplete: {check_name} is {status}",
                details=f"Wait for CI to complete before gate check",
            )
        if status not in passing:
            return CheckResult(
                check_name="ci_green",
                verdict=CheckVerdict.FAIL,
                reason=f"CI failed: {check_name} is {status}",
            )

    if not pr.ci_status:
        return CheckResult(
            check_name="ci_green",
            verdict=CheckVerdict.PASS,
            reason="No CI checks configured (proceeding cautiously)",
        )

    return CheckResult(
        check_name="ci_green",
        verdict=CheckVerdict.PASS,
        reason=f"All {len(pr.ci_status)} CI checks passing",
    )


def _is_test_file(filepath: str) -> bool:
    """Check if a file path looks like a test file."""
    for pattern in TEST_FILE_PATTERNS:
        if re.search(pattern, filepath, re.IGNORECASE):
            return True
    return False


def _extract_diff_deletions(diff: str) -> list[tuple[str, str]]:
    """Extract deleted lines from a unified diff.

    Returns list of (filename, deleted_line) tuples.
    """
    deletions = []
    current_file = None

    for line in diff.split("\n"):
        if line.startswith("diff --git"):
            match = re.search(r"b/(.+)$", line)
            if match:
                current_file = match.group(1)
        elif line.startswith("-") and not line.startswith("---"):
            if current_file:
                deletions.append((current_file, line[1:]))

    return deletions


def check_tests_not_weakened(pr: PRInfo, config: KarenConfig) -> CheckResult:
    """Check that tests are not deleted or weakened in the diff.

    Looks for:
    - Deleted test files
    - Removed test functions/methods
    - Deleted assertions
    - Removed test decorators (@pytest.mark, @test, etc.)
    """
    issues = []

    for filepath in pr.deleted_files:
        if _is_test_file(filepath):
            issues.append(f"Deleted test file: {filepath}")

    deletions = _extract_diff_deletions(pr.diff)

    test_function_patterns = [
        r"^\s*def\s+test_",
        r"^\s*async\s+def\s+test_",
        r"^\s*it\s*\(",
        r"^\s*describe\s*\(",
        r"^\s*test\s*\(",
    ]

    assertion_patterns = [
        r"\bassert\s+",
        r"\.assert",
        r"expect\s*\(",
        r"\.to(Equal|Be|Have|Match|Throw)",
        r"\.should\.",
    ]

    decorator_patterns = [
        r"@pytest\.mark\.",
        r"@test",
        r"@unittest",
    ]

    for filepath, line in deletions:
        if not _is_test_file(filepath):
            continue

        for pattern in test_function_patterns:
            if re.search(pattern, line):
                issues.append(f"Removed test function in {filepath}: {line.strip()[:60]}")
                break

        for pattern in assertion_patterns:
            if re.search(pattern, line):
                issues.append(f"Removed assertion in {filepath}: {line.strip()[:60]}")
                break

        for pattern in decorator_patterns:
            if re.search(pattern, line):
                issues.append(f"Removed test decorator in {filepath}: {line.strip()[:60]}")
                break

    if issues:
        return CheckResult(
            check_name="tests_not_weakened",
            verdict=CheckVerdict.FAIL,
            reason=f"Tests appear weakened: {len(issues)} issue(s) found",
            details="\n".join(issues[:10]),
        )

    return CheckResult(
        check_name="tests_not_weakened",
        verdict=CheckVerdict.PASS,
        reason="No test weakening detected",
    )


def check_no_secrets_in_diff(pr: PRInfo, config: KarenConfig) -> CheckResult:
    """Check that no secrets are added in the diff.

    Only checks additions (lines starting with +), not deletions.
    """
    issues = []
    current_file = None

    for line_number, line in enumerate(pr.diff.split("\n"), 1):
        if line.startswith("diff --git"):
            match = re.search(r"b/(.+)$", line)
            if match:
                current_file = match.group(1)
            continue

        if not line.startswith("+") or line.startswith("+++"):
            continue

        added_line = line[1:]

        for pattern, secret_type in SECRET_PATTERNS:
            if re.search(pattern, added_line):
                issues.append(
                    f"{secret_type} in {current_file} at diff line {line_number}: [REDACTED]"
                )
                break

    if issues:
        return CheckResult(
            check_name="no_secrets",
            verdict=CheckVerdict.FAIL,
            reason=f"Potential secrets detected: {len(issues)} issue(s)",
            details="\n".join(issues[:5]),
        )

    return CheckResult(
        check_name="no_secrets",
        verdict=CheckVerdict.PASS,
        reason="No secrets detected in diff",
    )


def check_no_dangerous_commands(pr: PRInfo, config: KarenConfig) -> CheckResult:
    """Check for dangerous commands in diff additions.

    Looks for:
    - Privileged commands
    - rm -rf with dangerous paths
    - force push commands
    - tag/release creation
    """
    dangerous_patterns = [
        (r"\bsudo\s+", "sudo"),
        (r"rm\s+-rf\s+[/~]", "rm -rf on root/home paths"),
        (r"git\s+push\s+.*--force", "git force push"),
        (r"git\s+push\s+.*-f\b", "git force push"),
        (r"git\s+tag\s+", "tag creation"),
        (r"gh\s+release\s+create", "GitHub release creation"),
        (r"git\s+push\s+.*--tags", "pushing tags"),
        (r"chmod\s+777", "world writable permissions"),
        (r"eval\s*\(", "eval usage"),
        (r"\bexec\s*\(", "exec usage"),
    ]

    issues = []
    current_file = None

    for line_number, line in enumerate(pr.diff.split("\n"), 1):
        if line.startswith("diff --git"):
            match = re.search(r"b/(.+)$", line)
            if match:
                current_file = match.group(1)
            continue

        if not line.startswith("+") or line.startswith("+++"):
            continue

        added_line = line[1:]

        for pattern, danger_type in dangerous_patterns:
            if re.search(pattern, added_line, re.IGNORECASE):
                issues.append(f"{danger_type} in {current_file} at diff line {line_number}")
                break

    if issues:
        return CheckResult(
            check_name="no_dangerous_commands",
            verdict=CheckVerdict.FAIL,
            reason=f"Dangerous commands detected: {len(issues)} issue(s)",
            details="\n".join(issues[:5]),
        )

    return CheckResult(
        check_name="no_dangerous_commands",
        verdict=CheckVerdict.PASS,
        reason="No dangerous commands detected",
    )


def run_all_checks(pr: PRInfo, config: KarenConfig) -> list[CheckResult]:
    """Run all deterministic checks and return results.

    Order matters: blocked repo check runs first.
    """
    checks = [
        check_blocked_repo,
        check_not_draft,
        check_ci_green,
        check_tests_not_weakened,
        check_no_secrets_in_diff,
        check_no_dangerous_commands,
    ]

    results = []
    for check in checks:
        try:
            result = check(pr, config)
            results.append(result)
        except Exception as e:
            results.append(CheckResult(
                check_name=check.__name__.replace("check_", ""),
                verdict=CheckVerdict.FAIL,
                reason=f"Check crashed: {e}",
                details=str(e),
            ))

    return results


def all_checks_pass(results: list[CheckResult]) -> bool:
    """Return True if all checks passed (none failed)."""
    return not any(r.failed for r in results)


def get_failed_checks(results: list[CheckResult]) -> list[CheckResult]:
    """Return list of failed checks."""
    return [r for r in results if r.failed]
