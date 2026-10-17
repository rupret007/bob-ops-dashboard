# Lanes on JetStream: one source of truth for LLM lane work

Status: **stage 1 (library + tests) only.** Nothing live changes until Karen gates each stage and the
hub's `nats.conf` gets the permission delta below (a Jeff-approved config change; no token changes).

## Why
LLM lane work today runs outside the cluster: a flat queue file, JSON claim files with long TTLs,
and `screen` sessions counted to cap concurrency. The JOBS stream has only ever carried a handful of
test jobs. That split caused the 10/16 incidents: duplicate fill loops, double-booked PRs, claims
held for hours after their lanes died, and an idle NucBox while the file queue ran dry.

## Design (this module, `cluster/lanes.py`)
| Need | JetStream primitive |
|---|---|
| No double-booking across machines | KV `LANE_LEASES`: `create` succeeds only if the key is absent (atomic) |
| Leases that free themselves | Bucket TTL (default 900 s); lanes heartbeat with `renew` every ~300 s |
| No zombie writes after a lapse | Revision = fencing token; `renew`/`release` are compare-and-set |
| One writer per PR branch | Lease key = repo + PR/issue (`rupret007.webjam.pr.390`), not job id |
| No duplicate jobs | Stream `LANES` `duplicate_window` 2 h + `Nats-Msg-Id` = job id @ head SHA |
| Per-provider concurrency cap | One durable consumer per provider, `max_ack_pending` = lane target |
| Crash recovery | Explicit ack, `ack_wait` 600 s with `in_progress` heartbeats, `max_deliver` 3 |
| Empty queue is normal | `pull` returns `[]` on timeout (the worker crash loop was an unhandled timeout) |

Delivery is at-least-once. Exactly-once *effects* come from the lease: a lane takes the lease for
its PR before acting, acks only after its RESULT/BLOCKER line, and `term`s work that must not retry.

## Stages (each keeps the current lanes running)
1. **Library + tests** (this PR). No live effect.
2. **Shadow:** fill-autoqueue / gemini-feed also `enqueue` every job they write to the file queue,
   and claim-gate also mirrors each claim into `LANE_LEASES`. A report compares the two every
   self-improvement round. File system stays authoritative. Needs the permission delta.
3. **Leases authoritative:** claim-gate takes the KV lease first and falls back to files only if the hub
   is unreachable (fail closed for PR-opening work). File claims become a read-only mirror.
4. **Dispatch from NATS:** a Python dispatcher (replaces the fill.sh loop) pulls per provider; NucBox
   lanes pull directly, so its idle cores take work without a Mini round-trip. The file queue is
   kept as an input adapter for one week, then removed.
5. **Retire** `fill.queue`, file claims and screen counting; dashboards read consumer state.

## Hub permission delta (stage 2, not applied)
- Operator user (NucBox): publish `lanes.>`, `$KV.LANE_LEASES.>`, and the stream/consumer/KV
  create APIs for `LANES` and `KV_LANE_LEASES`.
- Each member lane host: publish `lanes.>` (enqueue) and `$KV.LANE_LEASES.>`, plus
  `$JS.API.CONSUMER.MSG.NEXT.LANES.lane_<provider>` and `$JS.ACK.LANES.lane_<provider>.>`
  for the providers it actually runs, and `$JS.API.DIRECT.GET.KV_LANE_LEASES.>` for reads.
- Guests: none. Lane jobs carry repo work and never go to the GUEST account.
