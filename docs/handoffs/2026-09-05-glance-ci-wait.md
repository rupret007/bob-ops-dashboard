# Dashboard handoff: glance CI wait vs review yellow

Snapshot: 2026-09-05, America/Chicago. Source-only product round.

## Product delta

The one phone next-action could name a yellow lane whose only public evidence
was in-flight tip CI. That wait state hid later review yellow, such as a
real open-PR count, while still beating standing owner holds.

The glance order is now:

1. Ordinary pending decisions, preserving existing risk order.
2. Real red project evidence, preserving existing project order.
3. Jeff-actionable yellow: open PRs or an incomplete public listing.
4. CI running/pending yellow, still ahead of a standing owner hold.
5. A valid standing owner hold, preserving existing risk order.
6. Quiet when none exists.

A waiting tip with open PRs or `pr_listing_complete=false` stays actionable.
Private rows never invent a public CI wait. Boolean or missing PR counts are
not review work. First-paint and soft-paint stay equivalent. No new tab,
count, approval action, or authority.

This extends the already-landed phone tabs and owner-hold priority, not a
second dashboard. Parked leftover drafts #12 and #21 were not merged or
rewritten. Possession of the public URL remains the trust boundary; there
is no Unlock / OTP.

## Verification and reproduction

Run `python3 qa-offline.py` from the source checkout. That is the
pull-request QA entry point. It builds marked synthetic artifacts, runs the
full claim smoke, and must leave scheduler-owned `index.html` / `status.json`
byte-stable. Hosted exact-tip evidence belongs in the PR; these results do
not claim this source is published.

## Authority and remaining limits

- Draft only for Karen. No merge, tag, release, deploy, Pages dispatch, or
  scheduler mutation without Jeff.
- Parked leftover drafts #12 and #21 remain open drafts.
- Scheduled publication materializes generated files after source lands.
- Opening a glance row still does not review, approve, or execute it.

## Next safe step

Karen reviews the exact draft tip, review-vs-wait ranking, unchanged owner-hold
fallback, and the OTP / parked-draft / source-only boundary. Only after
explicit exact-head landing approval should the scheduled refresh materialize
the source.

## Privacy check

No private repository metadata, customer data, creative details, credentials,
owner probes, or local paths are added to this handoff or the public product.
