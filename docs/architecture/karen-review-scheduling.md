# Karen review scheduling

The poller collects the whole `needs-karen` queue before starting a review.
Scheduling metadata is fetched through bounded GraphQL batches using the
shared account budget. Optional metadata reads share a thirty-second limit;
each request has at most ten seconds. Missing or changed-head metadata remains
unknown and never counts as green CI.

Small changes below 300 added/deleted lines with green CI go first, across
repository boundaries. Other green changes follow, then unknown/pending CI,
then failing CI. The normal gate independently fetches current head/CI and
claims ownership; scheduling metadata cannot grant a verdict or merge.

Each cycle admits at most three review attempts and starts no further review
after twenty minutes. A review already admitted retains its existing
twenty-minute deadline. This admission window is not a twenty-minute limit
for the entire cycle.

A large change deferred in two previous cycles receives the first admission
in the next cycle. Old work with unavailable size metadata also gets a turn.
Deferral counts are private, atomic, exact-head scheduling state; they neither
reset repair retries nor change review authority. Attempted heads reset their
scheduling wait, and changed heads start fresh.
