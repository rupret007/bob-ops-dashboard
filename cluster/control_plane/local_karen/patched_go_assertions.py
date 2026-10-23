"""Recognize the correlated, stricter oracle for a forward Go patch update."""
import ast
from collections import Counter
import io
import re
import textwrap
import tokenize
from unicodedata import normalize

CI = '.github/workflows/ci.yml'
NETWORK = '.github/workflows/cross-network-acceptance.yml'
PIN = re.compile(r'go-version: "(\d+)\.(\d+)\.(\d+)"')


def _replacements_keep_test_context(block, number, before, after):
    """Require an in-place replacement in the same executable test body."""
    hunk = re.search(r'(?m)^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@[^\n]*\n', block)
    if not hunk:
        return False
    rows = block[hunk.end():].splitlines()
    changed = [i for i, row in enumerate(rows) if row.startswith(('-', '+'))]
    # ASTs discard comments, including a string delimiter whose opener is outside
    # the hunk. Never move the replacement across even one unchanged source line.
    if not changed or changed[-1] - changed[0] + 1 != len(changed):
        return False
    old_source = '\n'.join(row[1:] for row in rows if row.startswith((' ', '-')))
    new_source = '\n'.join(row[1:] for row in rows if row.startswith((' ', '+')))
    try:
        # The new local must not replace an existing binding or change a later
        # reference. Tokens also cover parameters, imports and global declarations.
        if any(token.type == tokenize.NAME
               and normalize('NFKC', token.string) == 'cross_network'
               for token in tokenize.generate_tokens(io.StringIO(old_source).readline)):
            return False
        # Preserve indentation and surrounding strings/control flow. Incomplete
        # context cannot establish an executable replacement, so fail closed.
        old_tree = ast.parse(old_source)
        new_tree = ast.parse(new_source)
    except (SyntaxError, ValueError, tokenize.TokenError):
        return False
    removed_line = number - int(hunk.group(1)) + 1
    for test in old_tree.body:
        if not isinstance(test, ast.FunctionDef) or not test.name.startswith('test_'):
            continue
        for index, statement in enumerate(test.body):
            if (statement.lineno == removed_line
                    and ast.dump(statement) == ast.dump(before[0])):
                # The entire hunk must agree after this one substitution. Moving
                # assertions into a string, helper, branch or past a return fails.
                test.body[index:index + 1] = after
                return ast.dump(old_tree) == ast.dump(new_tree)
    return False


def patched_go_assertions(diff, deletions, additions):
    """Return old assertion locations only for the complete pinned transition.

    The new test must keep the exact CI lane count, additionally assert every
    cross-network pin, and reject the former compiler across both workflows.
    Other assertion/decorator removals remain the caller's normal failures.
    """
    old = [(p, n, s) for p, n, s in deletions if p.endswith('.py')]
    if len(old) != 1:
        return set()
    path, number, source = old[0]
    added = [s for p, _, s in additions if p == path]
    try:
        before = ast.parse(source.strip()).body
        after = ast.parse(textwrap.dedent('\n'.join(added))).body
        old_pin = before[0].test.left.args[0].value
        new_pin = after[0].test.left.args[0].value
        count = before[0].test.comparators[0].value
        network_count = after[2].test.comparators[0].value
    except (SyntaxError, IndexError, AttributeError, TypeError, ValueError):
        return set()
    if not (isinstance(old_pin, str) and isinstance(new_pin, str)
            and type(count) is int and count > 0
            and type(network_count) is int and network_count > 0):
        return set()
    old_match, new_match = PIN.fullmatch(old_pin), PIN.fullmatch(new_pin)
    if not old_match or not new_match:
        return set()
    try:
        previous = tuple(map(int, old_match.groups()))
        current = tuple(map(int, new_match.groups()))
    except ValueError:
        return set()
    if previous[:2] != current[:2] or current[2] <= previous[2]:
        return set()
    version = '.'.join(map(str, previous))
    old_expected = ast.parse(f'assert WORKFLOW.count({old_pin!r}) == {count}').body
    new_expected = ast.parse(
        f'assert WORKFLOW.count({new_pin!r}) == {count}\n'
        'cross_network = (ROOT / ".github" / "workflows" / '
        '"cross-network-acceptance.yml").read_text(encoding="utf-8")\n'
        f'assert cross_network.count({new_pin!r}) == {network_count}\n'
        f'assert {version!r} not in WORKFLOW + cross_network\n').body
    dump = lambda nodes: [ast.dump(n, include_attributes=False) for n in nodes]
    if dump(before) != dump(old_expected) or dump(after) != dump(new_expected):
        return set()
    expected = Counter({CI: count, NETWORK: network_count})
    removed_pins, added_pins = Counter(), Counter()
    for rows, expected_pin, counts in [(deletions, old_pin, removed_pins),
                                       (additions, new_pin, added_pins)]:
        for file, _, line in rows:
            if file in expected:
                if line.strip() != expected_pin:
                    return set()
                counts[file] += 1
    if removed_pins != expected or added_pins != expected:
        return set()
    # A changed test file may not be paired with pin edits from another hunk.
    blocks = diff.split('diff --git ')[1:]
    target = [b for b in blocks if b.splitlines()[0].endswith(' b/' + path)]
    if len(target) != 1 or len(re.findall(r'(?m)^@@ ', target[0])) != 1:
        return set()
    if not _replacements_keep_test_context(target[0], number, before, after):
        return set()
    return {(path, number)}
