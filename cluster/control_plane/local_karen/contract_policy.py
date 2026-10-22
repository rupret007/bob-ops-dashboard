"""One exact test-contract correction, proved from immutable Git objects.

Code-owned policy only; configuration, labels, PR prose and model votes cannot
supply an exception. Repository/PR/full head/frozen baseline/file/function and
exact assertion pair are bound below. Only that unique removed assertion is
discharged. Every other deletion, decorator, skip, credential, reviewer, CI,
signature, delivery, current-head and human-merge requirement remains unchanged.

Frozen packet: rupret007/bob-ops-dashboard#104, head
1f45a6f87a5ff8588bb4c4cb5b58a5690cc4b582, baseline
c5650be27329e507fe73ef5fc7a3905b64991469. The named direct assertion changes
semantic-yellow admission to rejection. Its full canonical diff digest must
match APPROVED. Complete immutable old/new source must prove the unique direct
assertion in the exact test. Changed packets require a new decision.

Nine separately reviewed source advancements are explicitly pinned:

PR | reviewed head | sole-parent merged commit | Nuc native COMPLETE/PASS SHA256
100 | 62c64bdd27da4fcd1a07ac9b3be43d60663f86b0 | c3374b769820408edd43cdd4abf1eed5efb0e65b | e1df7fe6ed9fb78e0a9dab4decc208c624da82f60eac30f7bb49f28677b2dd6b
107 | a0fe6d8f780105c4ec6f667d7be90ec680e75b5a | 21242f72725cd64065b37656e6c269676793f7aa | 8ba0d6a4796a4f355a50f7c76f3a1fdb638e3a253e056a6bde92cecf11e24763
108 | 330d9a91c77b8efbdc21743633910f4d18f01d10 | 079d0a184895556f64e8aa9806211c13a39fa1c8 | 38d4944e8464f42b923d0977d7e6c3f8c4ab6e438824dc14bc296f60991d2015
109 | 3b926929292274aea028c4d9fc86428884f32ab4 | 26384733115ce9803a61298e8fb49c4eb3b8780a | bd7883ad52cbdc2c607d968aaa9090805bba102d709f21ba805b260674641c04
116 | 0bb497e951f68bf2314b06ce2351d5b222ea3cc5 | 85f411af86dd8ceb2f829c24801538217d4ff071 | 03dbc253a0e63107a4d9d786aa02ffe3362ad745ce72442bb922060f02c5cc36
123 | 92ee51bfd18a22179623cf6832e90396078fd647 | 3905ca1ad5f3f581a8c8d1d64bda0b52db679ea7 | 21737214201b385ee01e426edc19bcc653aae6881a1bf15a7f51941fb7f67d37
128 | 2f0059e556f4e92df8c31f0af12d17d9fafad4e9 | 4042b8998234699fcf0a1882c26e6b7a202837ef | fe1b1dfa78b71ce4297c499770601c7ee79b3f17e10000d687e1a615253df0fa
129 | 78519f4bbaca3984153aa5f83ca9e1778ef05d6a | 12e4d8c3c01f81bbc528fb77ab973769b376a7ea | 3ca8a9619f18b9d76f5de3c727f1618040ceb738d3f8206440bd37f8b9285247
131 | 6005a9252752a2d22a3d91ef35a2a707e9abbf0d | 33b0ddcf2fdbc4dfb2c6cff2c9d30d6cbb0df1f6 | d308bc9a023b2c53600e8d44eb7bf8d964dbf9bc6ff81ce70e152178b5c0825b

Each merge's sole parent is the preceding row, starting at the frozen baseline.
Missing/reversed/skipped edges, extra parents and unlisted later changes fail.
Squash ancestry is not assumed: reviewed/merged objects and exact diffs are proved.
PR109 tree:2d444c51919451be3d725b602295964ff333dff3;
PR116 tree:54f9f490d4ed6b8d2e77f93ed4c43c52273bbc31;
PR123 tree:6a1c35fd36e3d0b585a31022f8a83c8cea8c587d.
PR128 reviewed/merged tree:8003c9606f1de396add0e0c1d07b1dc55dfce55d.
PR129 was reviewed on3905, tree7d5f7610bf83fa951da0bf1d464cddb88e77a354.
Its exact full-index diff equals4042->12e4, retaining the separately reviewed128.
PR129 reviewed/merged diff SHA256:
3f3077b752786e62f3fe8f6dc6dd48bd75aadc8d23b2627d29d89b8bcc9393a8.
Receiving12e4 tree:bfa795f6bbcf3baf57606aee573facaed06224da.
PR131 reviewed/merged tree:17b01ac79a279b2ffb1f1912b2ed5f184ed28999.

Native Nuc COMPLETE/PASS, canonical/LATEST identity and normal reviewed predicate
are recorded for these immutable heads.128/129 have nine checks each and9/3
Codex/Gemini/MiniMax votes;131 has nine checks/three votes. Budgeted GitHub reads
confirm their merges and CI; original-owner receiving qualification is separate.
The latest owner direction retains90% Mini admission; this policy changes no
resource limit, pending decision or release hold. No copied verdict, private-key
access, fresh signature claim or synthetic human approval grants authority.

The head/base merge base must remain the frozen baseline. ADMITTED_BASE must be
an ancestor of the live base. Live base must equal BOTH installed and loaded
source revisions with the ordinary runtime source check passing. Its complete
tree difference from ADMITTED_BASE must contain exactly POLICY_FILES,
each regular100644 and byte-identical to this loaded policy snapshot. This
permits the separately reviewed policy merge without arbitrary base drift or
a stale/unreviewed runtime overlay. Missing immutable objects, source mismatch,
packet drift or unproved advancement refuses before paid review.

The process captures its loaded base once at policy import. Later source-head
reporting cannot relabel unchanged Karen files as a newly installed release.
The client proves the named live branch separately from the PR's saved base OID
and rechecks both observations. Normal final SHA verification also requires this
same loaded base, so a branch/install change during review refuses.

This policy head needs normal exact-head Karen/CI/signature/delivery and the
human-only merge process, then qualified existing-owner installation before104
can reopen for its OWN normal review. The original106 rejection remains.
Existing review byte limits still apply; no cap waiver is implemented here.
No new source may merge into the target before104 without a separately proved,
normally reviewed policy update. Neither this record nor private tests grant
review, admission, healthy deployment or recovery authority.
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
)
ADMITTED_BASE = '33b0ddcf2fdbc4dfb2c6cff2c9d30d6cbb0df1f6'

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
_STARTUP_STATUS = deployment_status()
_LOADED_BASE = (_STARTUP_STATUS.get('loaded_head')
                if _STARTUP_STATUS.get('source_matches') is True else None)
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
