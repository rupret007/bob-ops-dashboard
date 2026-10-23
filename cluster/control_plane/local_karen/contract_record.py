"""Code-owned frozen #104 identity and the complete loaded policy snapshot.

Only the complete verifier and normal installed assertion hook may consume this
record. Every source file in POLICY_FILES must exist, match immutable Git and
loaded snapshot bytes, and the runtime base must be bound at startup. An empty
snapshot must never authorize a proof. All original test/fixture assertions and
other identity, source, diff, deletion/skip, reviewer, CI, signature/delivery,
qualified installation and release/maintenance requirements remain unchanged.

This private complete candidate is not review or installation authority; #104
still requires its own normal review before #105 may merge.
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
    ('a59dd53773bca832783d97215a7dd8fb25b8213d', 'ae87e802837d453de6e0ed394c2a4564a25efc98'),
    ('56e091c81f851f7d0233389e5725bb263c102fc3',
     ('a59dd53773bca832783d97215a7dd8fb25b8213d', '1dc2f6f192f96eb82eb4047c3513288997e30963')),
    ('8918bfa0286cff2e4bf42cac5d22ce8b836c60da', '56e091c81f851f7d0233389e5725bb263c102fc3'),
    ('6de09797a3f224da02825f71592452afb80d6f6c', '8918bfa0286cff2e4bf42cac5d22ce8b836c60da'),
    ('330baa66c69c6dbad29ec85a8123d4475c078111', '6de09797a3f224da02825f71592452afb80d6f6c'),
    ('0b65227dd8e20fa6e291b591b469b917c7d3bd40', '330baa66c69c6dbad29ec85a8123d4475c078111'),
)


ADMITTED_BASE = '0b65227dd8e20fa6e291b591b469b917c7d3bd40'


POLICY_FILES = frozenset({
    'cluster/control_plane/local_karen/assertion_policy.py',
    'cluster/control_plane/local_karen/check_types.py',
    'cluster/control_plane/local_karen/contract_policy.py',
    'cluster/control_plane/local_karen/contract_record.py',
    'cluster/control_plane/local_karen/github_client.py',
    'cluster/control_plane/local_karen/tests/contract_policy/__init__.py',
    'cluster/control_plane/local_karen/tests/contract_policy/conftest.py',
    'cluster/control_plane/local_karen/tests/contract_policy/observations.py',
    'cluster/control_plane/local_karen/tests/contract_policy/test_contract_ancestry.py',
    'cluster/control_plane/local_karen/tests/contract_policy/test_contract_client.py',
    'cluster/control_plane/local_karen/tests/contract_policy/test_contract_fetch.py',
    'cluster/control_plane/local_karen/tests/contract_policy/test_contract_merge.py',
    'cluster/control_plane/local_karen/tests/contract_policy/test_contract_objects.py',
    'cluster/control_plane/local_karen/tests/contract_policy/test_contract_primitives.py',
    'cluster/control_plane/local_karen/tests/contract_policy/test_contract_refs.py',
    'cluster/control_plane/local_karen/tests/contract_policy/test_contract_runtime.py',
    'cluster/control_plane/local_karen/tests/test_contract_record.py',
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
