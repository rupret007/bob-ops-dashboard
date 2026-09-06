# Dashboard handoff: keep keyboard place

Snapshot: 2026-09-05, America/Chicago. Source-only product round.

## Product delta

Selecting an existing project-type tab replaced the focused button and sent
keyboard focus back to the page body. A changed status snapshot also replaced
focused project actions. The tablist exposed tab roles without arrow navigation.

The tabs now have one Tab stop. Left/Right wrap, Home/End move to the first/last
tab, and Enter/Space retain the existing open/close behavior. Moving focus alone
does not select a panel. Selecting a tab or repainting it keeps keyboard focus.
First-paint and soft-paint tabs use the same initial tab stop.

When the visible project list refreshes, a focused action returns only if the
same unique project and exact action URL and label remain. Changed, removed,
or ambiguous actions fall back to the project row. A missing or ambiguous
project falls back to its visible panel; a missing panel falls back to the
current or first available tab. A later refresh also preserves panel focus.
Opening a leftover type from Parked focuses its newly visible type tab.
Refreshing while focus is outside this navigation leaves it alone.
Focus recovery does not open a link or review/submit a decision.

## Verification

Run `python3 qa-offline.py`. The full claim smoke builds disposable synthetic
artifacts and covers the keyboard and focus behavior along with existing
decision-review, privacy, outage, and receipt guards. The output-directory test
uses an absolute outside-temp fixture so the suite also works when the source
checkout itself is temporary; production directory validation is unchanged.

Browser evidence and hosted exact-tip results belong in the draft PR. Passing
offline QA verifies source behavior, not published status or live repositories.

## Review boundary

Draft for Karen, with leftover and security review pending. Scheduler-owned
`index.html` and `status.json` are unchanged. Parked drafts #12/#21 stay held.
No OTP/Unlock, new authority, provider calls, or automatic approval is added.
No merge, tag, release, deployment, Pages action, or scheduler change is part
of this handoff. Publication still requires the existing owner process.

No private coordination metadata, owner probes, local paths, or credentials
are added to the public product or this handoff.
