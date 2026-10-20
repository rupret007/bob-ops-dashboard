"""One reviewed test-contract correction, proved from immutable Git objects.

This is code-owned policy, never configuration, PR prose or a model waiver.
The live base may advance only by this installed policy's exact file contents.

# PR #104 test-contract correction

This code-owned policy permits one assertion correction in
`rupret007/bob-ops-dashboard#104`, full head
`1f45a6f87a5ff8588bb4c4cb5b58a5690cc4b582`, frozen baseline
`c5650be27329e507fe73ef5fc7a3905b64991469`.
In `test_target_gate_requires_direct_fresh_exact_active_observation`, the old
assertion admits `semantic-yellow`; the new assertion rejects it. Unknown or
failed health must not qualify a canary. The exact assertion pair, source
location and canonical complete diff digest are recorded in `contract_policy.py`.

The proof comes from fetched immutable Git objects, never PR text, labels or
configuration. Both complete source files are parsed to identify the unique
assertion directly in the named test. The complete frozen-baseline/head diff
must match the approved digest; any changed packet needs a new decision.

## Frozen baseline and advancing target

The head/base merge base must remain the frozen baseline. Five explicit
separately reviewed base advancements are admitted. The first is
`c3374b769820408edd43cdd4abf1eed5efb0e65b`, the human merge of PR #100,
with sole parent `c5650be27329e507fe73ef5fc7a3905b64991469`.
Its reviewed head is `62c64bdd27da4fcd1a07ac9b3be43d60663f86b0`; the
native Nuc's exact-head COMPLETE/PASS artifact SHA-256 is
`e1df7fe6ed9fb78e0a9dab4decc208c624da82f60eac30f7bb49f28677b2dd6b`.
Revalidation found nine deterministic passes and four independent Gemini/
MiniMax PASS votes accepted by the existing normal approval predicate.
No verdict copy, signing-key provision or public-label inference was used.

The second is PR #107's human-merged commit
`21242f72725cd64065b37656e6c269676793f7aa`, with sole parent c337 above.
Its reviewed head is `a0fe6d8f780105c4ec6f667d7be90ec680e75b5a`.
The Nuc-native exact-head COMPLETE/PASS artifact SHA-256 is
`8ba0d6a4796a4f355a50f7c76f3a1fdb638e3a253e056a6bde92cecf11e24763`;
normal authority revalidation found nine checks and six Gemini/MiniMax PASS
votes accepted by the existing approval predicate. GitHub confirmed green
CI on that head and its actual merged release at 2026-10-20 02:34 UTC.

The third is PR #108's human-merged commit
`079d0a184895556f64e8aa9806211c13a39fa1c8`, with sole parent 21242f above.
Its reviewed head is `330d9a91c77b8efbdc21743633910f4d18f01d10`.
The Nuc-native exact-head COMPLETE/PASS artifact SHA-256 is
`38d4944e8464f42b923d0977d7e6c3f8c4ab6e438824dc14bc296f60991d2015`;
normal authority revalidation found nine checks and four Gemini/MiniMax PASS
votes accepted by the existing approval predicate. Independent REST metadata
confirms Jeff's human merge at 2026-10-20 03:51:28 UTC; current GitHub checks
confirm green CI on that exact head and merged release. No artifact or signing
key was copied. The earlier policy review for 17346a1a remains historical;
this additional pinned transition requires a new policy head and normal review.

The fourth is PR #109's human-merged commit
`26384733115ce9803a61298e8fb49c4eb3b8780a`, with sole parent 079d0a above.
Its reviewed head is `3b926929292274aea028c4d9fc86428884f32ab4`.
The exact-head COMPLETE/PASS artifact SHA-256 on both native nodes is
`bd7883ad52cbdc2c607d968aaa9090805bba102d709f21ba805b260674641c04`;
fresh validation found nine deterministic passes and independent Gemini and
MiniMax PASS votes. The prior normal signed receiver acknowledgment is retained.
GitHub confirmed the human merge at 2026-10-20 12:28:54 UTC and green release
CI. Its single-parent squash commit has the exact reviewed Git tree
`2d444c51919451be3d725b602295964ff333dff3`; reviewed-head ancestry is not assumed.
The Nuc's normally installed module copy matches this release. This is source
qualification, not a claim of whole-host health or Mini readiness. The new
baseline still requires this policy head's own complete normal review.

The fifth is PR #116's human-merged commit
`85f411af86dd8ceb2f829c24801538217d4ff071`, with sole parent 2638473 above.
Its reviewed head is `0bb497e951f68bf2314b06ce2351d5b222ea3cc5`.
The native Nuc exact/latest COMPLETE/PASS artifact SHA-256 is
`03dbc253a0e63107a4d9d786aa02ffe3362ad745ce72442bb922060f02c5cc36`.
Fresh normal authority validation found nine deterministic PASS checks and six
complete Codex/Gemini/MiniMax PASS votes with no outstanding context requests.
GitHub confirmed Jeff's merge at 2026-10-20 20:12:39 UTC and green reviewed-head
CI. The sole-parent squash commit has the exact reviewed tree
`54f9f490d4ed6b8d2e77f93ed4c43c52273bbc31`. The policy review still requires
the legitimate installed context, its own new-head CI and normal Karen review;
this additional edge grants no runtime or review authority by itself.

Every sole-parent edge from the frozen baseline to this explicit admitted
commit is proved from immutable objects. Missing, reversed, skipped or
additional-parent transitions fail; no later unlisted release is permitted.

The final admitted commit must be an ancestor of the live base. The live base must equal the installed
and loaded source revision, verified by the existing runtime source check.
Its complete tree difference from that admitted commit must contain exactly the four
policy files listed in the module, each with regular-file mode and content
identical to the policy snapshot loaded by that process. Consequently a
separate reviewed policy merge can advance the target without admitting
unrelated base changes or accepting a stale running policy.

Merge and install this policy through the existing review/admission process
before reopening #104. Do not merge another change into its target first;
that changes this proof's permitted baseline transition and requires a new
reviewed decision. No unreviewed runtime overlay qualifies.

This operational note is embedded in the policy module docstring and travels
with the normal module copy. It adds no separate document to the publication
directory or supplemental review packet. The four policy code/test files remain
bound to their exact installed and loaded bytes.

The typed proof discharges only one removed assertion. The checker continues
to inspect every other assertion, removed test/file/decorator and added skip.
All other deterministic checks, independent reviewers, CI, signatures, delivery,
current-head requirements and human-only repository merges remain in force.
Missing objects, packet drift, source mismatch and unproved base advancement
stop fetching before paid review. Malformed typed proofs never discharge a
finding. This policy does not itself
approve #104 or establish healthy deployment or recovery.
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

# Separately reviewed and human-merged PR100, PR107, PR108, PR109 and PR116. Every ancestry edge is
# pinned; this is not a live-base wildcard. The #104 packet remains unchanged.
BASE_ADVANCEMENTS = (
    ('c3374b769820408edd43cdd4abf1eed5efb0e65b', 'c5650be27329e507fe73ef5fc7a3905b64991469'),
    ('21242f72725cd64065b37656e6c269676793f7aa', 'c3374b769820408edd43cdd4abf1eed5efb0e65b'),
    ('079d0a184895556f64e8aa9806211c13a39fa1c8', '21242f72725cd64065b37656e6c269676793f7aa'),
    ('26384733115ce9803a61298e8fb49c4eb3b8780a', '079d0a184895556f64e8aa9806211c13a39fa1c8'),
    ('85f411af86dd8ceb2f829c24801538217d4ff071', '26384733115ce9803a61298e8fb49c4eb3b8780a'),
)
ADMITTED_BASE = '85f411af86dd8ceb2f829c24801538217d4ff071'

POLICY_FILES = frozenset({
    'cluster/control_plane/local_karen/checks.py',
    'cluster/control_plane/local_karen/github_client.py',
    'cluster/control_plane/local_karen/contract_policy.py',
    'cluster/control_plane/local_karen/tests/test_contract_policy.py',
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
