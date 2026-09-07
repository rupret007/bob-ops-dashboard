# Dashboard handoff: phone browser history

Snapshot: 2026-09-07, America/Chicago. Source-only product slice.

## Product delta

Opening a dashboard type replaced the current browser-history entry. From the
home glance, opening Live and pressing Back could leave the dashboard entirely.

Deliberate type, glance, leftover-type, and owner-hold navigation now adds a
visit when the section URL changes. Back and Forward traverse those views;
closing the selected type creates a returnable home visit. Repeated taps on
the same glance destination still reveal its row without duplicate visits.
Initial rendering, reload, accepted polls, and history traversal add no visits.

History contains only existing allowlisted section hashes and the current path
and query. It stores no project snapshot, decision identity, or review state.
Traversal paints the current accepted board rather than restoring old data.
Focus returns to a visible tab, the home glance, or the Decisions panel. It
does not target a decision action or take focus from an outside control.
Native browser scroll restoration remains in charge. If history writes are
unavailable, tab navigation still works without Back/Forward recording.

## Verification

Run `python3 qa-offline.py` for synthetic generation and the full claim smoke.
The navigation regressions cover deliberate visits, Back/Forward, closing a
type, repeated destinations, deep links, refresh stability, rejected history
writes, and visible focus after the browser resets it to the page body.

For browser reproduction, retain marked artifacts with
`python3 qa-offline.py --output-dir /tmp/bob-dashboard-phone-history-check`.
Using an isolated browser with HTML and JSON requests fulfilled from those
fixtures, open the home page, visit Live then Apps, and use Back twice and
Forward twice. Repeat after a synthetic content poll. Check the glance,
Parked owner holds, leftover types, deep-link reload, keyboard activation,
and the JavaScript-off saved view at phone and desktop widths. Exact results
and hosted QA evidence belong in the draft PR. Browser emulation does not
claim physical iPhone or Safari verification, nor live publication.

## Review boundary

OPEN DRAFT PRE_KAREN for leftover and security review. Parked #12 and #21
remain held. No OTP/Unlock, decision-authority change, generated snapshot
edits, workflow/scheduler changes, merge, tag, release, Pages action, send,
spend, or live-provider access. Scheduled publication remains separate from
this source-only review.

## Privacy check

No private repository metadata, customer data, credentials, creative details,
owner probes, or local machine paths are added to the public product or handoff.
