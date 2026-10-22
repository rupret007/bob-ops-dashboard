"""One exact test-contract correction, proved from immutable Git objects.

Only the code-owned APPROVED repository/PR/full head/frozen baseline/file/test
and assertion pair can discharge its unique removed-assertion finding. The full
canonical diff and complete old/new source must match. PR prose, labels,
configuration and model votes cannot supply authority. Every other deterministic
check, deletion/skip/decorator rule, credential rule, context/budget limit, claim,
reviewer, signature/delivery, CI and human-only merge requirement remains.

BASE_ADVANCEMENTS pins fourteen separately reviewed source edges from the frozen
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

from dataclasses import dataclass
import re


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
    ('ae87e802837d453de6e0ed394c2a4564a25efc98', '469feba9579e04fe240d22306af92735d25ee65c'),
)


ADMITTED_BASE = 'ae87e802837d453de6e0ed394c2a4564a25efc98'


POLICY_FILES = frozenset({
    'cluster/control_plane/local_karen/tests/contract_policy/__init__.py',
    'cluster/control_plane/local_karen/contract_record.py',
    'cluster/control_plane/local_karen/tests/contract_policy/test_contract_runtime.py',
    'cluster/control_plane/local_karen/tests/contract_policy/test_contract_fetch.py',
    'cluster/control_plane/local_karen/tests/contract_policy/test_contract_ancestry.py',
    'cluster/control_plane/local_karen/checks.py',
    'cluster/control_plane/local_karen/github_client.py',
    'cluster/control_plane/local_karen/contract_policy.py',
    'cluster/control_plane/local_karen/tests/contract_policy/conftest.py',
    'cluster/control_plane/local_karen/tests/contract_policy/test_contract_client.py',
    'cluster/control_plane/local_karen/tests/contract_policy/test_contract_objects.py',
})


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
