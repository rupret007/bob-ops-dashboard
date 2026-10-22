# Background GitHub observations

The same durable account budget admits at most 40 reservations in a rolling
60-second window. Main-CI metadata reads additionally share a lower allowance of
24/min across both hosts. This leaves 16 reservations for foreground work: the
blocked mobile review needs five initial metadata reads and may then require
bounded source-context and current-head checks. Primary API reserves, backoff and
the total cap are unchanged. Other foreground work still competes within 40;
this reservation does not promise every foreground request will be admitted.

Only scheduled main-CI batches mark known read commands as background. Immediate
merge validation and recovery writes retain foreground admission. Exhaustion raises the typed
`background-exhausted` deferral with a retry delay bounded to 60 seconds. A
background batch stops without manufacturing a CI result. Evidence freshness,
unknown findings and exact-head merge gates retain their existing rules.

The version-one KV key and numeric `requests` ledger remain compatible with
existing writers. An optional numeric `foreground_requests` multiset identifies
explicitly classified foreground reservations; every unclassified old-writer
entry conservatively consumes background allowance. Expired auxiliary stamps
are pruned before subset validation because old writers only prune the original
ledger. Unmatched active stamps or malformed state fail closed. Neither rolling
upgrade nor classification resets the total account budget.

Both 61-PR observers cannot independently refresh all historical CI within the
current lifetime at this background allowance. Pending/stale coverage remains
visible and requires a separate ownership/observation design. Reserved foreground
room solves admission starvation without claiming that duplicate coverage is
complete.
