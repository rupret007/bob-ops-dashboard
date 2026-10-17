# StoryPool demand-based lane scheduler research

## Summary

Recommend a small StoryPool admission controller over NATS JetStream, with one local supervisor on Mini and one on NucBox. Start lanes only for queued, eligible work that fits fresh host capacity and reserved provider budget. Scale idle lanes to zero. Keep queue delivery, permission to execute, and resource accounting separate.

This is research, not an implementation or deployment approval. Host/provider limits below are operator requirements supplied for this study, not vendor plan guarantees. Survey conclusions are based on the primary sources listed below, opened during this research on 2026-10-17. Proposed timing values are tuning candidates.

**The LeaseService lease-reply HIGH issue is CLOSED by [bob-ops-dashboard #76](https://github.com/rupret007/bob-ops-dashboard/pull/76) (merged `c838b310`). StoryPool still must not reuse its reply paths.** Capacity uses publish-only `metrics.capacity.<host>` subjects, following the existing `metrics.<host>` identity pattern. New grant and status events also use dedicated publish/subscribe subjects; no LeaseService calls, reply headers, shared inboxes, or fallback through that service. Native JetStream delivery/API traffic is a separate protocol and still requires independently tested permissions.

## Survey

| System | Mechanism | What fits | What does not fit |
| --- | --- | --- | --- |
| NATS JetStream | Durable pull consumers let clients request bounded batches. `MaxAckPending` (`max_ack_pending` in configuration) limits outstanding unacknowledged messages across all subscriptions bound to a consumer; delivery pauses at the limit. Explicit acknowledgments and redelivery support recovery. [NATS consumer reference](https://raw.githubusercontent.com/nats-io/nats.docs/master/nats-concepts/jetstream/consumers.md) | Existing transport; pull only after admission; small batches avoid hoarding long jobs. | A consumer limit is neither a machine-wide cap across consumers nor a provider budget. Redelivery can overlap a still-running process; an ack is not a fencing mechanism. |
| KEDA queue-length scalers | Queue metrics drive Kubernetes replica targets. The JetStream scaler selects account, stream and consumer through a monitoring endpoint; `lagThreshold` sets its average target and `activationLagThreshold` gates activation. [JetStream scaler, v2.18](https://keda.sh/docs/2.18/scalers/nats-jetstream/) | Borrow backlog-per-slot targets and separate activation from scaling. | Adding Kubernetes for two existing hosts is unnecessary. Raw lag cannot distinguish quota-blocked, dependency-blocked and executable work. |
| KEDA cooldown and HPA | KEDA's `cooldownPeriod` applies to returning to zero; HPA controls scaling among positive replica counts, including stabilization behavior. [ScaledObject specification](https://keda.sh/docs/2.18/reference/scaledobject-spec/) | Separate idle teardown delay from stabilization of target changes. | A cooldown must never prolong unsafe execution or permit grants from stale telemetry. A positive fallback replica count on metrics failure conflicts with fail-closed admission. |
| Nomad Autoscaler | Policies are evaluated at an `evaluation_interval`; handlers obtain metrics, calculate desired counts, invoke targets and honor cooldown after scaling. [Policy evaluation](https://developer.hashicorp.com/nomad/tools/autoscaling/concepts/policy-eval) | Borrow the observe, decide, reconcile loop and explicit policy bounds. | A Nomad deployment adds another control plane; policy evaluation alone cannot reserve a shared dollar budget or reclaim a running CLI process safely. |
| Ray Autoscaler | Reconciles resource demand against current capacity and worker-group limits, fits pending demand, and drains idle instances after a timeout. Task scheduling remains separate from autoscaling. [Autoscaler v2](https://docs.ray.io/en/latest/ray-core/internals/autoscaler-v2.html) | Borrow demand/capacity matching and separate desired, starting, running and draining states. | Ray task/actor resources and node provisioning do not directly describe fixed macOS/WSL machines or account quota windows. CPU demand is not spending permission. |
| GitHub Actions Runner Scale Sets / ARC | Kubernetes runner scale sets can grow with assigned jobs and shrink when jobs finish. `minRunners` adds idle capacity; `maxRunners` bounds growth. Setting both to zero drains admission. [ARC deployment](https://docs.github.com/en/actions/how-tos/manage-runners/use-actions-runner-controller/deploy-runner-scale-sets) | Borrow bounded job-driven growth, ephemeral execution and an explicit drain switch. | GitHub Actions job assignment is not StoryPool dispatch. ARC does not supply cross-provider budgeting or our host guards. Keep Actions CI separate. |
| Celery | Worker concurrency bounds execution; prefetch is generally the multiplier times concurrency. With early acknowledgment, multiplier one can still reserve work beyond executing tasks. Late acknowledgment supports retry but requires idempotency. [Optimizing workers](https://docs.celeryq.dev/en/latest/userguide/optimizing.html) | Borrow low prefetch for long lanes and separate reserved work from running work. | Replacing NATS with Celery is unnecessary. Concurrency and prefetch do not enforce global account budgets or exactly-once external effects. |
| Temporal | Worker slots represent concurrent task capacity. Fixed, resource-based and custom slot suppliers control when workers may poll and execute. Resource-based targets are not guaranteed hard ceilings. [Worker performance](https://docs.temporal.io/develop/worker-performance) | Closest admission model: obtain a permit before accepting execution, return it after confirmed completion. | A full workflow-engine migration is larger than this problem. CPU/memory tuning alone cannot enforce swap, disk, provider windows or project priority. |

## Recommended design for our two machines

### Capacity publication and trust

Publish a versioned capacity snapshot every 10 seconds on `metrics.capacity.mini` and `metrics.capacity.nucbox`. Proposed expiry is 30 seconds. Include host identity, boot ID, monotonically increasing sequence, sample time, policy version, load measurement/window, swap used and total, free disk bytes, configured lane limit, counts by provider/state, unknown processes, and guard failure reasons. Never substitute zero for an unreadable metric.

Authenticate identity from subject permissions, not a payload hostname. A host may publish only its own capacity/status subjects; the scheduler may subscribe to both. Explicitly add these subjects in a later reviewed permission change: permission for `metrics.mini` does not automatically allow `metrics.capacity.mini`. Existing broad dashboard subscriptions to `metrics.>` must tolerate the new schema or filter it. Do not widen host permissions to `metrics.>` or generic inbox access.

Use receiver monotonic time for expiry, plus sequence/boot checks and bounded sample-time skew so delayed packets cannot refresh old measurements. A scheduler restart begins with zero usable capacity until fresh samples and a process/grant reconciliation complete. A local supervisor rechecks guards immediately before every start and periodically while running; a central snapshot is never sufficient permission by itself.

### Need computation: queued work x available capacity

Here “queued work x available capacity” means matching demand against feasible slots, not multiplying two counts. Count distinct runnable job attempts, excluding completed, dependency-blocked, delayed retry, already granted and running attempts. Keep blocked backlog visible separately. A host-targeted pending message must not be counted again as unassigned demand.

For each scheduling tick:

- `Q[p,t]` is runnable, ungranted work for provider `p` and project tier `t`.
- `C[h]` is the configured host ceiling; `U[h]` counts all occupied or reserved slots, including starting, running, draining, stopping and unknown lanes across every provider and launcher.
- `A[h,p] = max(0, C[h] - U[h])` only when that host/provider combination passes fresh guards, reservations and compatibility checks; otherwise it is zero.
- Match jobs to slots under shared host and provider constraints. For one compatible pool, new starts are bounded by `min(Q, A, provider_permits)`. For multiple pools, decrement the shared residual capacities after each grant; never sum independently calculated provider maxima.
- Desired occupancy is current nonterminal occupancy plus admitted starts. An empty queue gives zero new starts and zero idle target, not an instruction to kill healthy jobs already executing.

Example: NucBox has four occupied slots and ten eligible jobs; at most three new lanes can start, subject to provider budgets and the Codex reservation. Ten queued jobs with stale capacity admit zero. An idle machine with no queued work admits zero.

### Host policy and reservations

| Host | Admission conditions | Capacity policy |
| --- | --- | --- |
| Mini (macOS) | Load `<10`; free disk `>=15 GB`; swap `<80%` for ordinary lanes, `<90%` only for Gemini review lanes. | Require an explicit validated Mini lane ceiling; it was not supplied. Until configured, usable capacity is zero. The Gemini exception is a per-job classification, never a host-wide relaxation. |
| NucBox (WSL) | Load `<12`; fresh capacity; queued eligible work. | At most **7 total lanes**, including every provider, review lane, borrowed lane and manually started lane. Two named slots are reserved for Codex whenever Codex jobs wait. |

Use existing validated definitions of load, swap percentage and free disk where available; missing definitions block activation pending Jeff's decision below. External workloads can push measured load/swap/disk beyond thresholds after admission. The enforceable guarantee is no admission across a failed guard and bounded stop response, not that a scheduler can mathematically prevent all OS metric excursions.

On NucBox, model five general slots and two Codex slots. Only Cursor composer may borrow the two Codex slots, and only while no Codex jobs wait. Codex may also use general slots. Preserve the reservation even when waiting Codex work is quota-blocked; this is deliberately conservative and may leave capacity idle.

When Codex arrives, revoke Cursor borrowing immediately and stop further provider calls in borrowed lanes. Checkpoint/cancel those attempts and confirm process exit before granting those physical slots to Codex. While cancellation is pending, the slots remain occupied and unavailable; never count revoked slots as free. Cursor borrowing is allowed only for work with a tested bounded cancellation path; otherwise leave reserved slots idle. This separates an immediate reservation decision from a non-instantaneous process stop. Ordinary tier priority does not kill healthy work by default.

### Provider windows, pace and budget reservations

Keep a single durable account-level ledger across both hosts, keyed by provider account and window ID. Never give each host a separate copy of the whole budget. Track authoritative usage, settled costs, outstanding maximum-cost reservations, observation age, reset evidence and uncertainty. Reconcile observations without double-counting settled usage. Reads from approved usage telemetry must not require inspecting authentication files or chat databases.

A token bucket refills at a configured rate with a bounded burst allowance. It is useful for pacing, but is not itself a hard spending ceiling; AWS explicitly distinguishes throttling targets from guaranteed limits. [Token-bucket throttling](https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-request-throttling.html) The following are proposed StoryPool policies, not claims about that service:

| Provider | Admission and continuation policy |
| --- | --- |
| Codex | Weekly used percentage must be known, valid and no more than 2 hours old. At `>=75%`, stop new work and further provider calls. Missing or older readings fail closed. Reserve a conservative upper bound in percentage points for each bounded work chunk; require observed usage plus unreported/reserved usage to remain below 75%. If usage cannot be bounded or reconciled, do not grant autonomous calls. A fresh low reading does not erase outstanding reservations. |
| Gemini | Enforce a `$20/hour` pace bucket with an explicit small burst cap, plus an independent `$118/day` hard ledger. Reserve maximum request/chunk cost before execution and settle actual cost afterward. Require settled plus unreported plus reserved daily spend `<= $118`. At the cap, no more calls. Do not accrue a day's worth of burst credits while idle. |
| Claude | Model each applicable session window separately: remaining allowance, reset time and freshness. Require permits in every overlapping limit window. On limit response, block until authoritative reset evidence; do not guess allowance from elapsed time alone. Unknown allowance fails closed. |
| MiniMax | Prefer for suitable inexpensive work; default to one global lane, configurable to two maximum across both machines. It still consumes host slots and any configured request/cost quota. Cheap does not mean unlimited. |
| Cursor composer | Counts toward host capacity and its own observed account/rate limits. May borrow only the designated NucBox Codex slots under the rule above; borrowing does not waive any other guard. Unknown required account limits block grants. |

Reserve budget and host capacity as one admission decision in the scheduler ledger before publishing a grant. Use bounded provider calls, maximum output limits and conservative price/usage bounds; renew reservations before more calls. A lane-wide estimate without enforceable call bounds cannot prove the hard ceilings. Other tools using the same accounts must participate in the ledger or have a separately deducted allowance; otherwise global spending safety is unprovable and autonomous admission stays disabled.

Default interpretation of `$20/hour` is a pacing rate, not a strict rolling-hour cap: bucket balance is bounded by burst size and refills at `$20/3600` per second. If Jeff requires no more than $20 in any rolling hour, add a rolling-window ledger; a bucket with bursts alone cannot guarantee that. Choose daily timezone and weekly/session resets from authoritative account windows. Never reset a ledger merely because a worker's wall clock crossed midnight.

### Grok Bot: tracked conserve-only budget

Jeff's 2026-10-17 00:33 CT policy: Grok Bot is a **tracked conserve-only budget**. Keep usage low; never fill or deliberately consume the available budget. It is not an execution pool, spare capacity, a refill target or a fallback provider. The scheduler **NEVER grants a lane that uses Grok Bot**. Lanes never consume this budget, including through nested calls, aliases or another provider's wrapper. Reject `grok-*` lane requests before reserving resources or publishing grants; enforce the same prohibition at local start/renewal and provider-call boundaries.

No direct Grok Bot usage source was found in this repository. `probe-agents-status.sh` reports conductor capability, and `live/agent.py` recognizes `grok-*` process names; neither measures Grok Bot budget consumption. Specify a UI budget entry named **Grok Bot**, with policy **conserve-only** and usage placeholder labeled exactly **"unknown"** until an approved direct usage source exists. Do not infer usage from process counts, idle state or capability labels; never invent percentages, spend, remaining allowance or reset times. When direct observations become available, track their source, timestamp and uncertainty in the account ledger without converting remaining budget into lane permits. Known low usage never relaxes the prohibition.

### Conductor and routine design notes

Keep Grok Bot use minimal in both conductor and routine design. Use deterministic queue/guard evaluation, cached observations, batched status summaries and event-driven updates to avoid repeated model calls. Routines must not invoke Grok Bot on every scheduler tick, poll it to fill a budget, or route lane execution through the conductor. Any necessary conductor use remains outside lane grants and is accounted for against the same conserve-only budget when directly observable; missing telemetry stays "unknown". Prefer fewer calls and concise context, with no utilization target or automatic spend-up behavior.

### Grant logic, delivery and fencing

Use a single active scheduler initially. Keep its durable grant/budget ledger behind a transactional authority that rejects stale scheduler epochs. An OS singleton lock alone is insufficient across hosts. No automatic takeover until previous authority is fenced and outstanding execution is reconciled; if unavailable, stop admission. Selection of the transactional store is an open decision, not permission to fall back to LeaseService.

A grant binds job/attempt, resource key (repository plus branch/PR), host boot ID, slot ID, provider, maximum budget, epoch, grant ID and expiry. Publish grants/revocations on dedicated `scheduler.grants.<host>` subjects and host acceptance/progress/exit events on `scheduler.status.<host>`. Use durable events and idempotent IDs so a lost publication can be retried. Application events use no reply subject. Only the scheduler can publish grants, and each host can report only its own status.

Reserve first; publish second. A grant that was committed but not delivered still occupies capacity until reconciled. Before starting, the host validates current authority, grant freshness, local guards and job identity, then atomically records the slot locally. Duplicate delivery must not spawn another process. Allow a short grant acceptance lifetime (candidate 30 seconds), with execution renewed in bounded chunks. Clock uncertainty or a missing authority heartbeat rejects renewals and stops further calls.

Keep per-provider/per-host durable pull consumers compatible with the existing lane layout. Pull at most the number of grants ready to execute, initially one message per permit. Bound `max_ack_pending` per consumer as a secondary delivery guard; the sum of consumer limits must not be mistaken for the shared host limit. Lowering it does not terminate existing lanes. Use explicit ack only after durable completion/result recording; maintain progress during long jobs and bound retries with a dead-letter path. All external writes must validate the current resource fence through the launcher/gateway; a fencing number carried in a message alone cannot stop an old process from pushing to GitHub.

The repository's `cluster/LANES.md` documents stage-one delivery, leases and per-consumer bounds. Reuse its delivery vocabulary, but its LeaseService paths and exactly-once-effect assertion are not sufficient safety evidence for this design. Test cancellation, fencing and idempotent effects independently before activation.

### Priority, fairness and anti-thrash

Schedule eligible Web Bob work first. Next serve Stalemate site, Turdanoid and Hang Out with equal-weight deficit round robin; then serve all remaining projects using the same policy. Use FIFO age as a tie-breaker, and measure deficits in estimated lane-time rather than job count so many small jobs do not monopolize service. Ineligible higher-tier work does not block compatible lower-tier work, except for the explicit Codex reservation. Report oldest wait and starvation by tier. Strict Web Bob precedence can starve lower tiers indefinitely; any aging promotion or minimum lower-tier share needs Jeff's approval.

Candidate controls: evaluate every 10 seconds; require two consecutive healthy samples before growth; add at most one lane per host per tick with 30 seconds between increases. Resume Mini below load 9, swap 75% (85% for Gemini review), and above 17 GB free; resume NucBox below load 10. These recovery margins are stricter than the hard thresholds. Require 60 seconds of stable recovery after a guard trip.

For ordinary nonzero demand variation, use a 60-second target stabilization window and a 60-second minimum interval between discretionary reductions. This borrows the stabilization concept documented by [Kubernetes HPA](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/). Hard guards, stale reports, quota stops and reservation revocations bypass cooldowns immediately. On an empty queue, cancel unused grants and disable pulling immediately; terminate idle lane processes after 30 seconds of sustained emptiness. Healthy running work finishes without a replacement. A tripped guard stops new starts/calls and initiates cancellation of affected lanes; retain their slot accounting until confirmed exit.

## Reuse vs build

Reuse NATS transport, durable pull delivery, explicit ack/progress, redelivery and dead-letter concepts. Reuse the metrics host identity convention, with a reviewed subject permission delta and a distinct capacity schema. Existing metrics collectors need validity information before they can be trusted for admission: error defaults must not look like healthy zeros.

Build the small policy layer: shared host inventory, account/window budget ledger, atomic grants, local supervision, authority/resource fencing, priority selection and reason-coded reconciliation. Borrow KEDA/Nomad/Ray control-loop ideas and Temporal's slot-permit model. Do not introduce Kubernetes, Nomad, Ray, Celery or Temporal solely to schedule two machines. Do not reuse LeaseService reply paths or treat a repaired service as automatically reviewed for this new purpose.

Start with replay/simulation, then shadow decisions with no execution authority, then a separately approved one-lane canary. This document makes no runtime, permission, deployment or test changes.

## Guard invariants

These are acceptance criteria for a future implementation. Guards and reservations are mandatory intersections; no priority, cheap provider, retry or cooldown may override them.

1. **Every lane counts:** at every grant transaction, occupied plus reserved slots on NucBox are `<=7`; Mini is `<=` its explicit configured ceiling. Starting, stopping, borrowed and unknown lanes count. Unknown inventory blocks growth; externally created excess lanes trigger reconciliation and stop, never a larger cap.
2. **Mini guards:** no start/renewal passes with load `>=10`, free disk `<15 GB`, or swap `>=80%`; only jobs classified as Gemini review use `<90%`. Missing, invalid or ambiguous measurements deny admission. Boundary tests cover equality.
3. **NucBox guard:** no new lane starts without queued eligible work and load `<12`. No queue means no replacement or speculative lane. Running jobs retain slots until exit.
4. **Codex reservation:** while any Codex jobs wait, no new non-Codex grant owns either reserved slot. Existing Cursor borrowers lose execution authorization and are stopped within the tested cancellation bound; their slots cannot be reassigned until exit. Without Codex waiters, only Cursor may borrow them.
5. **Freshness:** capacity age beyond 30 seconds, unknown boot/sequence state or failed local sampling yields zero new capacity. A Codex observation older than 2 hours, missing, nonnumeric or outside `[0,100]` denies Codex calls. Replayed packets cannot extend validity.
6. **Codex ceiling:** observed plus conservatively unreported/reserved weekly usage stays below 75% before every call; observed `>=75%` blocks continuation. Unbounded calls are inadmissible.
7. **Gemini budgets:** settled plus unreported plus reserved daily cost never exceeds $118 under the shared accounting boundary. Each call also holds pace tokens; no negative bucket balances or restart refills. Exhaustion stops continuation regardless of host capacity.
8. **Other providers:** Claude admits only with permits in all applicable windows; MiniMax occupancy never exceeds its configured global limit of one or two; Cursor receives no exemption from host or account checks.
9. **Atomic ownership:** one live grant per job attempt and resource key; only the current fenced authority may reserve budget or slots. Duplicate events neither debit twice nor spawn twice. Failure to prove authority denies execution.
10. **Safe release:** grant expiry, missing heartbeat or message redelivery alone never proves a process stopped. Retain uncertain slots/budget until fenced termination and cost reconciliation; no replacement can overlap an unfenced old attempt.
11. **Control-path isolation:** no capacity/grant/status event uses LeaseService subjects or reply paths. Host spoofing, grant publishing by a host and cross-host status publishing are denied by permissions.
12. **Bounded safety response:** every admitted provider chunk and borrowed lane has a tested stop bound (candidate 10 seconds after local detection). Guards run before calls and at least every 5 seconds. Hard-stop actions bypass stabilization; processes lacking enforceable bounds are not eligible for autonomous admission.
13. **Grok Bot exclusion:** the scheduler NEVER grants a lane that uses Grok Bot, including every `grok-*` request and indirect/wrapped use. No backlog, spare capacity, priority, retry, failover or known remaining budget overrides this guard. Grok Bot stays a tracked conserve-only budget; conductor and routines minimize use, never fill it, and missing direct usage is displayed as "unknown".

## Failure modes

| Failure | Required behavior |
| --- | --- |
| Stale/missing capacity or provider usage | Fail closed for affected capacity/provider; never infer idle from silence. Local guard independently stops new calls. Surface reason and observation age. |
| Split brain or scheduler restart | Ledger rejects stale epochs. New authority starts with no usable capacity and reconciles outstanding grants. Without proven fencing, no takeover or new grants. |
| Clock skew, wall-clock jump, delayed replay | Use monotonic durations, boot IDs and sequence checks; bound sender-time skew. Refuse ambiguous resets/expiry rather than minting budget. |
| Crashed lane holding slots | Reconcile process group and grant; reap only after exit/fencing proof. Retain uncertain spend. Retry the job under a new attempt fence. |
| Lost grant/status or disconnect | Retry durable events idempotently; keep uncertain grants charged. Heartbeat expiry stops further calls locally. Broker recovery does not authorize replayed grants. |
| Redelivery while old work still runs | Require attempt/resource fencing before execution or external effects; duplicate results are idempotent. Do not rely on `AckWait` or `max_ack_pending` for mutual exclusion. |
| Quota exhaustion mid-job or delayed billing | Reserve bounded chunks up front; checkpoint before another call. Unknown final cost remains reserved. External account spend without coordination disables the hard-budget guarantee and therefore admission. |
| Host guard trips during healthy work | Close admission immediately, stop affected calls/processes within the bound, and retain slots until exit. Apply recovery hysteresis before restarting. |
| Codex arrives while Cursor occupies seven slots | Revoke the two borrowed slots; no eighth lane. Codex waits for confirmed release. If cancellation fails, quarantine those slots and alert rather than oversubscribe. |
| Manual launcher bypass / untracked process | Inventory includes it; block growth and reconcile excess. Deployment requires every launcher to honor the supervisor gate. |
| Poison job or blocked high-tier backlog | Bounded retries with dead-letter visibility; distinguish eligible demand from blocked backlog. Preserve priority without an infinite retry storm. |
| Metrics schema or permission mismatch | Reject unknown schema and use zero capacity; validate exact subjects before canary. Never repair by granting broad publish/reply rights. |

## Test plan

No new tests or code are added in this research PR. Future implementation must demonstrate:

| Test group | Cases and expected result |
| --- | --- |
| Boundary/property tests | Mini load 9.99/10; swap 79.99/80 and Gemini-review 89.99/90; disk 14.99/15 GB; NucBox load 11.99/12; zero/one/many jobs. Random interleavings preserve invariants 1–4 with all providers counted. |
| Reservations and budgets | Race two hosts for the last slot/dollar; only one grant wins. Codex 74.99/75%, readings at 2h and just over, outstanding reservations, missing/NaN values. Gemini requests that reach/exceed $118, bucket burst/refill, daily rollover and restart persistence. Claude overlapping windows; MiniMax global 1/2 ceilings. |
| Grok Bot exclusion and tracking | Submit `grok-*` requests on both hosts with idle slots, high-priority backlog and low reported usage; repeat with unknown usage, retries, failover and Grok Bot hidden behind another provider's wrapper. Assert no grant is committed or published, no budget/slot reservation is created, and local start/renewal and provider-call gates reject bypass attempts. Confirm the UI usage placeholder is exactly "unknown" without a direct source, no numbers are fabricated, and conductor/routines have no budget-filling or per-tick Grok Bot call path. |
| Freshness and clocks | At/after 30-second TTL, reordered samples, duplicate sequence, reboot, future timestamps, suspended WSL, NTP jumps and scheduler restart: no stale admission or premature budget reset. |
| Borrowing/preemption | Fill seven NucBox slots with Cursor including two borrowers; enqueue Codex; verify immediate revocation, bounded stop and no Codex start before exit. Repeat with quota-blocked Codex and a stuck borrower. |
| Delivery/authority faults | Drop messages; duplicate grants; crash between ledger commit and publish, spawn and acceptance, completion and ack. Partition two schedulers; replay old epochs; kill process groups. Confirm no double spend, overlapping effects or unsafe slot release. |
| Real NATS permission tests | Publish-only capacity works for own identity; other-host telemetry/grants and LeaseService paths are unavailable to the new component. Verify pull/ack permissions separately with the deployed server/client versions, aggregate consumer caps, progress and redelivery behavior. |
| Fairness and thrash replay | Mixed projects and runtimes obey Web Bob, middle tier, rest ordering; equal-tier service uses lane-time deficits. Bursty queues and oscillating guards respect cooldown/recovery but safety stops bypass them. Empty queue removes idle lanes and running jobs finish. |
| Shadow/canary acceptance | Compare proposed grants with full process inventory and real sanitized usage reports; explain every denial. Activate only after bounds, accounting coverage and fencing are proven; rollback disables grants and reconciles existing work without forgiving reservations. |

Record decision inputs, policy version, grant/epoch, queue age, budget window, denial reason, actual start/exit, reclaim latency and provider cost error. Do not log credentials or job content. Pass criteria are invariant preservation under injected faults and measured stop/recovery bounds, not merely high lane utilization.

## Open decisions for Jeff

1. What is Mini's validated hard lane ceiling? Which load-average window, swap denominator and disk unit/filesystem are authoritative on macOS and WSL? Until explicit, fail closed.
2. Which transactional authority will own grants, account reservations and scheduler epochs independently of LeaseService? Who controls fencing of all launchers and external writes? Prefer no automatic failover initially.
3. Can each provider expose fresh usage and enforce bounded call cost/usage? How will non-StoryPool account usage be charged? Codex percentage estimates alone cannot prove a hard ceiling.
4. Is Gemini's $20/hour a pace or strict rolling-hour cap? What burst allowance and daily reset timezone apply? Which authoritative weekly/session windows apply to Codex and Claude?
5. Approve strict Web Bob priority with possible lower-tier starvation, or specify an explicit lower-tier minimum share. Are the three middle-tier projects equally weighted?
6. Confirm reserving two slots even for quota-blocked waiting Codex jobs. Which Cursor tasks can checkpoint/cancel within the stop bound? Unbounded work cannot borrow.
7. Validate the proposed 10-second publication/tick, 30-second capacity TTL, 5-second local guard checks, 10-second stop bound, growth cooldown and recovery margins against measured workloads.
8. Who signs off on the new publish-only permissions, separate authority, dashboard schema handling and one-lane canary? The lease-reply HIGH fix is CLOSED on `main` via #76 (merged `c838b310`); this research does not replace permission review for StoryPool control paths.

## Sources

Only URLs opened during this research are listed. Versions describe the surveyed documentation, not a requirement to upgrade deployed software. Design recommendations and operator limits are distinguished above from source-backed product mechanisms.

- NATS consumer configuration and `MaxAckPending`: https://raw.githubusercontent.com/nats-io/nats.docs/master/nats-concepts/jetstream/consumers.md
- NATS pull consumer patterns: https://docs.nats.io/learn/jetstream/pull-consumers
- KEDA JetStream scaler (v2.18): https://keda.sh/docs/2.18/scalers/nats-jetstream/
- KEDA ScaledObject and cooldown (v2.18): https://keda.sh/docs/2.18/reference/scaledobject-spec/
- Nomad Autoscaler policy evaluation: https://developer.hashicorp.com/nomad/tools/autoscaling/concepts/policy-eval
- Ray Autoscaler v2: https://docs.ray.io/en/latest/ray-core/internals/autoscaler-v2.html
- GitHub ARC runner scale sets: https://docs.github.com/en/actions/how-tos/manage-runners/use-actions-runner-controller/deploy-runner-scale-sets
- Celery worker optimization: https://docs.celeryq.dev/en/latest/userguide/optimizing.html
- Temporal worker slots and suppliers: https://docs.temporal.io/develop/worker-performance
- Kubernetes HPA stabilization: https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/
- AWS token-bucket throttling and limits: https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-request-throttling.html
