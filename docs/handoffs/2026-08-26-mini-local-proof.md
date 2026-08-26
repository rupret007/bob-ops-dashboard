# Portfolio handoff: Mini local proof and Bob coordination

Snapshot: 2026-08-26 15:23 CDT
Status: immutable public handoff; revalidate every head before acting

## Read this first

Bob/Grok continued landing front-door and product-honesty work while Codex used the Mac mini for deeper local proof. This record is continuity, not approval. It authorizes no merge, deployment, scheduled-workflow dispatch, message, upload, live-provider action, signing step, purchase, credential/settings change, or physical action.

The four evidence classes below are deliberate: **hosted green**, **locally green**, **external block**, and **owner-only**. A zero-step job whose runner and steps are empty is an external block with an unconfirmed cause. It is not a billing diagnosis, a code failure, or a passing suite.

## Completed since Batch A

| Public lane | Concrete result at this snapshot | Classification |
| --- | --- | --- |
| WebJam | #35 and #36 are on `master`; current default tip is `72fd88cc6774c0f6ea99886591df3a9f5c81d00b`. A separate canvas-delivery follow-on is draft #37 at exact head `5689da764127100593b9486d1be3eba9e54b9838`, based on that tip. | #36 hosted green; #37 locally green while hosted jobs run |
| StoryBoard | #13 is on `main` at `7148f9119b2a9c3634a2d312261d9fd1a9d1bf72`. Draft #14 at exact head `c228be2766755e14187be49d070900e1b1afcd4b` prevents reconciled-away catalog rows from still appearing in the create preview. Both hosted Quality jobs pass. | hosted green |
| Andrea NanoBot | #20 is on `main` at `27414fd455da820ab6561f9eaf962b567af6b517`. A stored test fixture cannot become the real send yes-fence. Nothing was sent. | hosted green |
| StoryLiner | #16 is on `main` at `81469c374471245a617d709a6bc1c376831426d5`. Empty Approved-state copy now stays honest. | hosted green |
| Bob Ops Dashboard | #19 and privacy-honesty #20 are on `main`; private/high-level lanes are no longer described as live repository taps. The scheduler-owned generated tip observed for this snapshot was `c1234ddcf997a55c82e96f4c0f47bf94a7e50d2d`. No manual refresh was dispatched. | hosted green |

The private-content vault also landed Bob's latest privacy-boundary follow-on. Its current exact-tip local validator, export check, and 116-test suite pass. Exact private repository metadata and implementation details remain outside this public handoff.

## Mac mini proof table

| Lane | Exact local proof | Classification |
| --- | --- | --- |
| StoryBoard draft #14 | Shared tests 27/27; full unit tests 300/300; manager evaluations 98/98 with safety 100%; database integrations 5/5; browser workflows 15/15; typecheck, lint, production builds, and local container readiness/login smoke passed. Hosted `verify` and `container-smoke` also pass at the exact head. | hosted green |
| WebJam draft #37 | Focused canvas/controller tests 72/72. Exact-base fresh-process partitions passed 6,570 tests plus 95 subtests with 25 intentional skips. Ruff, compile, dependency integrity, UX smoke, vulnerability audit, and diff checks pass. | locally green |
| Andrea NanoBot current `main` | Send-fence proof 43/43; full hermetic suite 3,874/3,874 across 296 result files; documentation check passed 82 files. No outbound action ran. | hosted green |
| StoryLiner current `main` | 23/23 suites and 271/271 tests; production build passed with existing unused-variable warnings. | hosted green |
| Private-content vault | High-level exact-tip local validation, 116 tests, and export consistency check passed; private content was not printed or copied here. | locally green |
| Bob Ops handoff source | `qa-source-only.sh` passed its 39 unit tests plus JavaScript, URL, open-link, privacy, authenticated-refresh, private-metadata, and end-to-end smokes. Checked-in `index.html` and `status.json` stayed unchanged; the hosted smoke also passed. | hosted green |

WebJam's long one-process macOS Qt run completed once on the pre-#36 base. On the exact #36 base, two later one-process attempts hit native segmentation faults at different pre-existing UI tests after thousands of passes; each named case passed alone, and every exact-base test passed in the fresh-process partitions above. That is a local runner/process-lifetime issue, not evidence that draft #37's canvas logic failed. Hosted jobs remain the platform authority.

## Still open

| Lane | Classification | Next safe action | Owner gate |
| --- | --- | --- | --- |
| WebJam #37 | locally green | Let hosted jobs finish; review the exact draft head | Merge needs exact-head approval; physical two-Mac, Drawpile, audio, signing, and release work are owner-only |
| StoryBoard #14 | hosted green | Review the exact draft head | Merge needs exact-head approval; green CI authorizes no production action |
| Bob Ops handoff #21 | hosted green | Review this source-only documentation draft at its exact current PR head | Merge needs exact-head approval and naturally exposes the link through the scheduled refresh/Pages flow; never dispatch manually |
| Bob Ops #12 | owner-only | Preserve the existing conflicted draft until Jeff chooses its disposition | Do not rebase, close, or merge without explicit direction |
| Turdanoid #8 | owner-only | Perform the phone/desktop fun-and-feel check | Merge needs exact-head approval; Pages impact follows a merge |
| RadDadSite #10 | owner-only | Review the song/QR/NFC experience at its current head | Merge and any production rollout need exact approval |
| Rad Dad Merch #2 | owner-only | Slice, print, fit, and clearance-check the configured three-device project | Physical work and merge remain Jeff-only |
| Ball Beacon | owner-only | Preserve its private draft stack and local proof | Device, account, and signing work remain owner-only |
| Private media | owner-only | Keep the handoff generic | Login, upload, publishing, and release remain owner-only |
| AdoptIQ | external block | Continue offline-only review and simulation; do not rerun zero-step hosted work without new evidence | Keep private/draft with `ready_for_live_cisco=false`; merge and live Cisco use need explicit approval |
| Bob application, CSS Conductor, StoryOps-AI, and TACTrack | external block | Preserve locally verified work and diagnose only from direct platform evidence | No merge, credential, live-provider, or production action without explicit approval |

## Evidence-backed leftovers not opened in this round

- StoryLiner's Published page should eventually distinguish confirmed local publish receipts from durable uncertainty that an external write may have started. That is a separate, non-overlapping follow-on; no post was sent or checked live.
- One additional private-content boundary candidate remains documented only in the authenticated operator handoff. Public details are intentionally omitted.
- Existing user checkouts with local branches, documentation edits, or backups were left untouched; isolated worktrees were used for the two code drafts.

## Next actions

1. Revalidate WebJam #37 and StoryBoard #14 at their exact heads and review hosted results; do not merge from this handoff.
2. Review the new source-only dashboard handoff draft. If Jeff later approves its exact head, allow only the natural scheduled refresh and Pages flow; do not dispatch it manually.
3. Choose at most one deferred safety/honesty candidate after the current drafts settle and after checking that Bob has not started the same work.
4. Ask Jeff only for a real merge, privacy, physical, paid, credentialed, live-provider, sending, signing, or production gate.

## Privacy check

- No private repository URL, private branch/pull-request/commit metadata, local path, private catalog row, creative detail, credential, customer/contact data, invitation, media asset, or raw exception appears here.
- Private lanes are high-level only; private-content and AdoptIQ boundaries remain intact.
- This source-only handoff does not change `index.html` or `status.json`.
