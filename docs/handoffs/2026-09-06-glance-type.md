# Dashboard handoff: glance type and next-action place

Snapshot: 2026-09-06, America/Chicago. Source-only product round.

## Product delta

The first phone screen still has one next action and the same ranking. The
glance no longer uses leftover **needs a look** copy for both review yellow
and CI wait. The work name stays on one line. A second allowlisted place
line names the next action and, for a project, its existing type:

- ordinary pending: **Decide**
- standing owner hold: **Owner hold**
- red: **Red · type**
- review yellow: **Review · type**
- CI running/pending yellow: **CI wait · type**
- quiet when none exists

Unknown kinds and invented type ids produce no place text. Decision and
hold rows never invent a project type. First-paint and soft-paint stay
equivalent. No new tab, count, approval action, or authority.

This extends the already-landed phone tabs, owner-hold priority, and
review-vs-wait ranking. Parked leftover drafts #12 and #21 were not merged
or rewritten. Possession of the public URL remains the trust boundary;
there is no Unlock / OTP.

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

Karen reviews the exact draft tip, name-plus-place presentment, unchanged
ranking, and the OTP / parked-draft / source-only boundary. Only after
explicit exact-head landing approval should the scheduled refresh
materialize the source.

## Privacy check

No private repository metadata, customer data, creative details, credentials,
owner probes, or local paths are added to this handoff or the public product.
