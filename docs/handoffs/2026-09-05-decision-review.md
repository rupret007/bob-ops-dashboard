# Dashboard handoff: full-context decision review

Snapshot: 2026-09-05, America/Chicago. Source-only product round.

## Product delta

The prior phone layout hid pending detail and shortened it to 72 characters,
while Approve/Hold/Deny links remained usable on a last-verified snapshot.
A second startup pending fetch could race the main board poll. A malformed
timestamp-only JSON response could also clear failed-poll state without
replacing the old decision rows.

The existing Decisions panel now shows the full already-public detail, risk,
and boundary on phones. Review choices expands three explicit GitHub draft
links. The receipt preserves the exact ID, title, detail, risk, kind, and
accepted snapshot time. No new page, login, OTP, provider, dependency, issue
submission, or operation executor was added.

The first paint, soft paint, current poll, and decision controller use one
accepted snapshot path. Complete structure and bounded canonical decisions
are required before a poll can restore trust. IDs must be unique even across
case variants. Invalid/future timestamps, stale responses, poll failure, and
refresh silence fail closed. Changed or removed context invalidates review;
an identical-content timestamp refresh can preserve it. An overlong receipt
does not produce a shortened URL. No JavaScript leaves the board read-only.

## Verification and reproduction

Local result for this round: the full offline claim smoke passed, including
75 Python tests, 17 generated-JavaScript decision cases, and all existing
navigation, stale-agent, privacy, escaping, and hierarchy checks. The
collector recorded 103 synthetic CLI reads. A separate actual Chromium
fixture pass checked 320/390/1280-pixel layouts and failure/recovery behavior:
45 assertions, no horizontal overflow or page errors, one action column on
phones and three on desktop. All non-fixture HTTP requests were blocked;
no GitHub draft was submitted. Hosted exact-tip evidence belongs in the PR;
these results do not claim this source is published.

Run `python3 qa-offline.py` from the source checkout. This is also the
pull-request QA entry point. It runs the complete existing claim smoke and
the new Python receipt, actual-generated-JavaScript state, and offline
harness safety tests. The harness uses a rejecting fake CLI, synthetic
previous state, and synthetic agent probes; inherited credentials and
decision mutation are excluded. Unsupported commands never delegate to
real GitHub. The outage test now uses the same isolated synthetic generator.

Optional visual fixtures: add `--output-dir /tmp/bob-dashboard-review-fixture`
for an absent/empty temporary directory outside a repository. Only marked
HTML, JSON, and an offline notice survive. Never publish those fixtures.
The runner has no push option. Its pass proves fixture behavior, not live
portfolio state, service health, publication, or owner acceptance.

The existing live-read `qa-source-only.sh` remains available only when live
portfolio reads are actually in scope. The production refresh workflow and
its credential/mutation boundaries are unchanged.

## Authority and remaining limits

- Opening a GitHub draft never submits it. The owner still submits as
  `rupret007`; high-risk work still needs the exact draft shown in chat.
- This is a client-side review guard, not an execution authorization service.
  Existing issue ingestion still reads owner-issued `BOB-*` titles. It does
  **not** validate the new receipt body. The acting owner/agent must re-check
  current repository state and exact approval before doing anything.
- A URL copied earlier or a composer already open on GitHub cannot be
  revoked by the dashboard. Later changes still require an owner recheck.
- The page uses its local clock for the freshness window. Review state lasts
  only for that page instance; reload is a new review.
- Scheduled publication, merge, and owner acceptance remain separate gates.
  No generated checkout files, Pages settings, tags, or releases belong in
  this source-only draft. Parked drafts remain untouched.

## Next safe step

Karen reviews the exact draft tip, happy path, failure guards, privacy
assertions, and the unchanged downstream authority boundary. Only after
explicit exact-head landing approval should the normal scheduled refresh
materialize the source and Pages publish it. Do not manually dispatch or
claim this draft is live.

## Privacy check

Only fields already present in the public pending snapshot enter the review
receipt. Unrelated fields are ignored. No private repository metadata,
customer data, creative details, credentials, owner probes, or local paths
are added to this handoff or the public product.
