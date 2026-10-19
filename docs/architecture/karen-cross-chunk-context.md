# Cross-chunk review handoff

Complete-file chunks receive an automatic supplement when their entire missing
changed-file complement fits four paths and the existing 48000 raw/serialized
packet limits. This uses complete exact-head diffs already in memory, without
source/API/checkout reads. It is all-or-none: split-file chunks, unavailable
manifest diffs, or over-budget complements retain the explicit request flow.
Original chunk order, byte coverage, and independent votes remain unchanged.

Automatic and explicitly requested evidence share one cumulative four-entry,
48000-size budget. Changed diffs and immutable source are distinct evidence
entries; overlaps are deduplicated. The single follow-up replaces the initial
packet with the bounded union rather than appending another budget. Only an
explicit source request may invoke the existing immutable-source resolver and
its unchanged provenance/privacy guards. Supplying context never grants PASS.

Each chunk needs complete integration evidence. Independent approval of a later
chunk cannot supply evidence missing from an earlier chunk. The trusted prompt
requires a reviewer that needs another changed file not already supplied to
return BORDERLINE plus
its exact manifest path in `requested_files`. Chunk context includes a bounded
example using at most four actual paths from the other-chunk manifest. Prose
mentioning a chunk number alone supplies no structured request and stays unresolved.

The existing resolver can then supply the requested file's complete changed diff
from the already pinned in-memory PR. This requires no new GitHub read or local
checkout access. Existing exact-head/path validation, four joint requested paths,
48000-character/serialized-packet ceiling and one follow-up remain unchanged.
Immutable source requests retain their separate provenance and privacy guards.
The reviewer must issue a new verdict after reading supplied context; neither
resupply nor another reviewer's PASS authorizes approval.

Controlled context telemetry records changed/source request counts and one of
`not_requested`, `invalid_request`, `changed_unavailable`, `context_budget`,
`source_unavailable`, `context_supplied`, or `followup_complete`. It records no
model text, requested path, source content, credential or arbitrary error value.
These diagnostics distinguish a missing structured request from an unavailable
file or exceeded context bound without weakening review evidence.
