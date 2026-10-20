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

The head/base merge base must remain the frozen baseline. The baseline must
also be an ancestor of the live base. The live base must equal the installed
and loaded source revision, verified by the existing runtime source check.
Its complete tree difference from the baseline must contain exactly the five
policy files listed in the module, each with regular-file mode and content
identical to the policy snapshot loaded by that process. Consequently a
separate reviewed policy merge can advance the target without admitting
unrelated base changes or accepting a stale running policy.

Merge and install this policy through the existing review/admission process
before reopening #104. Do not merge another change into its target first;
that changes this proof's permitted baseline transition and requires a new
reviewed decision. No unreviewed runtime overlay qualifies.

The typed proof discharges only one removed assertion. The checker continues
to inspect every other assertion, removed test/file/decorator and added skip.
All other deterministic checks, independent reviewers, CI, signatures, delivery,
current-head requirements and human-only repository merges remain in force.
Missing objects, packet drift, source mismatch and unproved base advancement
stop fetching before paid review. Malformed typed proofs never discharge a
finding. This policy does not itself
approve #104 or establish healthy deployment or recovery.
