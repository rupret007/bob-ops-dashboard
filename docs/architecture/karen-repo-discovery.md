# Bounded scheduled Karen discovery

Native poll cycles scan at most four repositories, rotating through the sorted
current active set. The private `review-repo-scan.json` cursor advances only
after a complete repository listing or an explicit blocked-repo skip. Malformed
or unavailable cursor state defers the cycle. Direct `poll_and_run` callers keep
their explicit full repository set; the existing three-review and deadline
limits are unchanged.

Scheduled scans use at most one optional25-candidate GraphQL metadata batch,
prioritizing candidates with the oldest stored scheduling age. Unsampled or
unavailable metadata remains unknown. Normal exact-head diff, CI, claim and
independent reviewer gates still decide whether work can proceed. A scoped scan
preserves aging for unscanned/unavailable repos and reconciles old heads only in
completely observed repos. Scheduling state is atomically written, limited to
10000entries/1MiB, and cannot authorize a review or merge.

The account ledger still permits40reservations/minute, with24/minute for the
existing scheduled main-CI observations. Discovery remains foreground in this
narrow repair; limiting its burst leaves room for an exact-head gate in the
observed18-repo workload. Other foreground callers or paginated large repos can
still cause deferral; no guaranteed admission or complete portfolio observation
is claimed. Every remaining request obeys the same ledger and API reserves.

GitHub admission deferrals retain their typed code and bounded retry metadata
through backlog/client reads. The native cycle stops on admission deferral and
skips remaining housekeeping. Failed read/JSON/shape results are controlled
unavailability errors rather than empty repositories. A full backlog or Karen
failure report is published only after its complete collection succeeds; prior
reports remain unchanged on unknown/deferred data. A partial candidate scan is
never presented as a complete backlog report.
Completed verified review outcomes are saved before optional housekeeping and
returned to the CLI even if a later label, admission or housekeeping operation
defers; the cycle then skips all remaining housekeeping.

Offline regressions use the real40/min rolling-ledger mutation with24 existing
background reservations. They reproduce full18-repo starvation before any gate
and show bounded rotation reaches exact pinned-head metadata under the same
cap, without calling providers or creating approval artifacts. Cursor fairness,
one-batch unknown behavior, unscanned aging, source/error propagation and report
preservation are verified separately.
