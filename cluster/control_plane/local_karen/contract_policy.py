"""One exact test-contract correction, proved from immutable Git objects.

Only the code-owned APPROVED repository/PR/full head/frozen baseline/file/test
and assertion pair can discharge its unique removed-assertion finding. The full
canonical diff and complete old/new source must match. PR prose, labels,
configuration and model votes cannot supply authority. Every other deterministic
check, deletion/skip/decorator rule, credential rule, context/budget limit, claim,
reviewer, signature/delivery, CI and human-only merge requirement remains.

BASE_ADVANCEMENTS pins thirteen separately reviewed source edges from the frozen
baseline through ADMITTED_BASE. Each merged commit must have exactly its stated
sole parent. The reviewed/merged patch identities and native exact-head verdicts
are recorded in the PR's provenance evidence; no live-base wildcard is allowed.
Missing/reversed/skipped edges or unlisted source changes refuse.

The live policy base must descend from ADMITTED_BASE with an exact tree delta
of POLICY_FILES, each a regular100644 file matching the loaded snapshot. It must
match the normally installed and loaded runtime source. The review client binds
that base once at import; missing startup evidence never gains authority later.
Named live branch and saved PR base are observed separately and rechecked; final
head verification also requires the bound runtime base.

This policy needs normal review, merge and qualified owner installation before
frozen104 receives its own normal review. The original106 rejection is retained.
New source outside this exact policy delta requires another proved, normally
reviewed policy update. Private tests grant no review, admission or recovery
qualification and no current lock, hold or resource limit is changed.
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

# Separately reviewed and human-merged source advancements. Every ancestry edge is
# pinned; this is not a live-base wildcard. The #104 packet remains unchanged.
BASE_ADVANCEMENTS = (
    ('c3374b769820408edd43cdd4abf1eed5efb0e65b', 'c5650be27329e507fe73ef5fc7a3905b64991469'),
    ('21242f72725cd64065b37656e6c269676793f7aa', 'c3374b769820408edd43cdd4abf1eed5efb0e65b'),
    ('079d0a184895556f64e8aa9806211c13a39fa1c8', '21242f72725cd64065b37656e6c269676793f7aa'),
    ('26384733115ce9803a61298e8fb49c4eb3b8780a', '079d0a184895556f64e8aa9806211c13a39fa1c8'),
    ('85f411af86dd8ceb2f829c24801538217d4ff071', '26384733115ce9803a61298e8fb49c4eb3b8780a'),
    ('3905ca1ad5f3f581a8c8d1d64bda0b52db679ea7', '85f411af86dd8ceb2f829c24801538217d4ff071'),
    ('4042b8998234699fcf0a1882c26e6b7a202837ef', '3905ca1ad5f3f581a8c8d1d64bda0b52db679ea7'),
    ('12e4d8c3c01f81bbc528fb77ab973769b376a7ea', '4042b8998234699fcf0a1882c26e6b7a202837ef'),
    ('33b0ddcf2fdbc4dfb2c6cff2c9d30d6cbb0df1f6', '12e4d8c3c01f81bbc528fb77ab973769b376a7ea'),
    ('47dc0f25171f02713d85a67799f2f889895bc411', '33b0ddcf2fdbc4dfb2c6cff2c9d30d6cbb0df1f6'),
    ('fafdb38fb964a59449fc20807c54c68daed5aad2', '47dc0f25171f02713d85a67799f2f889895bc411'),
    ('02b6f8e7137df8c72a408c54809c958360415560', 'fafdb38fb964a59449fc20807c54c68daed5aad2'),
    ('469feba9579e04fe240d22306af92735d25ee65c', '02b6f8e7137df8c72a408c54809c958360415560'),
)
ADMITTED_BASE = '469feba9579e04fe240d22306af92735d25ee65c'

POLICY_FILES = frozenset({
    'cluster/control_plane/local_karen/checks.py',
    'cluster/control_plane/local_karen/github_client.py',
    'cluster/control_plane/local_karen/contract_policy.py',
    'cluster/control_plane/local_karen/tests/contract_policy/conftest.py',
    'cluster/control_plane/local_karen/tests/contract_policy/test_contract_client.py',
    'cluster/control_plane/local_karen/tests/contract_policy/test_contract_objects.py',
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


def _startup_base():
    try:
        runtime = deployment_status()
        if not isinstance(runtime, dict): return None
        head = runtime.get('loaded_head')
        return head if (runtime.get('source_matches') is True
                        and isinstance(head, str) and _SHA.fullmatch(head)) else None
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return None


_UNSET = object()
_LOADED_BASE = _UNSET


def bind_startup_base():
    global _LOADED_BASE
    if _LOADED_BASE is _UNSET: _LOADED_BASE = _startup_base()


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


def runtime_base_matches(base):
    runtime = deployment_status()
    return (isinstance(base, str) and _SHA.fullmatch(base) is not None
            and base == _LOADED_BASE and set(_LOADED_FILES) == POLICY_FILES
            and runtime.get('source_matches') is True
            and runtime.get('installed_head') == runtime.get('loaded_head') == base)


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
    _require(base == _LOADED_BASE)
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
