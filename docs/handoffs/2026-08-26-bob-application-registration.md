# Portfolio handoff: Bob application registration

Snapshot: August 26, 2026, America/Chicago
Status: source-only draft; not merged or published

## Completed

- Registered Bob the Bot as its own repository-backed lane in the dashboard source.
- Kept it distinct from Andrea NanoBot, OpenClaw Runtime, and Bob Ops Dashboard.
- Applied a fail-closed high-level boundary even when an authenticated refresh can read the private repository.
- Limited the public row to the product name, coarse bootstrap state, safe operating note, accessibility flag, and CI conclusion when available.
- Added regression coverage for lane presence, separation, sanitized metadata, and owner-only operating boundaries.
- Left scheduler-owned `index.html` and `status.json` out of the feature draft. RadDadSite and Rad Dad Merch review state continues to come from the live scheduler fetch rather than a hardcoded pull-request snapshot.

## Still open

| Lane | Classification | Next safe action | Owner gate |
| --- | --- | --- | --- |
| Bob the Bot | Private bootstrap | Continue focused implementation and offline verification in the private application repository | Any merge needs exact-head approval; live sends, gateway restarts, credentials, settings, and production actions require explicit approval |
| Bob Ops Dashboard | Source-only draft | Review the current source head and its checks | Merge approval applies only to the exact reviewed head; the natural scheduled refresh may update the public Pages dashboard afterward |
| Turdanoid dashboard follow-on | Separate existing draft | Preserve its current head and review it independently | Do not merge, rebase, close, or mark ready without exact current approval |
| RadDadSite and Rad Dad Merch | Live scheduler discovery | Let the normal refresh report current open work | No deployment, physical target change, printing, or distribution from this handoff |

## Verification distinctions

- Locally green: the source-only dashboard QA rebuilds disposable artifacts and runs privacy, URL, JavaScript, unit, and end-to-end smokes without changing the checked-out generated files.
- Hosted state: a draft check may validate this source head; the public dashboard does not change until an approved merge is followed by the natural scheduled refresh and Pages flow.
- Owner-only: messages, restarts, credentials, settings, deployments, physical tags, physical NFC/QR target changes, printing, and distribution remain outside this source registration.

## Next actions

1. Review the draft diff and current checks.
2. Request exact-head approval before any merge.
3. After an approved merge, let the existing schedule materialize `index.html` and `status.json`; do not manually dispatch it.
4. Continue Bob application work only in its private repository with the same approval fences.

## Privacy check

- No private repository URL, branch, pull-request metadata, commit identifier, local path, implementation content, credential, contact, customer data, or private media detail appears in this handoff.
