# Dashboard handoff: phone next-action priority

Snapshot: 2026-09-05, America/Chicago. Source-only product round.

## Product delta

Standing owner-only holds previously took the single next-action spot before
any red/yellow project was considered. A persistent hold could hide actionable
CI or review work indefinitely. The priority is now:

1. Ordinary pending decisions, preserving existing risk order.
2. Real red/yellow project evidence, preserving existing project order.
3. A valid standing owner hold, preserving existing risk order.
4. Quiet when none exists.

Only explicit `jeff-gate` / `owner-live-gate` kinds identify standing holds.
Unknown kinds keep ordinary pending priority; titles or IDs never infer a hold.
The existing Parked panel now offers **Review owner holds** when a valid hold
and the Decisions destination exist. It opens and focuses the exact existing
row, including a collapsed lower-risk row. It does not review or approve it.

This extends the already-landed phone tabs and decision review, not a second
dashboard, collector, menu system, or decision executor. First-screen tabs
remain live types plus Parked; no Decisions tab or yes-count is added.

## Verification and reproduction

Run `python3 qa-offline.py`. It builds marked synthetic artifacts and runs the
full claim smoke without live repositories or owner probes. Scheduler-owned
`index.html` and `status.json` must remain byte-stable. Exact-tip local and
executed hosted evidence belongs in the draft PR; source QA is not publication.

Focused coverage includes mixed owner/ordinary pending/project priority,
canonical valid owner context, safe exact-row targeting, missing destinations,
and Python first-paint / JavaScript soft-paint parity. Check the actual phone
path from Parked to an owner row before claiming browser verification.

## Boundaries and remaining work

- Draft for Karen; no merge, tag, release, deploy, or Pages dispatch.
- Parked drafts #12 and #21 are untouched.
- No OTP, new approval authority, issue submission, or live collection.
- Existing current-snapshot review, stale/failure guards, and owner-issued
  decision authority remain unchanged. A navigation link grants no permission.
- An eventual approved landing will be materialized by the normal scheduled
  refresh. This draft does not change the published snapshot.
- Karen reviews this exact draft; an owner must separately approve any landing.

## Privacy check

No private repository metadata, customer data, creative details, credentials,
owner probes, or local paths are added to the public product or handoff.
