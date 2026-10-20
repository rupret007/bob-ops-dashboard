"""One reviewed test-contract correction, proved from immutable Git objects.

This is code-owned policy, never configuration, PR prose or a model waiver.
The live base may advance only by this installed policy's exact file contents.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .runtime_status import deployment_status


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

# Separately reviewed and human-merged PR100 and PR107. Every ancestry edge is
# pinned; this is not a live-base wildcard. The #104 packet remains unchanged.
BASE_ADVANCEMENTS = (
    ('c3374b769820408edd43cdd4abf1eed5efb0e65b', 'c5650be27329e507fe73ef5fc7a3905b64991469'),
    ('21242f72725cd64065b37656e6c269676793f7aa', 'c3374b769820408edd43cdd4abf1eed5efb0e65b'),
)
ADMITTED_BASE = '21242f72725cd64065b37656e6c269676793f7aa'

POLICY_FILES = frozenset({
    'cluster/control_plane/local_karen/checks.py',
    'cluster/control_plane/local_karen/github_client.py',
    'cluster/control_plane/local_karen/contract_policy.py',
    'cluster/control_plane/local_karen/tests/test_contract_policy.py',
    'cluster/control_plane/local_karen/PR104_CONTRACT_POLICY.md',
})
_ROOT = Path(__file__).resolve().parents[3]


def _snapshot_policy(module_root):
    # All policy files travel in the normal module copy. Missing evidence must
    # not break unrelated reviews.
    try:
        return {p: (module_root / p).read_bytes() for p in POLICY_FILES}
    except OSError:
        return {}


# Snapshot before any updater can replace files beneath this process.
_LOADED_FILES = _snapshot_policy(_ROOT)
_SHA = re.compile(r'[0-9a-f]{40}')
_DIFF_ARGS = ('diff', '--full-index', '--no-ext-diff', '--no-textconv',
              '--no-renames', '--binary', '--no-color', '--no-relative',
              '--diff-algorithm=myers', '--indent-heuristic', '--unified=3',
              '--inter-hunk-context=0', '--src-prefix=a/', '--dst-prefix=b/', '--line-prefix=')


class ContractUnavailable(Exception):
    """Fixed, non-sensitive failure; no unproved correction is granted."""


@dataclass(frozen=True)
class VerifiedContract:
    contract: Contract
    base: str
    diff_sha256: str
    files: tuple
    deleted: tuple
    new_line: int


def is_candidate(owner, repo, number, head):
    return (owner, repo, number, head) == (
        APPROVED.owner, APPROVED.repo, APPROVED.number, APPROVED.head)


def _require(condition):
    if not condition:
        raise ContractUnavailable('Exact test-contract provenance not established')


def _assertion_line(source, text):
    """Require the unique exact assertion directly in the named test body."""
    lines = source.decode('utf-8').splitlines()
    tree = ast.parse(source)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name == APPROVED.function]
    _require(len(functions) == 1 and lines.count(text) == 1)
    assertions = [n for n in functions[0].body if isinstance(n, ast.Assert)
                  and n.lineno == n.end_lineno and lines[n.lineno - 1] == text]
    _require(len(assertions) == 1)
    return assertions[0].lineno


def _prove(base, git):
    """git reads a freshly fetched bare object store; it never checks out code."""
    _require(isinstance(base, str) and _SHA.fullmatch(base) is not None)
    runtime = deployment_status()
    _require(runtime.get('source_matches') is True
             and runtime.get('installed_head') == runtime.get('loaded_head') == base)
    _require(set(_LOADED_FILES) == POLICY_FILES)
    _require(git('merge-base', base, APPROVED.head).decode().strip() == APPROVED.baseline)
    previous = APPROVED.baseline
    _require(type(BASE_ADVANCEMENTS) is tuple and bool(BASE_ADVANCEMENTS))
    for commit, parent in BASE_ADVANCEMENTS:
        _require(parent == previous and isinstance(commit, str) and _SHA.fullmatch(commit) is not None)
        _require(git('rev-list', '--parents', '-n', '1', commit).decode().split() == [commit, parent])
        previous = commit
    _require(previous == ADMITTED_BASE)
    _require(git('merge-base', ADMITTED_BASE, base).decode().strip() == ADMITTED_BASE)
    changed_base = git('diff', '--name-only', '--no-renames', '-z', ADMITTED_BASE, base)
    paths = changed_base.decode().rstrip('\0').split('\0') if changed_base else []
    # No arbitrary base drift, even when the PR head has not moved. Every policy
    # file must be the exact snapshot loaded by this normally installed source.
    _require(len(paths) == len(POLICY_FILES) and set(paths) == POLICY_FILES)
    for path in sorted(POLICY_FILES):
        entry = git('ls-tree', base, '--', path).decode().strip()
        _require(re.fullmatch(r'100644 blob [0-9a-f]{40}\t' + re.escape(path), entry) is not None)
        _require(git('show', base + ':' + path) == _LOADED_FILES[path])

    raw = git(*_DIFF_ARGS, APPROVED.baseline, APPROVED.head)
    digest = hashlib.sha256(raw).hexdigest()
    _require(digest == APPROVED.diff_sha256)
    old_source = git('show', APPROVED.baseline + ':' + APPROVED.path)
    new_source = git('show', APPROVED.head + ':' + APPROVED.path)
    _require(_assertion_line(old_source, APPROVED.old) == APPROVED.old_line)
    new_line = _assertion_line(new_source, APPROVED.new)
    names = git('diff', '--name-status', '--no-renames', '-z', APPROVED.baseline, APPROVED.head).decode().split('\0')
    _require(names[-1] == '' and len(names) % 2 == 1)
    files = [{'path': names[i + 1], 'status': 'removed' if names[i] == 'D' else 'modified'}
             for i in range(0, len(names) - 1, 2)]
    proof = VerifiedContract(APPROVED, base, digest, tuple(f['path'] for f in files),
                             tuple(f['path'] for f in files if f['status'] == 'removed'), new_line)
    return raw.decode('utf-8'), files, proof


def fetch_contract_packet(owner, repo, number, head, base):
    """Candidate-only bounded Git fetch; metadata cannot provide its own proof."""
    _require(is_candidate(owner, repo, number, head))
    _require(isinstance(base, str) and _SHA.fullmatch(base) is not None)
    deadline = time.monotonic() + 180
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS='1', GIT_TERMINAL_PROMPT='0', GIT_ATTR_NOSYSTEM='1')
    try:
        with tempfile.TemporaryDirectory(prefix='karen-contract-') as directory:
            def git(*args):
                remaining = deadline - time.monotonic()
                _require(remaining > 0)
                result = subprocess.run(
                    ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.attributesFile=/dev/null', *args],
                    cwd=directory, env=env, capture_output=True, timeout=min(90, remaining))
                _require(result.returncode == 0 and len(result.stdout) <= 1024 * 1024)
                return result.stdout
            git('init', '--bare', '-q')
            git('fetch', '--no-tags', '--filter=blob:none',
                'https://github.com/rupret007/bob-ops-dashboard.git', head, APPROVED.baseline,
                *(commit for commit, parent in BASE_ADVANCEMENTS), base)
            return _prove(base, git)
    except (OSError, ValueError, SyntaxError, UnicodeError, subprocess.SubprocessError):
        raise ContractUnavailable('Exact test-contract objects unavailable') from None


def authorized_assertion(pr):
    """Return one eligible finding, or nothing. No whole-check early PASS."""
    proof = pr.test_contract_proof
    if (type(proof) is not VerifiedContract or proof.contract != APPROVED
            or not is_candidate(pr.owner, pr.repo, pr.number, pr.head_sha)
            or not isinstance(proof.base, str) or proof.base != pr.base_sha
            or not _SHA.fullmatch(proof.base) or type(proof.new_line) is not int or proof.new_line <= 0
            or proof.diff_sha256 != APPROVED.diff_sha256
            or hashlib.sha256(pr.diff.encode()).hexdigest() != proof.diff_sha256
            or tuple(pr.changed_files) != proof.files or tuple(pr.deleted_files) != proof.deleted):
        return None
    return (APPROVED.path, APPROVED.old_line, APPROVED.old, proof.new_line, APPROVED.new)
