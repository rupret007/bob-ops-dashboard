# Dashboard handoff: retry keyboard focus

September 7, 2026, America/Chicago. Source-only product slice.

Retry now keeps keyboard focus while the existing snapshot check runs. The
button announces its busy/disabled state and ignores repeated activation.
When recovery hides the warning, focus returns to the home glance or current
view if Retry still owned focus. A failed retry stays usable in place; moving
focus elsewhere while waiting prevents recovery from taking it back.

Validation: `python3 qa-offline.py` exercises the generated retry handler,
repeat/hidden-page guards, focus destinations and no-steal behavior alongside
the full existing smoke. Isolated Chromium checks use marked synthetic HTML
and mocked JSON at phone and desktop widths. Exact results are in the PR.

This changes no decision review, authority, polling cadence, API or data
contract. Generated snapshots remain byte-stable. No live repository/probe
reads, scheduler or Pages actions, OTP/Unlock, send, spend or deployment.
Physical devices and Safari remain unverified. Parked drafts stay held.
OPEN DRAFT PRE_KAREN for leftover + security; stop before merge.
