# Dashboard handoff: phone quiet-vs-live type tabs

Snapshot: 2026-09-07, America/Chicago. Source-only product slice.
Marker: OVERNIGHT_CLAUDE_DASHBOARD_20260907_1823.

## Product delta

The first phone screen still has one next action and the same fixed tab
order (Live, Apps, Cisco, Bots, Media, Parked -- a tab per existing type).
The tab bar no longer paints every type at equal weight. A type whose every
row is an owner gate, a parked leftover, or unknown -- no green / yellow /
red lane -- now renders dimmed with a dashed outline (`data-quiet="true"`,
`class="is-quiet"`). Types that hold real shipping or attention work stay
full-weight. With today's data only Media and Parked dim; Cisco stays live
while AdoptIQ / TACTrack are yellow.

The quiet mark is derived only from row statuses already in the data. It
invents no new state, reorders nothing, and hides no tab. A quiet tab is
still a real tab: it opens its existing panel and, when selected, drops the
dim so the active type always reads clearly. Parked leftover drafts #12 and
#21 are unchanged and still never become a lane tap, a yellow "needs a
look", or an active Jeff yes -- the dim only reinforces that separation.

## Files

- `board_meta.py` -- `LIVE_LANE_STATUSES`, `type_tab_is_quiet(section)`,
  `type_tabs_html` emits the quiet class/attr.
- `refresh.sh` -- JS mirror `typeTabIsQuiet` + `typeTabsHtml`; one CSS rule
  `.type-tabs button.is-quiet:not([aria-selected="true"])`.
- `test_board_meta.py` -- `test_type_tabs_dim_quiet_types_by_live_row_state`.
- `test_soft_paint.js` -- quiet-tab first-paint / mirror assertions.

## Verification

`python3 qa-offline.py` -- PASS (103 synthetic GitHub reads, full claim
smoke, board_meta unit tests, soft-paint / open-links / open-decision JS).
`./qa-source-only.sh` -- PASS; scheduler-owned `index.html` / `status.json`
left byte-stable in the checkout.

Browser reproduction and hosted exact-tip evidence belong in the PR. Browser
emulation does not claim physical iPhone or Safari verification, nor live
publication.

## Review boundary

OPEN DRAFT PRE_KAREN for leftover and security review. Parked #12 and #21
remain held. No OTP/Unlock, decision-authority change, generated snapshot
edits, workflow/scheduler changes, merge, tag, release, Pages action, send,
spend, or live-provider access. Scheduled publication remains separate from
this source-only review.

## Privacy check

No private repository metadata, customer data, credentials, creative
details, owner probes, or local machine paths are added to the public
product or this handoff.
