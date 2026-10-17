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
| Leases that free themselves | Bucket TTL (default 900 s); lanes heartbeat with `renew` every ~300 s. A changed TTL is applied to an existing bucket |
| No zombie writes after a lapse | Revision = fencing token; `renew`/`release` are compare-and-set |
| No lease theft by a member host | Hosts never write `$KV.LANE_LEASES`; they call the hub `LeaseService` on `lanesvc.lease.<host>.<op>`, which binds the owner to `<host>` and does the CAS |
| One writer per PR branch | Lease key = repo + PR/issue (`rupret007.webjam.pr.390`), not job id or host. Lossy tokens get a hash suffix (`My.Repo` ≠ `My-Repo`) |
| No duplicate jobs | Stream `LANES` `duplicate_window` 2 h + `Nats-Msg-Id` = provider:job@head-sha (+ `#a<attempt>` for a retry; headless jobs key on a spec hash) |
| Per-host concurrency cap, own-ack only | One durable consumer per provider **per host**, `lane_<provider>_<host>` on `lanes.<provider>.<host>.*`, `max_ack_pending` = that host's lane target |
| Known providers only | `PROVIDERS` is an allowlist for enqueue, subscribe and `ensure_lanes` |
| Crash recovery | Explicit ack, `ack_wait` 600 s with `in_progress` heartbeats, `max_deliver` 3 |
| Lease already held on delivery | `take()` naks with delay = lease TTL (not term, not instant redelivery) |
| Poison jobs | `DeadLetterWatcher` copies MAX_DELIVERIES jobs to `LANES_DLQ` (then deletes from `LANES`, never before); the copy's `Nats-Msg-Id` is `dlq-` + the job's own enqueue msg-id, not the LANES sequence, so a recreated stream cannot drop a different job as a duplicate; `dead_letters()` lists them for the dashboard |
| Config drift | `ensure_lanes` applies editable changes in place; a non-editable difference (err 10012) raises `LaneConfigConflict`, and recreate only happens with `recreate_idle=True` and nothing in flight. Stream updates touch only managed fields |
| Empty queue is normal | `pull` returns `[]` on timeout (the worker crash loop was an unhandled timeout) |

Delivery is at-least-once. Exactly-once *effects* come from the lease: a lane takes the lease for
its PR before acting (`take`), acks only after its RESULT/BLOCKER line, and `term`s work that must not retry.

**LeaseHeld policy.** A delivery whose lease is held (usually a redelivery while the first lane is still
running after a missed `in_progress`) is `nak`ed with a delay of one lease TTL. On nats-server 2.15.1 a
delay-nak'd job keeps its `max_ack_pending` slot until it is redelivered (verified in
`test_lease_held_job_is_nakked_with_delay_not_lost`), so a held job costs one slot of that host's target
for up to one TTL. Each nak counts toward `max_deliver`; a job that keeps hitting a held lease ends up in
`LANES_DLQ` with its delivery count, where the conductor can re-enqueue it with `attempt=N+1`.

**Enqueue return value.** `False` means this exact work (provider, job, head SHA, attempt) is already in
the window. It is not an error and not "done"; to retry on the same SHA pass the next `attempt`.

## Stages (each keeps the current lanes running)
1. **Library + tests** (this PR). No live effect.
2. **Shadow:** fill-autoqueue / gemini-feed also `enqueue` every job they write to the file queue
   (choosing the host the file lane would use), and claim-gate also mirrors each claim through the lease
   service. A report compares the two every self-improvement round. File system stays authoritative.
   Needs the permission delta and the hub running `LeaseService` + `DeadLetterWatcher`.
3. **Leases authoritative:** claim-gate takes the lease via the service first and falls back to files only
   if the hub is unreachable (fail closed for PR-opening work). File claims become a read-only mirror.
4. **Dispatch from NATS:** a Python dispatcher (replaces the fill.sh loop, operator/conductor side) picks a
   host per job from per-host consumer state and enqueues to `lanes.<provider>.<host>.<job>`; each host
   pulls only its own consumer. The file queue is kept as an input adapter for one week, then removed.
5. **Retire** `fill.queue`, file claims and screen counting; dashboards read consumer and DLQ state.

## Hub permission delta (stage 2, not applied)
The exact grants live in `cluster/lanes_perms.py` and are proven on a real nats-server by
`cluster/tests/test_lanes_perms.py` (a member's raw `$KV` put/delete, KV stream delete/purge, another
host's lease subject, another host's MSG.NEXT/ACK, enqueue, and inbox sniffing are all denied, while its own
lease → pull → ack path works). Nothing reads that module at runtime and `nats.conf.template` is unchanged.

- **Operator (hub, NucBox):** publish `$JS.API.>`, `$KV.LANE_LEASES.>`, `lanes.>`, `lanes_dlq.>`;
  subscribe `lanesvc.lease.*.*` and `$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.LANES.*`; responses allowed.
  Runs `LeaseService`, `DeadLetterWatcher` and `ensure_lanes`. The only identity that writes lease keys.
  `LeaseService` drops requests with a nonempty reply address outside `_INBOX_<host>.` before any
  lease operation, using the host from the request subject. Rejections log no request fields.
- **Conductor (Mini fill loop):** publish `lanes.*.*.*` (enqueue) and read-only `LANES_DLQ` info/get;
  subscribe `_INBOX_conductor.>`. No `$KV`, no consumer APIs.
- **Each member lane host `<host>`:** publish only
  - `lanesvc.lease.<host>.<op>` for `acquire`/`renew`/`release`/`holder` (its own host token);
  - `$JS.API.CONSUMER.MSG.NEXT.LANES.lane_<provider>_<host>` and `$JS.ACK.LANES.lane_<provider>_<host>.>`
    for the providers it runs (its own consumer only, so it cannot ack, nak or term another host's job);
  - explicit deny `$KV.>`, `$JS.API.STREAM.>`, `lanes.>`.
  Subscribe only `_INBOX_<host>.>`; hosts connect with `inbox_prefix="_INBOX_<host>"` so one host cannot
  read another's replies or deliveries. No `$KV.LANE_LEASES.>` for any member, and no shared-consumer ack.
  Host names `hub` and `conductor` are reserved (`RESERVED_HOST_NAMES`): `check_host` refuses them, so no
  lane host can be given `_INBOX_hub` (the operator's JetStream API replies) or `_INBOX_conductor`.
- **Guests:** none. Lane jobs carry repo work and never go to the GUEST account.
