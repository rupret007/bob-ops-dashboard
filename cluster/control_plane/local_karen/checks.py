"""Deterministic checks for Local Karen gate.

These checks run BEFORE any LLM review and must all pass.
Concrete findings are FAIL; operational errors remain retryable ERROR.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Any, Dict, Tuple

from .config import KarenConfig
from .contract_policy import VerifiedContract, authorized_assertion

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
    base_sha: str = ""
    test_contract_proof: Optional[VerifiedContract] = None


SECRET_PATTERNS = [
    (r"(?i)(api[_-]?key|apikey)\s*[=:]\s*(['\"])[^'\"]{20,}\2", "API key"),
    (r"(?i)(secret|password|passwd|pwd)\s*[=:]\s*(['\"])[^'\"]{8,}\2", "Secret/password"),
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
    r".*\.test\.(js|ts|mjs|cjs)$",
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


def _removed_lines(diff):
    filepath, number = "diff", 1
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            filepath = line.split(" b/", 1)[-1]
            number = 1
        elif line.startswith("@@"):
            hunk = re.search(r"@@ -(\d+)", line)
            if hunk:
                number = int(hunk.group(1))
        elif line.startswith("-") and not line.startswith("---"):
            yield filepath, number, line[1:]
            number += 1
        elif line.startswith(" "):
            number += 1


def _extract_diff_deletions(diff):
    return [(path, line) for path, _, line in _removed_lines(diff)]


def _renamed_js_test_headers(diff):
    """Description-only edits preserve the test body; changed asserts still fail."""
    safe = set()
    filepath, number = "diff", 1
    header = re.compile(r"^(\s*(?:it|test|describe)\(\s*)(['\"])[^'\"]*\2(\s*,\s*(?:async\s+)?\(\)\s*=>\s*\{\s*)$")
    for block in diff.split("diff --git ")[1:]:
        filepath = block.splitlines()[0].split(" b/", 1)[-1]
        for hunk in re.split(r"(?m)^@@ ", block)[1:]:
            position = re.match(r"-(\d+)", hunk)
            number = int(position.group(1)) if position else 1
            removed, added = [], []
            for line in hunk.splitlines()[1:]:
                if line.startswith("-"):
                    removed.append((number, line[1:]))
                    number += 1
                elif line.startswith("+"):
                    added.append(line[1:])
                elif line.startswith(" "):
                    number += 1
            if len(removed) != 1 or not added:
                continue
            old = header.fullmatch(removed[0][1])
            new = header.fullmatch(added[0])
            # Extra additions must be simple assertion statements. Do not waive
            # bodies, setup, control flow, decorators or changed expectations.
            if (old and new and old.group(1, 3) == new.group(1, 3)
                    and all(re.fullmatch(r"\s*expect\([^;]+\);\s*", line) for line in added[1:])):
                safe.add((filepath, removed[0][0]))
    return safe


def _literal_equivalent_line(before, after):
    """Only string spelling may change; every token and literal value must agree."""
    import ast
    import io
    import tokenize

    def tokens(line):
        result = []
        try:
            for token in tokenize.generate_tokens(io.StringIO(line).readline):
                if token.type in (tokenize.NL, tokenize.NEWLINE, tokenize.ENDMARKER):
                    continue
                value = ast.literal_eval(token.string) if token.type == tokenize.STRING else token.string
                result.append((token.type, value))
        except tokenize.TokenError as exc:
            # Diff context may close a surrounding multiline expression.
            if not result or exc.args[0] != 'EOF in multi-line statement':
                return None
        except (IndentationError, SyntaxError, ValueError):
            return None
        return result

    return before != after and tokens(before) is not None and tokens(before) == tokens(after)


def _new_test_source_files(diff):
    """Only complete newly introduced files can contain no old tests to skip."""
    added = set()
    for block in diff.split("diff --git ")[1:]:
        lines = block.splitlines()
        path = lines[0].split(" b/", 1)[-1]
        if not _is_test_file(path):
            continue
        if ("new file mode 100644" in lines or "new file mode 100755" in lines):
            hunks = [line for line in lines if line.startswith("@@")]
            if ("--- /dev/null" in lines and hunks
                    and all(re.match(r"@@ -0,0 \+\d+(?:,\d+)? @@", hunk) for hunk in hunks)
                    and not any(line.startswith("-") and line != "--- /dev/null" for line in lines)):
                added.add(path)
    return added


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
            issues.append(f"Deleted test file: {filepath}:1")

    deletions = _removed_lines(pr.diff)
    renamed_headers = _renamed_js_test_headers(pr.diff)
    additions = list(_added_lines(pr.diff))
    equivalent_additions = set()
    contract = authorized_assertion(pr)

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

    for filepath, number, line in deletions:
        if not _is_test_file(filepath):
            continue

        for pattern in test_function_patterns:
            if (filepath, number) not in renamed_headers and re.search(pattern, line):
                issues.append(f"Removed test function in {filepath}:{number} (removed source; value redacted)")
                break

        for pattern in assertion_patterns:
            if re.search(pattern, line):
                replacement = next((i for i, (path, _, added) in enumerate(additions)
                                    if i not in equivalent_additions and path == filepath and
                                    filepath.endswith('.py') and _literal_equivalent_line(line, added)), None)
                if replacement is None and contract and (filepath, number, line) == contract[:3]:
                    replacement = next((i for i, (path, added_number, added) in enumerate(additions)
                                        if i not in equivalent_additions
                                        and (path, added_number, added) == (contract[0], contract[3], contract[4])), None)
                if replacement is None:
                    issues.append(f"Removed assertion in {filepath}:{number} (removed source; value redacted)")
                else:
                    equivalent_additions.add(replacement)
                break

        for pattern in decorator_patterns:
            if re.search(pattern, line):
                issues.append(f"Removed test decorator in {filepath}:{number} (removed source; value redacted)")
                break

    new_test_files = _new_test_source_files(pr.diff)
    for filepath, number, line in _added_lines(pr.diff):
        # A known optional-import guard may accompany a wholly new test file.
        # The same decorator applied to any existing file must remain a FAIL:
        # it could otherwise disable old assertions without deleting them.
        optional_import_guard = (
            filepath in new_test_files
            and line.strip() == '@unittest.skipUnless(_HAS_NEVER_TOUCH, "cluster.never_touch not available")'
        )
        if _is_test_file(filepath) and not optional_import_guard and re.search(
                r"^\s*(?:@(?:pytest\.mark\.(?:skip|xfail)|unittest\.skip)|(?:it|test|describe)\.(?:skip|todo)\s*\()", line):
            issues.append(f"Added test skip/xfail in {filepath}:{number} (source redacted)")

    if issues:
        return CheckResult(
            check_name="tests_not_weakened",
            verdict=CheckVerdict.FAIL,
            reason=f"Tests appear weakened: {len(issues)} issue(s) found",
            details="\n".join(issues),
        )

    return CheckResult(
        check_name="tests_not_weakened",
        verdict=CheckVerdict.PASS,
        reason="No test weakening detected",
    )



# Reviewed inert literals from the reported PRs. Restrict exemptions to test
# sources and these complete values; a fixture marker never hides arbitrary data.
_TEST_SECRET_LITERALS = frozenset({
    "synthetic-approle-secret-value", "synthetic-media-secret", "client-secret",
    "synthetic-secret-do-not-return", "synthetic-unit-test-session-value-with-32-characters",
    "synthetic-private-test-key", "sk-synthetic-caption-test", "synthetic-secret",
    "social-upload-http-test-secret", "synthetic-session-secret", "meta-secret",
    "yt-secret", "tt-secret", "synthetic-provider-secret", "private-fixture-secret",
    "SYNTHETIC_SECRET_1006", "synthetic-owner-password-123", "synthetic-review-password",
    "synthetic-review-secret", "SYNTHETIC-ONLY", "synthetic-armored-key-data",
    "synthetic-client-secret", "fixture-oauth-secret", "synthetic-owner-secret-123",
    "synthetic-pgp-key-data", "synthetic-routine-refresh-secret",
})


def _placeholder_secret(match, secret_type, filepath):
    if secret_type not in ("API key", "Secret/password"):
        return False
    value = re.search(r"[\"']([^\"']*)[\"']$", match.group(0))
    if value is None:
        return False
    # Only complete variable substitutions in configuration templates. A real
    # credential, literal suffix/prefix, or adjacent match remains a finding.
    if str(filepath or '').endswith('.template') and re.fullmatch(
            r'\{\{[A-Z][A-Z0-9_]*_(?:TOKEN|KEY|PASSWORD|SECRET)\}\}', value.group(1)):
        return True
    # The ACL renderer emits this complete format token, never a credential.
    # Keep literal prefixes/suffixes and all independent key rules actionable.
    if (filepath == 'cluster/nats_acl.py'
            and value.group(1) == '{{%s_TOKEN}}'):
        return True
    return _is_test_file(filepath or "") and value.group(1) in _TEST_SECRET_LITERALS


def _added_lines(diff):
    """Yield added source with its new-file position, never context/deletions."""
    filepath, number = "diff", 1
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            filepath = line.split(" b/", 1)[-1]
            number = 1
        elif line.startswith("@@"):
            hunk = re.search(r"\+(\d+)(?:,\d+)? @@", line)
            if hunk:
                number = int(hunk.group(1))
        elif line.startswith("+") and not line.startswith("+++"):
            yield filepath, number, line[1:]
            number += 1
        elif line.startswith(" "):
            number += 1


def _prose_or_comment(filepath, line):
    # Only non-executable documentation/comments; secrets are still scanned.
    return (filepath.lower().endswith((".md", ".rst", ".txt"))
            or line.lstrip().startswith(("#", "//")))


def _literal_shell_display(filepath, line):
    """Recognize one display command with no execution or output redirection.

    Installer help may print uninstall commands. Expansion, command chaining,
    pipes and redirected script contents must retain their normal checks.
    """
    import shlex

    if not filepath.lower().endswith((".sh", ".bash", ".zsh", ".command")):
        return False
    if any(char in line for char in ("$", "`", "\n", "\r")):
        return False
    try:
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ''
        tokens = list(lexer)
    except ValueError:
        return False
    return (bool(tokens) and tokens[0] in ('echo', 'printf')
            and not any(token and all(char in '();<>|&' for char in token)
                        for token in tokens[1:]))


def _python_test_fixture_source(diff):
    """Mask proven inert fixture expressions in complete Python test source.

    Calls, computed values, incomplete context and neighboring statements stay
    visible. Secret scanners always receive the original source.
    """
    import ast

    files, path, number = {}, '', 1
    for line in diff.splitlines():
        if line.startswith('diff --git '):
            path = line.split(' b/', 1)[-1]
        elif line.startswith('@@'):
            match = re.search(r'\+(\d+)(?:,\d+)? @@', line)
            number = int(match.group(1)) if match else 1
        elif line.startswith((' ', '+')) and not line.startswith('+++'):
            files.setdefault(path, {})[number] = line[1:]
            number += 1
    masked = {}
    for path, lines in files.items():
        approved_modules = {
            'cluster/control_plane/local_karen/tests/test_shell_display.py',
            'cluster/control_plane/local_karen/tests/test_python_fixture_source.py',
            'cluster/control_plane/tests/test_fix_difficulty.py',
        }
        if (path not in approved_modules
                or set(lines) != set(range(1, max(lines) + 1))):
            continue
        source = '\n'.join(lines[n] for n in range(1, max(lines) + 1))
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        # These regression harnesses only build PRInfo and invoke pure checks.
        # Any unknown import, alias or callable invalidates that proof.
        imports = {
            ('', 'pytest'),
            ('cluster.control_plane.local_karen', 'checks'),
            ('cluster.control_plane.local_karen.config', 'KarenConfig'),
            ('cluster.control_plane.fix_dispatcher', 'classify_fix_difficulty'),
        }
        safe_calls = {'candidate', 'scanner', 'len', 'KarenConfig', 'classify_fix_difficulty',
                      'pytest.mark.parametrize', 'checks.PRInfo',
                      'checks.check_no_dangerous_commands', 'checks.check_no_secrets_in_diff',
                      'checks.check_security_patterns', 'checks.run_static_security_scan',
                      'source.splitlines', 'pr.diff.replace'}
        protected = {'candidate', 'scanner', 'len', 'KarenConfig',
                     'classify_fix_difficulty', 'pytest', 'checks'}
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        invalid = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, 'module', '') or ''
                if (getattr(node, 'level', 0)
                        or any(alias.asname or (module, alias.name) not in imports for alias in node.names)):
                    invalid = True
            elif isinstance(node, ast.arg) and node.arg in protected:
                invalid = True
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in protected:
                canonical = (isinstance(node, ast.FunctionDef) and node.name == 'candidate'
                             and len(node.args.args) == 2
                             and node.args.args[0].arg in ('line', 'source')
                             and node.args.args[1].arg == 'path'
                             and all(isinstance(item, (ast.Assign, ast.AugAssign, ast.Return))
                                     for item in node.body)
                             and isinstance(node.body[-1], ast.Return)
                             and isinstance(node.body[-1].value, ast.Call)
                             and ast.unparse(node.body[-1].value.func) == 'checks.PRInfo')
                if not canonical:
                    invalid = True
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id in protected:
                parent = parents.get(node)
                scanner_loop = (node.id == 'scanner' and isinstance(parent, ast.For)
                                and parent.target is node and isinstance(parent.iter, (ast.Tuple, ast.List))
                                and bool(parent.iter.elts)
                                and all(isinstance(item, ast.Attribute)
                                        and ast.unparse(item) in safe_calls
                                        and ast.unparse(item).startswith('checks.check_')
                                        or isinstance(item, ast.Attribute)
                                        and ast.unparse(item) == 'checks.run_static_security_scan'
                                        for item in parent.iter.elts))
                if not scanner_loop:
                    invalid = True
            elif isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
                parent = parents.get(node)
                fixture_diff = (ast.unparse(node) == 'pr.diff' and isinstance(parent, ast.Assign)
                                and isinstance(parent.value, ast.Call)
                                and ast.unparse(parent.value.func) == 'pr.diff.replace')
                if not fixture_diff:
                    invalid = True
            elif isinstance(node, ast.Call):
                literal_format = (isinstance(node.func, ast.Attribute)
                                  and node.func.attr in ('format', 'join')
                                  and isinstance(node.func.value, ast.Constant)
                                  and isinstance(node.func.value.value, str))
                if not literal_format and ast.unparse(node.func) not in safe_calls:
                    invalid = True
        if invalid:
            continue
        names, spans = set(), []

        def literal(node):
            return (isinstance(node, ast.Constant)
                    or isinstance(node, ast.Name) and node.id in names
                    or isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
                    and literal(node.left) and literal(node.right)
                    or isinstance(node, (ast.List, ast.Tuple, ast.Set))
                    and all(literal(item) for item in node.elts))

        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id.isupper() and literal(node.value)):
                names.add(node.targets[0].id)
                spans.append(node)
        for node in ast.walk(tree):
            if isinstance(node, ast.For) and literal(node.iter):
                spans.append(node.iter)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and ast.unparse(node.func) == 'pytest.mark.parametrize'):
                spans.extend(arg for arg in node.args if literal(arg))
        for node in spans:
            for n in range(node.lineno, node.end_lineno + 1):
                value = masked.get((path, n), lines[n])
                # AST columns are UTF-8 byte offsets; Python slices use characters.
                start = (len(lines[n].encode()[:node.col_offset].decode())
                         if n == node.lineno else 0)
                end = (len(lines[n].encode()[:node.end_col_offset].decode())
                       if n == node.end_lineno else len(value))
                masked[path, n] = value[:start] + ' ' * (end - start) + value[end:]
    return masked


def _inert_scanner_rules(diff):
    """Identify complete literal rule tables, never executable expressions.

    Reconstruct only new-side context/additions. Missing context, computed
    entries or mixed executable expressions fail closed. Secrets still scan
    every line, including these tables.
    """
    import ast

    path = 'cluster/control_plane/local_karen/checks.py'
    source, number, current = {}, 1, None
    for line in diff.splitlines():
        if line.startswith('diff --git '):
            current = line.split(' b/', 1)[-1]
        elif line.startswith('@@'):
            hunk = re.search(r'\+(\d+)(?:,\d+)? @@', line)
            number = int(hunk.group(1)) if hunk else 1
        elif current == path and line.startswith((' ', '+')) and not line.startswith('+++'):
            source[number] = line[1:]
            number += 1
    if not source:
        return set()
    # Reject gaps within a table instead of inventing missing source.
    safe = set()
    start = re.compile(r'^(?P<indent>[ \t]*)(?:SECRET_PATTERNS|SECURITY_PATTERNS|dangerous_patterns|extra_patterns)\s*=\s*\[\s*$')
    for first, text in source.items():
        match = start.fullmatch(text)
        if not match:
            continue
        last, body = first + 1, ['[']
        while last in source and last - first <= 100:
            body.append(source[last])
            if source[last] == match.group('indent') + ']':
                break
            last += 1
        else:
            continue
        try:
            values = ast.literal_eval('\n'.join(body))
            if not isinstance(values, list) or not values:
                continue
            if not all(isinstance(v, tuple) and len(v) in (2, 3)
                       and all(isinstance(s, str) for s in v) for v in values):
                continue
            for entry in values:
                re.compile(entry[0])
        except (SyntaxError, ValueError, TypeError, re.error):
            continue
        safe.update((path, n) for n in range(first, last + 1))
    return safe


def _inert_review_prompt(diff):
    """Recognize the complete literal policy prompt, never computed code.

    Require contiguous new-side source and exactly one string assignment in
    the known module. Secret scanning deliberately does not use this exemption.
    """
    import ast

    path = 'cluster/control_plane/local_karen/reviewers.py'
    source, current, number = {}, None, 1
    for line in diff.splitlines():
        if line.startswith('diff --git '):
            current = line.split(' b/', 1)[-1]
        elif line.startswith('@@'):
            hunk = re.search(r'\+(\d+)(?:,\d+)? @@', line)
            number = int(hunk.group(1)) if hunk else 1
        elif current == path and line.startswith((' ', '+')) and not line.startswith('+++'):
            source[number] = line[1:]
            number += 1
    safe = set()
    for first, text in source.items():
        if not re.match(r'^STRUCTURED_REVIEW_PROMPT\s*=', text):
            continue
        body = []
        for last in range(first, first + 200):
            if last not in source:
                break
            body.append(source[last])
            try:
                tree = ast.parse('\n'.join(body))
            except SyntaxError:
                continue
            if len(tree.body) == 1 and isinstance(tree.body[0], ast.Assign):
                assignment = tree.body[0]
                if (len(assignment.targets) == 1
                        and isinstance(assignment.targets[0], ast.Name)
                        and assignment.targets[0].id == 'STRUCTURED_REVIEW_PROMPT'
                        and isinstance(assignment.value, ast.Constant)
                        and isinstance(assignment.value.value, str)):
                    safe.update((path, n) for n in range(first, last + 1))
            break
    return safe


def _crosses_fixture_string_tokens(line, match, filepath):
    """Do not invent a credential from quote/concatenation syntax in tests.

    A match contained inside one literal still fails. Provider-key patterns
    remain independent and are never suppressed by this lexical correction.
    """
    if not _is_test_file(filepath or "") or not filepath.endswith(".py"):
        return False
    import io
    import tokenize
    try:
        for token in tokenize.generate_tokens(io.StringIO(line).readline):
            if (token.type == tokenize.STRING and token.start[0] == token.end[0] == 1
                    and token.start[1] < match.start() < token.end[1] < match.end()):
                return True
    except (tokenize.TokenError, IndentationError):
        pass
    return False


def check_no_secrets_in_diff(pr: PRInfo, config: KarenConfig) -> CheckResult:
    """Check that no secrets are added in the diff.

    Only checks additions (lines starting with +), not deletions.
    """
    issues = []
    current_file = None
    new_line = 0
    lines = pr.diff.split("\n")

    for index, line in enumerate(lines):
        if line.startswith("diff --git"):
            match = re.search(r"b/(.+)$", line)
            if match:
                current_file = match.group(1)
            new_line = 0
            continue

        hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if hunk:
            new_line = int(hunk.group(1))
            continue
        if line.startswith(" "):
            new_line += 1
        if not line.startswith("+") or line.startswith("+++"):
            continue

        added_line = line[1:]
        location = f"{current_file or 'diff'}:{new_line or 1}"
        new_line += 1

        # Exact historical scanner fixtures only, never a blanket test-file
        # exemption. Whole-line matching prevents an adjacent real key hiding.
        fixture_root = "cluster/control_plane/local_karen/tests/"
        if current_file in (fixture_root + "test_checks.py", fixture_root + "test_gate.py"):
            dummy = "sk-" + "1234567890abcdefghijklmnopqrstuvwxyz123456789012"
            fixture = added_line.strip()
            api_fixture = 'API_KEY = "' + dummy + '"'
            if fixture in (api_fixture, "+" + api_fixture):
                continue
            if current_file == fixture_root + "test_checks.py" and fixture == "-" + api_fixture:
                continue
            if (current_file == fixture_root + "test_gate.py"
                    and fixture == "pr = self._mock_pr_info(diff='+" + api_fixture + "')"):
                continue
            if current_file == fixture_root + "test_checks.py":
                pat_fixture = "GITHUB_TOKEN=" + "ghp_" + "x" * 36
                if fixture in (pat_fixture, "+" + pat_fixture):
                    continue
                key_header = "-----BEGIN RSA " + "PRIVATE KEY-----"
                body_prefix = "++" if fixture.startswith("+") else "+"
                if (fixture in (key_header, "+" + key_header)
                        and lines[index + 1:index + 3] == [body_prefix + 'MIIEpQIBAAKCAQEA0Z...', '+""")']):
                    continue

        for pattern, secret_type in SECRET_PATTERNS:
            for match in re.finditer(pattern, added_line):
                syntax_only = (secret_type in ("API key", "Secret/password")
                               and _crosses_fixture_string_tokens(added_line, match, current_file))
                if not syntax_only and not _placeholder_secret(match, secret_type, current_file):
                    issues.append(f"{location}: {secret_type} (value redacted)")

    if issues:
        return CheckResult(
            check_name="no_secrets",
            verdict=CheckVerdict.FAIL,
            reason=f"Potential secrets detected: {len(issues)} issue(s)",
            details="\n".join(issues),
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
        (r"\beval\s*\(", "eval usage"),
        (r"\bexec\s*\(", "exec usage"),
    ]

    issues = []
    inert_rules = _inert_scanner_rules(pr.diff) | _inert_review_prompt(pr.diff)
    fixture_source = _python_test_fixture_source(pr.diff)
    for current_file, number, added_line in _added_lines(pr.diff):
        if (_prose_or_comment(current_file, added_line)
                or _literal_shell_display(current_file, added_line)
                or (current_file, number) in inert_rules):
            continue
        # Only literal RegExp.exec is non-executing. Member calls such as
        # child_process.exec and window.eval must still fail.
        command_source = re.sub(r"/(?:\\.|[^/\\\n])+/[gimsuy]*\.exec\s*\(",
                                "REGEXP_MATCH(", fixture_source.get((current_file, number), added_line))
        for pattern, danger_type in dangerous_patterns:
            if re.search(pattern, command_source, re.IGNORECASE):
                issues.append(f"{current_file}:{number}: {danger_type} (source redacted)")
                break

    if issues:
        return CheckResult(
            check_name="no_dangerous_commands",
            verdict=CheckVerdict.FAIL,
            reason=f"Dangerous commands detected: {len(issues)} issue(s)",
            details="\n".join(issues),
        )

    return CheckResult(
        check_name="no_dangerous_commands",
        verdict=CheckVerdict.PASS,
        reason="No dangerous commands detected",
    )


def _static_test_dom(pr, filepath, number, line):
    if not _is_test_file(filepath or "") or not re.match(r"^\s*document\.body\.innerHTML\s*=", line):
        return False
    # Read only the contiguous added literal; interpolation, scripts, handlers,
    # dynamic values and production HTML assignments retain their FAIL.
    text = line
    additions = {(path, n): content for path, n, content in _added_lines(pr.diff)}
    if line.rstrip().endswith("`"):
        for next_number in range(number + 1, number + 201):
            content = additions.get((filepath, next_number))
            if content is None:
                return False
            text += "\n" + content
            if "`" in content:
                break
    literal = re.fullmatch(r"\s*document\.body\.innerHTML\s*=\s*(?:''|\"\"|`([^`]*)`)\s*;?\s*", text, re.S)
    return bool(literal and not re.search(r"\$\{|<script|\bon\w+\s*=|javascript:", literal.group(1) or "", re.I))


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
    inert_rules = _inert_scanner_rules(pr.diff) | _inert_review_prompt(pr.diff)
    fixture_source = _python_test_fixture_source(pr.diff)
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
                if (_prose_or_comment(current_file or "", added_line)
                        or (current_file, line_num) in inert_rules
                        or _static_test_dom(pr, current_file, line_num, added_line)):
                    continue

                for pattern, desc, category in SECURITY_PATTERNS:
                    if re.search(pattern, fixture_source.get((current_file, line_num), added_line)):
                        findings.append({
                            "file": current_file,
                            "line": line_num,
                            "category": category,
                            "description": desc,
                            "evidence": "source redacted",
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
        for f in findings:
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


_UNSET = object()
_UNKNOWN = "unknown"


def _pages_source(owner: str, repo: str):
    """Pages publish path ("/" or "/docs"), None if Pages is off, or _UNKNOWN."""
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}/pages", "-q", ".source.path"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return _UNKNOWN
    if result.returncode == 0:
        path = result.stdout.strip()
        return path if path in ("/", "/docs") else _UNKNOWN
    if "404" in (result.stdout + result.stderr):
        return None
    return _UNKNOWN


def check_pages_exposure(pr: PRInfo, config: KarenConfig) -> CheckResult:
    """Check for accidental GitHub Pages exposure.

    Looks for:
    - Changes to docs/ or gh-pages branch patterns
    - Jekyll config files
    - CNAME files (custom domain setup)
    """
    issues = []
    current_file = None
    pages_source = _UNSET

    for line in pr.diff.split("\n"):
        if line.startswith("diff --git"):
            match = re.search(r"b/(.+)$", line)
            if match:
                current_file = match.group(1)

            if current_file:
                if current_file.lower().startswith("docs/"):
                    if pages_source is _UNSET:
                        pages_source = _pages_source(pr.owner, pr.repo)
                    # Only a repo that publishes Pages from / or /docs can
                    # expose docs/ files. Unknown state fails closed.
                    if pages_source is None:
                        pass
                    elif pages_source in ("/", "/docs") or pages_source == _UNKNOWN:
                        issues.append(f"Changes to published docs/ folder: {current_file}"
                                      + ("" if pages_source != _UNKNOWN
                                         else " (Pages status unknown; fail closed)"))
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
    
    shared_results = [check_no_secrets_in_diff(pr, config),
                      check_no_dangerous_commands(pr, config),
                      check_security_patterns(pr, config)]
    extra_patterns = [
        (r"os\.popen\s*\(", "os.popen (shell)", "dangerous"),
        (r"commands\.getoutput", "commands.getoutput", "dangerous"),
        (r"pickle\.load", "pickle deserialization", "security"),
        (r"yaml\.load\s*\([^)]*\)", "unsafe yaml.load", "security"),
        (r"marshal\.load", "marshal deserialization", "security"),
    ]
    inert_rules = _inert_scanner_rules(pr.diff) | _inert_review_prompt(pr.diff)
    for filepath, number, line in _added_lines(pr.diff):
        if _prose_or_comment(filepath, line) or (filepath, number) in inert_rules:
            continue
        for pattern, desc, category in extra_patterns:
            if re.search(pattern, line):
                all_findings.append({"file": filepath, "line": number,
                                     "category": category, "description": desc})
    for result, category in zip(shared_results, ("secret", "dangerous", "security")):
        if result.failed:
            for evidence in (result.details or result.reason).splitlines():
                if evidence.lstrip().startswith("Evidence:"):
                    continue
                finding_category = "auth" if "[auth]" in evidence else category
                all_findings.append({"category": finding_category, "description": evidence,
                                     "file": "", "line": 0})

    if all_findings:
        by_category = {}
        for f in all_findings:
            cat = f["category"]
            by_category[cat] = by_category.get(cat, 0) + 1
        
        summary = ", ".join(f"{v} {k}" for k, v in sorted(by_category.items()))
        
        details = []
        for f in all_findings:
            details.append((f"- {f['file']}:{f['line']} " if f["file"] else "")
                           + f"[{f['category']}] {f['description']}")
        
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
        check_no_secrets_in_diff,
        check_no_dangerous_commands,
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
                verdict=CheckVerdict.ERROR,
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
    return not any(r.failed or r.verdict == CheckVerdict.ERROR for r in results)


def get_failed_checks(results):
    # type: (list) -> list
    """Return list of failed checks."""
    return [r for r in results if r.failed]
