# Bounded main-CI receipt renewal

Main-CI follow-up uses the same shared GitHub request admission as other
StoryPool services. Local git commands remain local. GitHub operations retain
the remaining whole-batch timeout; the batch budget stays at 120 seconds.

The native scheduler runs the group every 120 seconds. Completed observations
still expire after 600 seconds. Renewal becomes due after 480 seconds so a
receipt can be refreshed before its next scheduling interval would expire it.

The account limit stays at 40 admitted requests per minute. Only an explicitly
typed shared-minute exhaustion can wait inside a batch, in steps of at most
five seconds and within the original deadline. The PR lease is released and
progress is persisted before waiting. Primary quota reserves, rate-limit
backoff, unknown errors, and unavailable coordination do not receive this
minute retry behavior.

An admission deferral cannot refresh a receipt timestamp. Existing observations
may be reused only within their original 600-second lifetime. New pending or
unknown observations replace prior success and remain unconfirmed. Inventory
discovery deferral is also unconfirmed, including when no saved records exist.

A deterministic simulation covers 63 receipts, three GitHub operations per
receipt, eight requests per minute reserved for other services, bounded partial
batches, and steady renewal across sixteen scheduling intervals. It preserves
the strict 40-per-minute limit and 600-second acceptance threshold. Production
still requires observed coverage and diagnosis of genuine unknown CI results.

Implicit current-CI checks resolve the repository's default branch and commit
together through one shared-budget GraphQL read. Check runs and commit statuses
then use that immutable commit SHA, including when the default is `master` or
the branch advances during the reads. Missing or malformed metadata remains
unknown. Explicit refs, including post-merge squash commits, retain their
existing two REST requests and CI interpretation. Scheduled resolution uses
the existing background allowance and whole-batch deadline.
