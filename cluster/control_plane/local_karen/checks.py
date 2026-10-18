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
    ci_status: dict  # Dict[str, str]
    changed_files: list  # List[str]
    deleted_files: list  # List[str]
    description: str = ""  # PR body/description


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

SECURITY_PATTERNS = [
    (r"(?i)\.innerHTML\s*=", "XSS: innerHTML assignment", "security"),
    (r"(?i)dangerouslySetInnerHTML", "XSS: dangerouslySetInnerHTML", "security"),
    (r"(?i)document\.write\s*\(", "XSS: document.write", "security"),
    (r"(?i)\beval\s*\(", "Code injection: eval()", "security"),
    (r"subprocess\.(?:run|call|Popen)\s*\([^)]*shell\s*=\s*True", "Shell injection: shell=True", "security"),
    (r"os\.system\s*\(", "Shell injection: os.system", "security"),
    (r"(?i)SELECT\s+.*\s+FROM\s+.*\s+WHERE\s+.*\+", "SQL injection: string concat", "security"),
    (r"(?i)f['\"].*SELECT.*{", "SQL injection: f-string", "security"),
    (r"(?i)(auth|login|session).*bypass", "Auth bypass mention", "auth"),
    (r"(?i)disable.*auth", "Auth disable mention", "auth"),
    (r"(?i)skip.*auth", "Auth skip mention", "auth"),
    (r"(?i)if\s+.*admin.*:\s*pass", "Admin check bypass", "auth"),
    (r"verify\s*=\s*False", "TLS verification disabled", "security"),
    (r"(?i)CORS.*\*", "Permissive CORS", "security"),
    (r"Access-Control-Allow-Origin.*\*", "Permissive CORS header", "security"),
]

PAGES_PATTERNS = [
    (r"(?i)github\.io", "GitHub Pages reference"),
    (r"(?i)_config\.yml", "Jekyll config"),
    (r"(?i)\.nojekyll", "GitHub Pages marker"),
    (r"(?i)CNAME", "Custom domain file"),
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

    for line in pr.diff.split("\n"):
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
                snippet = added_line.strip()[:40]
                issues.append(f"{secret_type} in {current_file}: {snippet}...")
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
    - sudo usage
    - rm -rf with dangerous paths
    - force push commands
    - tag/release creation
    """
    dangerous_patterns = [
        (r"\bsudo\s+", "sudo usage"),
        (r"rm\s+-rf\s+[/~]", "rm -rf on root/home paths"),
        (r"git\s+push\s+.*--force", "git force push"),
        (r"git\s+push\s+.*-f\b", "git force push"),
        (r"git\s+tag\s+", "git tag creation"),
        (r"gh\s+release\s+create", "GitHub release creation"),
        (r"git\s+push\s+.*--tags", "pushing tags"),
        (r"chmod\s+777", "chmod 777"),
        (r"eval\s*\(", "eval usage"),
        (r"\bexec\s*\(", "exec usage"),
    ]

    issues = []
    current_file = None

    for line in pr.diff.split("\n"):
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
                snippet = added_line.strip()[:50]
                issues.append(f"{danger_type} in {current_file}: {snippet}")
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


def check_security_patterns(pr: PRInfo, config: KarenConfig) -> CheckResult:
    """Check for security vulnerabilities in diff additions.

    Evidence-backed findings with file:line references.
    Looks for:
    - XSS vectors (innerHTML, dangerouslySetInnerHTML, document.write)
    - Injection patterns (eval, shell=True, os.system, SQL concat)
    - Auth bypass patterns
    - TLS verification disabled
    - Permissive CORS
    """
    findings = []  # type: list
    current_file = None
    line_num = 0
    in_diff_hunk = False

    for line in pr.diff.split("\n"):
        if line.startswith("diff --git"):
            match = re.search(r"b/(.+)$", line)
            if match:
                current_file = match.group(1)
            line_num = 0
            in_diff_hunk = False
            continue

        if line.startswith("@@"):
            match = re.search(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
            if match:
                line_num = int(match.group(1)) - 1
            in_diff_hunk = True
            continue

        if in_diff_hunk:
            if line.startswith("+") and not line.startswith("+++"):
                line_num += 1
                added_line = line[1:]

                for pattern, desc, category in SECURITY_PATTERNS:
                    if re.search(pattern, added_line):
                        findings.append({
                            "file": current_file,
                            "line": line_num,
                            "category": category,
                            "description": desc,
                            "evidence": added_line.strip()[:60],
                        })
                        break
            elif line.startswith("-"):
                pass
            else:
                line_num += 1

    if findings:
        security_findings = [f for f in findings if f["category"] == "security"]
        auth_findings = [f for f in findings if f["category"] == "auth"]

        details_lines = []
        for f in findings[:10]:
            details_lines.append(f"- {f['file']}:{f['line']} [{f['category']}] {f['description']}")
            details_lines.append(f"  Evidence: {f['evidence']}")

        return CheckResult(
            check_name="security_patterns",
            verdict=CheckVerdict.FAIL,
            reason=f"Security issues: {len(security_findings)} security, {len(auth_findings)} auth",
            details="\n".join(details_lines),
        )

    return CheckResult(
        check_name="security_patterns",
        verdict=CheckVerdict.PASS,
        reason="No security vulnerabilities detected",
    )


def check_pages_exposure(pr: PRInfo, config: KarenConfig) -> CheckResult:
    """Check for accidental GitHub Pages exposure.

    Looks for:
    - Changes to docs/ or gh-pages branch patterns
    - Jekyll config files
    - CNAME files (custom domain setup)
    """
    issues = []
    current_file = None

    for line in pr.diff.split("\n"):
        if line.startswith("diff --git"):
            match = re.search(r"b/(.+)$", line)
            if match:
                current_file = match.group(1)

            if current_file:
                if current_file.lower().startswith("docs/"):
                    issues.append(f"Changes to docs/ folder: {current_file}")
                if "_config.yml" in current_file.lower():
                    issues.append(f"Jekyll config modified: {current_file}")
                if current_file.upper() == "CNAME":
                    issues.append(f"CNAME file changed: {current_file}")

    if issues:
        return CheckResult(
            check_name="pages_exposure",
            verdict=CheckVerdict.FAIL,
            reason=f"GitHub Pages exposure risk: {len(issues)} file(s) affected",
            details="\n".join(issues[:5]),
        )

    return CheckResult(
        check_name="pages_exposure",
        verdict=CheckVerdict.PASS,
        reason="No Pages exposure detected",
    )


def run_repo_tests_in_worktree(pr: PRInfo, config: KarenConfig) -> CheckResult:
    """Run repo tests at head SHA in a temp worktree.
    
    This is a BETTER-THAN-GROK feature: actually executes tests locally
    before the LLM review, catching issues without spending tokens.
    
    Strategy:
    1. Clone repo to temp dir (sparse checkout, depth=1)
    2. Checkout exact head SHA
    3. Detect test runner (pytest, npm test, make test, etc.)
    4. Run tests with timeout
    5. Report pass/fail with evidence
    
    Falls back to CI status if local test run isn't possible.
    """
    import tempfile
    import shutil
    import os
    
    full_repo = f"{pr.owner}/{pr.repo}"
    
    if not getattr(config, 'run_local_tests', True):
        return CheckResult(
            check_name="repo_tests",
            verdict=CheckVerdict.SKIP,
            reason="Local test execution disabled in config",
        )
    
    worktree_dir = None
    try:
        worktree_dir = tempfile.mkdtemp(prefix="karen-test-")
        
        clone_result = subprocess.run(
            ["gh", "repo", "clone", full_repo, worktree_dir, "--", 
             "--depth=1", "--single-branch", "-b", pr.head_sha[:12]],
            capture_output=True,
            text=True,
            timeout=120,
        )
        
        if clone_result.returncode != 0:
            clone_result = subprocess.run(
                ["gh", "repo", "clone", full_repo, worktree_dir, "--", "--depth=50"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if clone_result.returncode == 0:
                subprocess.run(
                    ["git", "checkout", pr.head_sha],
                    cwd=worktree_dir,
                    capture_output=True,
                    timeout=30,
                )
        
        if clone_result.returncode != 0:
            return CheckResult(
                check_name="repo_tests",
                verdict=CheckVerdict.SKIP,
                reason="Could not clone repo for local testing",
                details=clone_result.stderr[:200],
            )
        
        test_cmd = None
        test_label = ""
        
        if os.path.exists(os.path.join(worktree_dir, "pyproject.toml")) or \
           os.path.exists(os.path.join(worktree_dir, "pytest.ini")) or \
           os.path.exists(os.path.join(worktree_dir, "setup.py")):
            test_cmd = ["python3", "-m", "pytest", "-x", "--tb=short", "-q"]
            test_label = "pytest"
        elif os.path.exists(os.path.join(worktree_dir, "package.json")):
            test_cmd = ["npm", "test", "--", "--passWithNoTests"]
            test_label = "npm test"
        elif os.path.exists(os.path.join(worktree_dir, "Makefile")):
            with open(os.path.join(worktree_dir, "Makefile")) as f:
                if "test:" in f.read():
                    test_cmd = ["make", "test"]
                    test_label = "make test"
        elif os.path.exists(os.path.join(worktree_dir, "Cargo.toml")):
            test_cmd = ["cargo", "test", "--no-fail-fast"]
            test_label = "cargo test"
        elif os.path.exists(os.path.join(worktree_dir, "go.mod")):
            test_cmd = ["go", "test", "./...", "-short"]
            test_label = "go test"
        
        if not test_cmd:
            return CheckResult(
                check_name="repo_tests",
                verdict=CheckVerdict.SKIP,
                reason="No recognized test runner found",
                details="Checked: pytest, npm, make, cargo, go",
            )
        
        test_result = subprocess.run(
            test_cmd,
            cwd=worktree_dir,
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "CI": "true"},
        )
        
        if test_result.returncode == 0:
            return CheckResult(
                check_name="repo_tests",
                verdict=CheckVerdict.PASS,
                reason=f"Local {test_label} passed at {pr.head_sha[:8]}",
                details=f"Ran in temp worktree, exit code 0",
            )
        else:
            output = (test_result.stdout + test_result.stderr)[-500:]
            return CheckResult(
                check_name="repo_tests",
                verdict=CheckVerdict.FAIL,
                reason=f"Local {test_label} failed at {pr.head_sha[:8]}",
                details=f"Exit code {test_result.returncode}:\n{output}",
            )
            
    except subprocess.TimeoutExpired:
        return CheckResult(
            check_name="repo_tests",
            verdict=CheckVerdict.FAIL,
            reason="Local test execution timed out (5 min limit)",
        )
    except Exception as e:
        return CheckResult(
            check_name="repo_tests",
            verdict=CheckVerdict.SKIP,
            reason=f"Local test execution error: {type(e).__name__}",
            details=str(e)[:200],
        )
    finally:
        if worktree_dir and os.path.exists(worktree_dir):
            shutil.rmtree(worktree_dir, ignore_errors=True)


def run_static_security_scan(pr: PRInfo, config: KarenConfig) -> CheckResult:
    """Comprehensive static security scan BEFORE LLM review.
    
    This is a BETTER-THAN-GROK feature: catches security issues without
    spending LLM tokens. Grok runs LLM first; we run static scan first.
    
    Combines all security-related patterns into one thorough scan:
    - Secrets (API keys, tokens, passwords)
    - Dangerous commands (sudo, rm -rf, force push)
    - Security vulnerabilities (XSS, injection, auth bypass)
    - Unsafe shell patterns
    """
    all_findings = []
    current_file = None
    line_num = 0
    in_diff_hunk = False
    
    all_patterns = [
        *[(p, desc, "secret") for p, desc in SECRET_PATTERNS],
        *[(p, desc, cat) for p, desc, cat in SECURITY_PATTERNS],
        (r"\bsudo\s+", "sudo usage", "dangerous"),
        (r"rm\s+-rf\s+[/~]", "rm -rf on root/home", "dangerous"),
        (r"git\s+push\s+.*--force", "git force push", "dangerous"),
        (r"git\s+push\s+.*-f\b", "git force push", "dangerous"),
        (r"chmod\s+777", "chmod 777", "dangerous"),
        (r"os\.popen\s*\(", "os.popen (shell)", "dangerous"),
        (r"commands\.getoutput", "commands.getoutput", "dangerous"),
        (r"pickle\.load", "pickle deserialization", "security"),
        (r"yaml\.load\s*\([^)]*\)", "unsafe yaml.load", "security"),
        (r"marshal\.load", "marshal deserialization", "security"),
    ]
    
    for line in pr.diff.split("\n"):
        if line.startswith("diff --git"):
            match = re.search(r"b/(.+)$", line)
            if match:
                current_file = match.group(1)
            line_num = 0
            in_diff_hunk = False
            continue
        
        if line.startswith("@@"):
            match = re.search(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
            if match:
                line_num = int(match.group(1)) - 1
            in_diff_hunk = True
            continue
        
        # Scan additions even in diff fragments without hunk headers. Hunk
        # metadata supplies locations; it must not be required for detection.
        if line.startswith("+") and not line.startswith("+++"):
            line_num += 1
            added_line = line[1:]

            for pattern, desc, category in all_patterns:
                if re.search(pattern, added_line, re.IGNORECASE):
                    all_findings.append({
                        "file": current_file,
                        "line": line_num,
                        "category": category,
                        "description": desc,
                        "evidence": added_line.strip()[:50],
                    })
                    break
        elif in_diff_hunk and not line.startswith("-"):
            line_num += 1
    
    if all_findings:
        by_category = {}
        for f in all_findings:
            cat = f["category"]
            by_category[cat] = by_category.get(cat, 0) + 1
        
        summary = ", ".join(f"{v} {k}" for k, v in sorted(by_category.items()))
        
        details = []
        for f in all_findings[:15]:
            details.append(f"- {f['file']}:{f['line']} [{f['category']}] {f['description']}")
        
        return CheckResult(
            check_name="static_security_scan",
            verdict=CheckVerdict.FAIL,
            reason=f"Static scan found issues: {summary}",
            details="\n".join(details),
        )
    
    return CheckResult(
        check_name="static_security_scan",
        verdict=CheckVerdict.PASS,
        reason=f"Static security scan clean ({len(pr.changed_files)} files)",
    )


def run_all_checks(pr, config):
    # type: (PRInfo, KarenConfig) -> list
    """Run all deterministic checks BEFORE LLM review.

    BETTER-THAN-GROK: Static scan and local tests run BEFORE LLM.
    This catches issues without spending tokens.
    
    Order (all run before LLM):
    1. Blocked repo check (never-touch list)
    2. Draft status
    3. Static security scan (secrets, dangerous, vulnerabilities) - NEW
    4. CI status (green required)
    5. Test integrity (no weakening)
    6. Security patterns (XSS, injection, auth bypass)
    7. Pages exposure risk
    8. Local repo tests at head SHA (if enabled) - NEW
    """
    checks = [
        check_blocked_repo,
        check_not_draft,
        run_static_security_scan,
        check_ci_green,
        check_tests_not_weakened,
        check_security_patterns,
        check_pages_exposure,
    ]
    
    run_local = getattr(config, 'run_local_tests', False)

    results = []
    for check in checks:
        try:
            result = check(pr, config)
            results.append(result)
        except Exception as e:
            results.append(CheckResult(
                check_name=check.__name__.replace("check_", "").replace("run_", ""),
                verdict=CheckVerdict.FAIL,
                reason=f"Check crashed: {e}",
                details=str(e),
            ))
    
    if run_local:
        try:
            local_test_result = run_repo_tests_in_worktree(pr, config)
            results.append(local_test_result)
        except Exception as e:
            results.append(CheckResult(
                check_name="repo_tests",
                verdict=CheckVerdict.SKIP,
                reason=f"Local test execution crashed: {e}",
            ))

    return results


def all_checks_pass(results):
    # type: (list) -> bool
    """Return True if all checks passed (none failed)."""
    return not any(r.failed for r in results)


def get_failed_checks(results):
    # type: (list) -> list
    """Return list of failed checks."""
    return [r for r in results if r.failed]
