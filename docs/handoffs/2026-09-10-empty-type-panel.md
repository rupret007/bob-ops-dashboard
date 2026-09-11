# Dashboard handoff: honest empty type panel

September 10, 2026, America/Chicago. Source-only product slice.
Marker: OVERNIGHT_CLAUDE_DASHBOARD_20260910_2315.

A phone type tab (Live, Apps, Cisco, Bots, Media, Parked) paints whenever its
section id is present in the data, before any project is checked against it.
`lanes_html`/`lanesHtml` previously rendered a bare empty `<div class="lanes">`
for a type with zero current rows -- a silent blank panel under the heading,
not the honest no-invented-content state the rest of the board holds to.
Both the server-render path (`board_meta.py`, `refresh.sh`) and the
client soft-paint mirror now show one small note naming the real type
("Nothing under Bots right now.") instead of blank space, keeping
first-paint / soft-paint parity.

This is a defensive correctness fix: with the current fixed portfolio list
every existing type tab always has at least one project, so the blank panel
does not currently show live. It closes the gap for whenever that changes
(a repo removed from a type, a future type with no rows yet) so the board
never goes quiet by accident.

Validation: `python3 -m pytest test_board_meta.py` covers the new
`empty_type_panel_html` note (real types and an unknown id, never blank,
never invented). `python3 qa-offline.py` re-ran the full synthetic claim
smoke (103 reads) end to end through the changed render path; scheduler-owned
`index.html`/`status.json` stayed byte-stable. `node test_soft_paint.js`,
`test_open_decision.js`, `test_open_links.js` and the rest of the local
pytest suite (`test_pr_source_only.py`, `test_refresh_outage_guard.py`,
`test_offline_qa.py`) all pass locally. `test_decision_review.py` fails to
import on a clean `main` checkout before this change (missing
`decision_review_identity` in `board_meta.py`) -- pre-existing, unrelated,
left untouched; out of this leftover's narrow scope.

This changes no decision review, authority, polling cadence, API or data
contract, and no OTP/Unlock. Generated snapshots remain byte-stable. No live
repository/probe reads, scheduler or Pages actions, send, spend or
deployment. Physical devices and Safari remain unverified. Parked
drafts (#12/#21) stay held and untouched.
OPEN DRAFT PRE_KAREN for leftover + security; stop before merge.
