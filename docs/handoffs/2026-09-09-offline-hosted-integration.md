# Dashboard handoff: offline hosted integration

Marker: BOB_MULTI_APP_FINISH_20260908. Follow-up on draft54.

## Gap and change

PR validation previously generated live portfolio data using a GitHub token.
That prevented hosted validation under an offline-only scope, even though
the source already had a safe synthetic generator and full claim smoke.

The QA workflow now runs the existing offline generator, its environment
allowlist/fake GitHub command checks, and the full claim smoke with tools
already on the runner. It installs no npm packages or browsers. No GitHub
token is passed to collection; checkout credentials are not persisted.
Optional local `--browser` adds a pinned test-only Chromium runner when
those tools are already available or their installation is authorized.

The browser serves only the marked fixture HTML/JSON on loopback. It checks
320/390/1280px glance wording, full visible title text and exact destination
focus. Clipped-text negative controls prove hidden text cannot pass merely
because innerText contains it. Off-origin requests, writes and popups fail.
This is fixture verification, not a general network sandbox.

Source-only PR enforcement remains. The runner checks scheduler-owned
index.html/status.json hashes, and hosted QA requires a clean checkout.
The separate refresh scheduler and product source are unchanged.

Hosted execution also reproduced a false source-only failure: an old event
base SHA caused newer scheduled snapshots in the merge checkout to count
as feature edits. The gate now verifies the expected PR head against the
two merge parents, rejects feature snapshot edits relative to their merge
base, and separately requires integrated snapshots to match the base.
Synthetic git-history tests cover the reproduced failure and actual feature
or integration snapshot edits, wrong heads and non-merge checkouts.

## Verification and limits

Run `python3 qa-offline.py --browser` after authorized test dependency setup:
`npm ci --ignore-scripts`, then
`npx --no-install playwright install chromium`.
Existing `python3 qa-offline.py` needs no new browser dependencies.
Exact local/hosted results and final-tip review belong in the PR receipt.
Hosted checks run the offline claim suite; the browser journey is separate
local evidence. Do not describe that local browser run as hosted coverage.

The hosted summary records the actual checkout commit, tree and parents,
including the synthetic integration commit for PR events. It certifies that
captured tree only; later source/main changes require fresh evidence.
No live repository state, physical iPhone/Safari or publication is verified.

OPEN DRAFT/PRE_KAREN. Parked12/21 held. No OTP/Unlock, decision authority,
scheduler, generated snapshot, merge, deployment, Pages, signing or send
change. Test-tool installation is distinct from app installation and
requires authorization when the active goal prohibits installation.
