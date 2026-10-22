"""Read-only proof of the frozen104 Git packet; no checker authorization.

The caller must supply a bounded immutable Git-object reader. This verifies
only the code-owned packet, not installed source, live/saved base, ancestry,
startup binding or current-head freshness. Its Packet cannot grant a test
exception. Activation still requires those separate proofs and normal review,
CI, signatures/delivery, human merge and qualified owner installation.
"""
import ast
from dataclasses import dataclass
import hashlib
import subprocess


@dataclass(frozen=True)
class Contract:
    owner: str
    repo: str
    number: int
    head: str
    baseline: str
    path: str
    function: str
    old: str
    new: str
    old_line: int
    diff_sha256: str


APPROVED = Contract(
    'rupret007', 'bob-ops-dashboard', 104,
    '1f45a6f87a5ff8588bb4c4cb5b58a5690cc4b582',
    'c5650be27329e507fe73ef5fc7a3905b64991469',
    'cluster/control_plane/tests/test_update_coordination.py',
    'test_target_gate_requires_direct_fresh_exact_active_observation',
    "    assert updater.target_canary_ready('mini', TARGET) is (change in ('none', 'semantic-yellow'))",
    "    assert updater.target_canary_ready('mini', TARGET) is (change == 'none')",
    185, '5012f4fa1be0aaf9d271cf210961c600caa20b57509d08ddbee1cf17a40fe956',
)
DIFF_ARGS = ('diff', '--full-index', '--no-ext-diff', '--no-textconv',
             '--no-renames', '--binary', '--no-color', '--no-relative',
             '--diff-algorithm=myers', '--indent-heuristic', '--unified=3',
             '--inter-hunk-context=0', '--src-prefix=a/', '--dst-prefix=b/', '--line-prefix=')


class ContractUnavailable(Exception):
    """Fixed diagnostic without unproved source or reader output."""


@dataclass(frozen=True)
class Packet:
    diff: str
    files: tuple
    new_line: int


def require(condition):
    if not condition:
        raise ContractUnavailable('Exact frozen Git packet unavailable')


def assertion_line(source, text):
    lines = source.decode('utf-8').splitlines()
    functions = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef) and node.name == APPROVED.function]
    require(len(functions) == 1 and lines.count(text) == 1)
    assertions = [node for node in functions[0].body if isinstance(node, ast.Assert)
                  and node.lineno == node.end_lineno and lines[node.lineno - 1] == text]
    require(len(assertions) == 1)
    return assertions[0].lineno


def prove_packet(owner, repo, number, head, *, git):
    """Prove only frozen objects. No runtime state, network or gate mutation."""
    require(type(number) is int and (owner, repo, number, head) ==
            (APPROVED.owner, APPROVED.repo, APPROVED.number, APPROVED.head))
    require(callable(git))

    def read(*args):
        value = git(*args)
        require(type(value) is bytes and len(value) <= 1024 * 1024)
        return value

    try:
        raw = read(*DIFF_ARGS, APPROVED.baseline, APPROVED.head)
        require(hashlib.sha256(raw).hexdigest() == APPROVED.diff_sha256)
        old = read('show', APPROVED.baseline + ':' + APPROVED.path)
        new = read('show', APPROVED.head + ':' + APPROVED.path)
        require(assertion_line(old, APPROVED.old) == APPROVED.old_line)
        new_line = assertion_line(new, APPROVED.new)
        names = read('diff', '--name-status', '--no-renames', '-z',
                     APPROVED.baseline, APPROVED.head).decode('utf-8').split('\0')
        require(names[-1] == '' and len(names) % 2 == 1)
        files = tuple((names[i + 1], names[i]) for i in range(0, len(names) - 1, 2))
        require(files and len({path for path, status in files}) == len(files)
                and all(path and status in ('A', 'M', 'D') for path, status in files)
                and (APPROVED.path, 'M') in files)
        return Packet(raw.decode('utf-8'), files, new_line)
    except (OSError, ValueError, SyntaxError, UnicodeError, subprocess.SubprocessError):
        raise ContractUnavailable('Exact frozen Git packet unavailable') from None
