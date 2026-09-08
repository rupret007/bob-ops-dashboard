# Dashboard handoff: phone glance leads with the action verb

Snapshot: 2026-09-08, America/Chicago. Source-only product slice.
Marker: OVERNIGHT_CLAUDE_DASHBOARD_20260908_0019.

## Product delta

The first phone screen still has one next action, the same ranking, and the
same fixed tab order. Only the wording of the inbox next-action changes.

Before, when the next action was a pending decision, the first-screen hero
was a bare noun -- for example `AdoptIQ live Cisco readiness`. A red or
yellow project already reads as an instruction (`... is red`,
`... needs a look`); the decision case did not. It named a thing, not a
move.

Now the inbox glance leads with the verb: `Decide: AdoptIQ live Cisco`.
The title is word-boundary trimmed so the hero stays within the existing
~two-line budget, and the button still opens the Decisions panel and
focuses the exact row, which carries the full untrimmed title. Red and
yellow glance copy is unchanged. The quiet (nothing pending, nothing
red/yellow) state is unchanged.

The verb invents no new state, adds no DOM node, no CSS, no second line,
no count, no approval action, and no authority. Standing owner-hold inbox
items keep the same `Decide:` framing -- the board's move for every inbox
row is still the same GitHub issue, opened as `rupret007`.

## Files

- `board_meta.py` -- `glance_decide_text()` helper + `GLANCE_DECIDE_PREFIX`;
  `glance_status` pending branch uses it.
- `refresh.sh` -- JS mirror in `glanceStatus` (same prefix + word trim).
- `test_board_meta.py` -- `test_glance_status_is_one_short_line`,
  `test_glance_status_names_the_three_standing_gates` updated.
- `test_soft_paint.js` -- pending-glance and three-gate assertions updated.

## Verification

`python3 qa-offline.py` -- PASS (synthetic GitHub reads, full claim smoke,
board_meta unit tests, soft-paint / open-links / open-decision JS).
`./qa-source-only.sh` -- PASS; scheduler-owned `index.html` / `status.json`
left byte-stable in the checkout.

Browser reproduction and hosted exact-tip evidence belong in the PR.
Browser emulation does not claim physical iPhone or Safari verification,
nor live publication.

## Review boundary

OPEN DRAFT PRE_KAREN for leftover and security review. Parked #12 and #21
remain held and untouched. No OTP/Unlock, decision-authority change,
generated snapshot edits, workflow/scheduler changes, merge, tag, release,
Pages action, send, spend, or live-provider access. Scheduled publication
remains separate from this source-only review.

## Privacy check

No private repository metadata, customer data, credentials, creative
details, owner probes, or local machine paths are added to the public
product or this handoff.
