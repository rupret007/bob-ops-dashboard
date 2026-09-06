# Dashboard handoff: readable saved snapshot

Snapshot: 2026-09-06, America/Chicago. Source-only product round.

## Product delta

The page promised a read-only view without JavaScript, but every project and
decision panel, the snapshot timestamp, and even its no-script notice were
hidden. Inert tabs and a misleading starting-live label remained visible.

Generated HTML now presents named, readable saved sections and their timestamp,
with a clear read-only explanation and ordinary in-page section links. Compact
home styling is enabled only after navigation finishes initializing. Until then,
inert navigation and action controls are hidden; a partial startup that already
set panel hidden attributes cannot erase the saved content. Reload retries startup.

Decision readiness is separate from snapshot freshness. Accepting initial data,
receiving a valid poll, or enabling navigation alone cannot review a decision or
open a composer. Successful navigation must still pass the existing current-data
checks, followed by explicit review. Invalid, stale, failed, changed, and removed
decision safeguards remain; repeated readiness cannot clear a failed review.

## Verification and limitations

Run `python3 qa-offline.py` for synthetic generation and the full claim smoke.
The regressions cover saved HTML, progressive region-to-tab enhancement,
pre-navigation decision blocking, freshness, review identity, and popup fallback.
Browser proof should exercise JavaScript disabled, interrupted startup, and
normal compact/keyboard/review behavior on phones and desktop. Exact local and
hosted results belong in the draft PR; offline evidence is not live publication.

This fallback does not refresh data without JavaScript, recover every later
runtime error, or make a stored snapshot current. Section/project links are
read-only navigation; they do not confer approval. Existing GitHub issue
ingestion and the requirement to recheck current owner authority are unchanged.

## Review boundary

Draft for Karen leftover and security review. No generated `index.html`,
`status.json`, agent probes, workflow, or scheduler changes. Parked drafts remain
held. No OTP, new authority, merge, release, deployment, Pages action, provider
access, messages, or spending. Source landing and publication require the
existing owner process. No private coordination metadata or local paths are
included in this public handoff.
