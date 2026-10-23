"""Literal test integrity with one code-owned, provenance-verified contract pair.

Kept as complete bounded source so normal reviewers can read the entire guard
without increasing immutable-source context limits. Public checks.py exports
remain available. Definitions and policy values are moved unchanged.
"""
from __future__ import annotations

import re

from .config import KarenConfig
from .contract_policy import authorized_assertion
from .check_types import CheckVerdict, CheckResult, PRInfo


TEST_FILE_PATTERNS = [
    r"test[_\-].*\.py$",
    r".*[_\-]test\.py$",
    r"tests?/.*\.py$",
    r"spec[_\-].*\.(js|ts)$",
    r".*\.spec\.(js|ts)$",
    r".*\.test\.(js|ts|mjs|cjs)$",
    r"__tests__/.*\.(js|ts)$",
]



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
