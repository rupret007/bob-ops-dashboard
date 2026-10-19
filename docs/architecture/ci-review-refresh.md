# Refreshing obsolete CI repair evidence

A Karen FAIL can retain an old CI result after checks finish on the same
commit. Sending that observation to a coding worker can waste all three
exact-head repair attempts even though tests already pass and no code change
is justified.

The dispatcher recognizes a completed, locally reviewed exact-head artifact
whose only actionable failure is `ci_green`. It observes current PR state,
full head SHA, and the CI rollup in one response. These additional reads have
a ten-second limit each and share a thirty-second dispatch-cycle budget.

- Green CI defers repair with `review-needed`.
- Pending, missing, unavailable, or changed-head evidence defers repair.
- Terminal failing CI retains the existing claimed repair path.
- Other deterministic failures or model FAIL findings retain normal repair.

Conservative BORDERLINE observations remain in the original artifact. This
path does not convert them into fixes or passing reviews. The normal Karen
gate must perform a fresh full review before any passing authority exists.

Existing conductor and poller backlog admission can queue the refreshed
review with `needs-karen`. A shared NATS claim, fresh exact-head green CI,
and a second artifact check precede that write. Existing `needs-karen` stops
duplicate queueing. A replacement review without the old CI failure ends
refresh eligibility, even if the replacement still fails for review reasons.

No repair retry counter, job outcome, review artifact, merge guard, or label
other than `needs-karen` is changed by this refresh path. Coordination or
GitHub failures defer work without granting review authority.
