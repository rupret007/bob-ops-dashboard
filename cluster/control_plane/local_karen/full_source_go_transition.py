"""Pure full-source proof for a compiler-oracle update; grants no authority.

Callers must independently obtain and pin complete Git blobs. This module does
not read repositories, accept diff fragments, change policy, or publish verdicts.
"""
import ast
import copy
import hashlib
import io
import re
import tokenize
import unicodedata

CI = '.github/workflows/ci.yml'
NETWORK = '.github/workflows/cross-network-acceptance.yml'
TEST = 'tests/test_desktop_installer_workflow.py'
PATHS = frozenset((CI, NETWORK, TEST))
LIMITS = {CI: 256000, NETWORK: 16000, TEST: 64000}
VERSION = r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})'
PIN = re.compile(r'(?m)^\s*go-version: "(' + VERSION + r')"\s*$')


def _ast(text):
    return ast.dump(ast.parse(text.encode('utf-8')), include_attributes=False)


def prove_transition(old, new):
    """Return an input-bound proof, or None. No partial-context fallback exists."""
    if (type(old) is not dict or type(new) is not dict
            or set(old) != PATHS or set(new) != PATHS):
        return None
    try:
        if any(type(text) is not str or not text.endswith('\n')
               or len(text.encode('utf-8')) > LIMITS[path]
               for mapping in (old, new) for path, text in mapping.items()):
            return None
        old_pins = [m.group(1) for m in PIN.finditer(old[CI])]
        new_pins = [m.group(1) for m in PIN.finditer(new[CI])]
        if len(old_pins) != 2 or len(new_pins) != 2:
            return None
        before, after = old_pins[0], new_pins[0]
        if old_pins != [before] * 2 or new_pins != [after] * 2:
            return None
        a, b = tuple(map(int, before.split('.'))), tuple(map(int, after.split('.')))
        if a[:2] != b[:2] or b[2] <= a[2]:
            return None
        for path, count in ((CI, 2), (NETWORK, 1)):
            old_pin, new_pin = 'go-version: "' + before + '"', 'go-version: "' + after + '"'
            if (old[path].count(old_pin) != count
                    or [m.group(1) for m in PIN.finditer(old[path])] != [before] * count
                    or new[path] != old[path].replace(old_pin, new_pin)):
                return None
        if before in new[CI] + new[NETWORK]:
            return None  # The required old-version rejection must actually pass.
        # Inspect the complete old module, including bindings outside the hunk.
        if any(t.type == tokenize.NAME and unicodedata.normalize('NFKC', t.string) == 'cross_network'
               for t in tokenize.generate_tokens(io.StringIO(old[TEST]).readline)):
            return None
        tree = ast.parse(old[TEST].encode('utf-8'))
        # Python's parser recognizes some Unicode identifiers that tokenize
        # splits into multiple tokens. Its identifier fields are normalized.
        for node in ast.walk(tree):
            if any(getattr(node, field, None) == 'cross_network'
                   for field in ('id', 'arg', 'name', 'asname')):
                return None
            if isinstance(node, (ast.Global, ast.Nonlocal)) and 'cross_network' in node.names:
                return None
        first = ast.parse("assert WORKFLOW.count('go-version: \"" + before + "\"') == 2").body[0]
        targets = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name.startswith('test_') and not n.decorator_list and n.body
                   and ast.dump(n.body[0]) == ast.dump(first)
                   and not (n.args.posonlyargs or n.args.args or n.args.kwonlyargs
                            or n.args.vararg or n.args.kwarg)]
        if len(targets) != 1:
            return None
        target = targets[0]
        replacement = ast.parse(
            "assert WORKFLOW.count('go-version: \"" + after + "\"') == 2\n"
            "cross_network = (ROOT / '.github' / 'workflows' / 'cross-network-acceptance.yml').read_text(encoding='utf-8')\n"
            "assert cross_network.count('go-version: \"" + after + "\"') == 1\n"
            "assert '" + before + "' not in WORKFLOW + cross_network\n"
        ).body
        expected = copy.deepcopy(tree)
        expected.body[tree.body.index(target)].body[0:1] = replacement
        # Whole-module equality preserves setup, decorators, all other tests,
        # execution context, and every pre-existing assertion except this oracle.
        if ast.dump(expected) != _ast(new[TEST]):
            return None
        return dict(
            old_version=before, new_version=after, test=target.name,
            old_assertion_line=target.body[0].lineno,
            source_sha256={phase: {p: hashlib.sha256(text.encode()).hexdigest()
                                   for p, text in mapping.items()}
                           for phase, mapping in (('old', old), ('new', new))},
        )
    except (SyntaxError, ValueError, TypeError, UnicodeError, tokenize.TokenError,
            IndentationError, RecursionError):
        return None
