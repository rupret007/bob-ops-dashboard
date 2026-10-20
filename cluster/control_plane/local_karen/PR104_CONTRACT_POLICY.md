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

The head/base merge base must remain the frozen baseline. Three explicit
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

Every sole-parent edge from the frozen baseline to this explicit admitted
commit is proved from immutable objects. Missing, reversed, skipped or
additional-parent transitions fail; no later unlisted release is permitted.

The final admitted commit must be an ancestor of the live base. The live base must equal the installed
and loaded source revision, verified by the existing runtime source check.
Its complete tree difference from that admitted commit must contain exactly the five
policy files listed in the module, each with regular-file mode and content
identical to the policy snapshot loaded by that process. Consequently a
separate reviewed policy merge can advance the target without admitting
unrelated base changes or accepting a stale running policy.

Merge and install this policy through the existing review/admission process
before reopening #104. Do not merge another change into its target first;
that changes this proof's permitted baseline transition and requires a new
reviewed decision. No unreviewed runtime overlay qualifies.

This operational note stays beside the gate code and travels with the normal
module copy. It does not add a document to the Pages publication directory.

The typed proof discharges only one removed assertion. The checker continues
to inspect every other assertion, removed test/file/decorator and added skip.
All other deterministic checks, independent reviewers, CI, signatures, delivery,
current-head requirements and human-only repository merges remain in force.
Missing objects, packet drift, source mismatch and unproved base advancement
stop fetching before paid review. Malformed typed proofs never discharge a
finding. This policy does not itself
approve #104 or establish healthy deployment or recovery.
