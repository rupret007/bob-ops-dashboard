# StoryPool health consolidation

Research and inventory: 2026-10-18 CT. Baseline `858a3b5`. Jeff explicitly
approved implementation at 20:23 and requested research before code at 20:25.
This approval supersedes item 7's design-only restriction. It does not open
architecture items A–G or change Karen, quota, maintenance or merge admission.

## Recommendation

Run one **native Python health daemon per machine**, supervised by launchd on
the Mini and user systemd on the NucBox. Retain the existing check implementations
as adapters first; combine their scheduling, observation records, repair ownership
and alert lifecycle. Integrate shadow's observational dispatch/gate evaluations
at their existing cadence, rather than deleting those useful diagnostics.

Use `health-status.json` as the sole health authority after cutover, with complete
check details, original source, observation/expiry times, errors, repair results,
peer evidence and rollout receipt. Diagnostic logs are history, not health state.
`NEEDS_JEFF.md` gets one managed health section from this daemon. Other authorized
operator/conductor notes remain owned by their existing writers; consolidation
does not authorize deleting human requests or rewriting their meaning.

Keep health native when future item F containerizes the control plane: the host
must still observe/recover an unavailable container runtime. This recommendation
fits the already-decided hybrid architecture, not an alternative to that decision.

## Research: proven mechanisms and their boundaries

Kubernetes separates startup, liveness and readiness. Startup shields a slow boot
from premature liveness restarts; a readiness failure withdraws admission without
killing the process. Its probe thresholds and timeouts are useful patterns here,
but installing Kubernetes on two workstations adds a control plane without solving
our quorum problem. Adopt the taxonomy and bounded probes. A GitHub outage must
not cause a dashboard restart. [Kubernetes probe
semantics](https://kubernetes.io/docs/concepts/workloads/pods/probes/),
[configuration](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-probes/).

systemd supplies process supervision, restart delays and start-rate limits.
`WatchdogSec` requires the service to send `WATCHDOG=1` notifications; setting it
without implementing that protocol would manufacture failures. Use
`Restart=on-failure` with bounded backoff and rate limits. The Python scheduler
owns service-specific recovery, while systemd owns restarting the health process.
Do not use host hardware watchdog/reboot behavior. [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html),
[systemd.unit rate limits](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html).

launchd's `KeepAlive` supervises process existence and `ThrottleInterval` limits
rapid respawn. It does not verify HTTP, job outcomes or Karen. Use a long-running
health process with KeepAlive and a throttle; do not combine an unconditional
KeepAlive with an intentionally short, successful timer command. Per-user agents
still require Jeff's login after reboot, as he chose. The service cannot bootstrap
itself while unloaded: installers must verify registration, and missing daemon
heartbeats must turn dashboard health unknown. [Apple launchd
guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html),
[Apple plist specification](https://github.com/apple-oss-distributions/launchd/blob/main/man/launchd.plist.5).

Monit demonstrates useful cycle-based thresholds, timeout budgets and restart
limits. Consecutive failures alone miss alternating failure/success patterns;
retain transition history and a cooldown so a flapping service does not restart
each cycle. Monit can supervise commands on both platforms, but domain checks and
markdown projections would still need Python adapters. [Monit
manual](https://www.mmonit.com/monit/documentation/monit.html).

Consul supports active probes and TTL heartbeats, with separate failure and
success thresholds. Expiry is an observation failure, not proof of machine death.
Apply that rule to peer snapshots. Consul would add agents, registration and a
server quorum without removing our domain adapters. A two-server quorum cannot
remain writable after either server fails. [Consul check
API](https://developer.hashicorp.com/consul/api-docs/agent/check),
[deployment guidance](https://developer.hashicorp.com/consul/docs/install/production).

Google SRE's golden signals map to time-to-verdict/merge (latency), verified
jobs/reviews/merges (traffic), failed jobs/reviews/check execution (errors), and
load/swap/disk/lanes/quota (saturation). A responsive HTTP port establishes only
liveness. Record useful work separately. Avoid treating ERROR reviews as product
FAILs or a paused queue as evidence of broken service processes. [SRE monitoring](https://sre.google/sre-book/monitoring-distributed-systems/).

For a future measured 99% availability SLO, burn rate is observed bad-event ratio
divided by 0.01. Paired 1h/5m at 14.4 and 6h/30m at 6 are starting patterns for
fast alerts, not measured StoryPool results. Job-volume SLOs need sufficient
traffic: one failure in one job is statistically different from 1,000 failures.
Keep the explicit no-verdict/no-success-within-60m rule and minimum sample counts
until canonical event windows exist. Do not fabricate burn-rate history from
mtime or enable merge/restart actions based on a derived SLO. [SRE SLO
alerting](https://sre.google/workbook/alerting-on-slos/).

Alertmanager separates alert fingerprints, grouping, inhibition and resolution.
Adopt stable machine/check fingerprints; group repeated observations in place;
resolve only after a positive new observation. Inhibition suppresses notification
noise while retaining the underlying failure. Alertmanager's timeout semantics
are not permission to declare an unobserved local check healthy. A full
Prometheus/Alertmanager stack becomes attractive when retained metric windows and
external receivers are needed; no email/Slack notification is authorized here.
[Alertmanager](https://prometheus.io/docs/alerting/latest/alertmanager/),
[routing and resolve configuration](https://prometheus.io/docs/alerting/latest/configuration/).

## Options for two machines

| Option | Benefits | Costs / failure modes | Decision |
| --- | --- | --- | --- |
| Native health daemon + native process supervision | Reuses all detailed checks; low memory; works with Python 3.9/macOS and Linux; local repairs survive broker outage | We own a small scheduler/state machine; need fault tests and heartbeat expiry | **Implement now**, preserving adapters first |
| Monit on each host + custom program checks | Mature process/resource monitoring and restart damping; portable | New package/config dialect; application checks, one status and alerts still custom; overlapping owners during migration | Viable, more migration work for little removal of custom logic |
| Consul agents + active/TTL checks | Standard node/service catalog and thresholds; future service discovery | Additional servers/quorum/ACLs; two physical machines insufficient for safe loss-tolerant consensus; custom outcome and dashboard integration remains | Defer; existing NATS direction makes a second consensus stack poor fit |
| Prometheus + Alertmanager + exporters | Strong historical queries, SLO windows, grouping/inhibition/resolution | Several services/storage; extra Mini pressure; exporters and recovery owner still needed; alertmanager is not a repair supervisor | Future observability backend, not today's consolidation |
| Kubernetes probes/operators | Standard startup/readiness/liveness and controllers | Large runtime/control-plane change; macOS VM and quorum costs; domain semantics still custom | Pattern reference only |

The choice is engineering judgment from the inventory and Mini memory guard,
not a benchmark. Research does not claim any alternative was deployed or measured.

## Complete legacy inventory and mapping

All paths below are under `cluster/control_plane/`. Dynamic rows mean **every**
installed agent/timer or PR matched by the old code, not a sampled subset.
Preserve legacy fields `name`, `passed`, `message`, `can_repair`, `repaired`,
`repair_action`, `execution_error`, plus full shadow decisions and smoke diagnostics.

| Old check / behavior (source evidence) | New observation / action | Old → new cadence |
| --- | --- | --- |
| Guardian loop, installed `com.storypool.*.plist`, registration/pending reload, live-marker Karen exclusion (`guardian.py:159`) | `guardian` complete action list; same reload/registration verification, exclude retired health units after activation | 120s → 120s |
| All installed `storypool-*.timer` enabled and active (`guardian.py:185`) | `guardian` same dynamic inventory, excluding explicitly retired timers | 120s → 120s |
| Dashboard GET `/`, no proxy/redirect, restart then HTTP verification; port-release wait (`guardian.py:24,65,117`) | `guardian.dashboard` with response/restart results; same bounded reload path | 120s → 120s |
| Guardian accumulated successful reload count/status/repair failure alert (`guardian.py:211`) | Cumulative reloads + actions in unified status, managed alert with auto-resolve | 120s → 120s |
| `shadow_stuck`, `dispatch_stuck`, `conductor_stuck`, `poller_stuck` (Mini only), `dashboard_stuck`; dashboard `/health`, 30m/75m thresholds (`watchdog.py:244`) | Same names/fields in `watchdog.checks`; same log selection and thresholds | 600s → 600s |
| `shadow_tracebacks`, `dispatch_tracebacks`, `conductor_tracebacks`, `poller_tracebacks` (Mini only), `dashboard_tracebacks`; last 500 lines, >5 (`watchdog.py:288`) | Same detailed checks; retain recent-lines meaning (legacy is **not** truly a one-hour timestamp window) | 600s → 600s |
| `gh_available` and common PATH fallbacks (`watchdog.py:338`) | Same check, paths and fix-path finding | 600s → 600s |
| `codex_available`, common binary paths, Mini SSH fallback (`watchdog.py:371`) | Same check and diagnostic; no provider invocation | 600s → 600s |
| `labels_rupret007_webjam`: needs-karen/karen-pass/karen-fail (`watchdog.py:410,660`) | Same repo and exact label checks; shared API budget retained; defer means unknown, not positive recovery | 600s → 600s |
| `install_stale`, fetch dry-run/revision comparison (`watchdog.py:513`) | Same detailed check; trigger existing self-update only under recovery ownership | 600s → 600s |
| `head_lag`, >30m since update receipt (`watchdog.py:449`) | Same detailed check; stable incident fingerprint | 600s → 600s |
| Watchdog restart_service, create_labels, self_update (`watchdog.py:570,597,624`) | Same allowlisted actions; verified return code, per-target cooldown and lock; label changes restricted to existing Karen flow | On failed check → same, damped |
| Peer `/api/engine`, self-only jobs/resources/flow, sync result and original timestamp (`watchdog.py:173,209`) | Peer group stores direct self report, receive time, source time; no echoed cluster verdict | 600s → 300s |
| Broken-main followup/revert PR + shared coordination (`watchdog.py:730`, `main_ci.py`) | `main_ci` adapter retaining reconciliation and claim gates; no merge authority granted | 600s → 600s |
| Doctor smoke: recent job, queued/start/done, verified commit_pushed, outcome SHA changed (`smoke.py:24`) | `doctor` bounded complete diagnostics | 3600s → 3600s |
| Smoke live GitHub head equals job pushed SHA, verdict full SHA, verdict PASS (`smoke.py:30`) | Same source checks, distinguishing findings from execution failures | 3600s → 3600s |
| Doctor second watchdog --check-only, detailed stdout/stderr (`ops_doctor.py:128`) | Reuse current full watchdog result; retain it in doctor record, avoid duplicate API probe | 3600s → at least 600s |
| Doctor findings-only exit0, execution error nonzero (`ops_doctor.py:151`) | Typed finding/error result; failures visible without poisoning service process exit | 3600s → 3600s |
| Doctor optional isolated repair, 3/day, shared task claim, tests/secrets/draft-only PR (`ops_doctor.py:43`) | Preserve manual repair entrypoint and budget/lease gates; installed hourly mode remains findings-only | Manual → manual |
| Shadow guards (load/swap/disk/lanes), provider pause/budget/filters, candidate selection (`shadow_runner.py:219`) | `shadow.dispatch_decisions` complete observations, no dispatch | 900s → 900s |
| Shadow every configured repo/open non-draft PR: fetch, draft/CI/approval checks, budget, would_need_llm (`shadow_runner.py:335,438`) | `shadow.gate_decisions`, same fields and reasons; no LLM call, merge or push | 900s → 900s |
| Shadow totals, dates, per-decision extra fields and logs (`shadow_runner.py:467`) | Full `shadow` group retained plus diagnostic history | 900s → 900s |
| Honest green: completed full-SHA verdict + verified success within60m, no stale alerts (`dashboard/engine_flow.py`) | `flow` group at120s; existing dashboard outcome rule preserved | dashboard refresh → refresh and120s |
| Maintenance suppresses repair; poller live marker; shared claims/quota/never-touch | All adapters keep these admission gates; health cannot create a bypass | Every action → every action |

Inventory caveats: the legacy checker sometimes calls missing/unreadable logs a
pass; this diagnostic remains visible, but cannot prove readiness. NucBox skips
the poller log check today; inventory preserves that platform behavior rather than
claiming a second installed poller. Future item E adds those installations.

## Scheduling and probe taxonomy

One scheduler loop uses monotonic due times; wall-clock UTC timestamps support CT
display and peer exchange. Start all groups once on boot, then schedule from their
start times. At most one in-flight invocation per group; no catch-up stampede.
Fast guardian/flow groups have a separate execution lane from slow probes. Slow
subprocesses have process-group deadlines, so a stuck GitHub/doctor/shadow check
cannot consume the fast lane. Publish a heartbeat while work is running. A failed
probe process yields ERROR, never an empty successful result. Persist completed
results atomically; overdue/missing/invalid observations are yellow.

| Class | Meaning | Repair / admission |
| --- | --- | --- |
| Startup | Manager registered; dashboard listener can bind and answer after reload | Bounded existing socket release/startup waits; no premature second restart |
| Liveness | Local HTTP responds; expected timer/agent is registered | Local recovery owner may restart/reload; cooldown prevents races |
| Readiness | dependencies, GitHub quota/labels, guards, config/version fresh | Yellow/blocked with reason; no new paid work while its existing gate blocks |
| Work flowing | Full-SHA Karen verdict and verified job in60m, no stale NEEDS_JEFF | Yellow with exact reason; never manufacture jobs/PASSes to turn green |
| Peer freshness | Direct authenticated private endpoint, original node identity/time | Expired/partitioned peer is unknown; never remote-restart or elect leader |

The Nuc doctor runs the actual local queued → running → pushed → exact-SHA
Karen smoke. Mini has no enabled Codex fix lane, so its doctor observes that
same Nuc pipeline through a fresh nonce-validated response, using the peer clock
and the full revision explicitly supplied by its own health scheduler. The Nuc
doctor must have a completed positive `pipeline_smoke`, exit code 0, and probe
times after activation of that revision. Mini's local services remain covered
by watchdog. This observation does not qualify the whole peer cycle: deployment
admission still separately requires the complete positive Nuc canary. Neither
an absent job nor a failed or stale Nuc smoke becomes a healthy result.

Thresholds: transient GitHub execution failures notify only after three consecutive
failed *observations*. A budget-deferred check neither resolves an incident nor
proves success. Show pending/warning immediately. Genuine local repair failure is
visible immediately. Successful new checks auto-resolve their own incident. Every
repair target has a minimum five-minute cooldown; retain attempt and transition
history across daemon restarts. Maintenance inhibits repair notifications/actions
without deleting evidence or clearing failures as healthy.

## Ownership, alerts, peer evidence and item E

One local advisory recovery lock covers watchdog repairs, guardian restarts,
installer reloads and cutover. Reentrant calls inside the owner are permitted;
independent processes serialize. Per-agent reload locks still protect definition
changes. Lock ownership alone is insufficient: re-check authority, maintenance,
cooldown and current need after acquisition. During side-by-side observation only
legacy services repair/write health alerts. After cutover only the new daemon
does so; old definitions stay on disk and disabled. Installers must not resurrect
them on the next update.

Incident identity is `(machine, group, check name)`; dynamic values/durations stay
out of the key. Preserve first_seen, last_seen, consecutive failure count and
resolved_at. Rewrite one delimited section under the shared alert file lock and
atomic replace. Do not resolve on timeout, missing observations, daemon restart,
or API backoff. Resolve on a new positive observation, archive once, and preserve
all operator text outside the managed section. Repeated writes are idempotent.

Scheduler exceptions have their own stable `<group>_execution` incident. The
parent runner must receive a nonempty, valid adapter result within the existing
group deadline before recording execution completion. The scheduler resolves
that execution incident only when the completed result has no group error,
execution-unknown flag or check execution error. Adapter replies cannot supply
the scheduler's reserved execution check name. Missing, malformed, pending or
overdue completion provides no recovery evidence. Semantic findings retain their
original check names, states and uncertainty: a completed probe can resolve its
old execution failure while an unhealthy or unverified finding remains open.

Peer data includes schema, machine, original timestamp, receive timestamp and
expiry. Reject wrong machine, future timestamps and oversized payloads; expose
only redacted status on a private read-only endpoint. A peer can be reachable but
its reporter stale. Local status and remote cached status keep separate identities.
No new central health leader or cross-host repair is part of this rollout.

For item E, these records become local reporters to a **separately fenced** elected
combined-health publisher (item C). Readiness is an input, never the lease itself.
Tailscale suspicion cannot authorize takeover: the singleton must acquire the
shared lease and verify fencing/claims before side effects. Local repairs continue
when NATS is unavailable; new distributed work remains fail-closed. See the
[shared-state design](failover-shared-state.md) for the three-failure-domain quorum
requirement. No two-node health heuristic can solve split brain.

## Test and rollout contract

1. Tests first: exact inventory/detail preservation; fake-clock due times and slow
   isolation; three GitHub failures/reset; no resolve on unknown; alert dedupe and
   positive resolution; two processes racing for recovery; maintenance; native
   templates/Python3.9; expired/wrong-peer status; dashboard authority switch.
2. Install candidate alongside existing services in **observe-only** mode. Complete
   every group once, including hourly smoke and shadow. Compare old/new check IDs,
   fields and outcomes, retaining timestamps and explaining any changed inputs.
   Never run two recovery owners during this stage. Receipt contains exact code
   head, machine, group coverage, comparison and execution errors.
3. NucBox canary first: only a passing local comparison can disable its four old
   timers/services and activate new recovery. Keep original unit files. Prove a
   subsequent full candidate cycle, HTTP response, fresh status and no loss of
   checks; publish a receipt. Missing proof leaves observation mode in place.
4. Mini may activate only after fetching the NucBox's fresh successful canary
   receipt for the same code revision. Repeat its own side-by-side comparison;
   disable/bootout the four old agents without deleting plists. Verify the new
   daemon remains loaded. A receipt from another revision is not sufficient.
5. Dashboard reads only unified status once the local authority switches. Missing
   new status is yellow; never silently fall back to old green logs. Peer rollout
   compatibility remains explicit during the staggered migration.
6. Rollback under the recovery lock: suppress new repairs, restore the saved
   legacy enable/load state, verify it, then change authority. Preserve incidents,
   logs and candidate status. A partial manager failure must restore or visibly
   retain the last functioning owner; never record successful cutover prematurely.

The monitor sandbox cannot manipulate user service managers or the Mini's launchd.
Delivery uses the approved normal push/self-update path. Installation, comparison,
cutover and canary receipts must be observed from that outside-sandbox runtime;
unit tests or a push alone are not a completed rollout.

## Execution evidence

At research baseline both direct dashboards answered HTTP, but used old green
logic; Mini swap was81.6%, over its unchanged80% guard. Both ledgers showed zero
active worker jobs. This is not a flow-qualified green or service-manager audit.
Implementation and live rollout evidence will be appended here with actual heads
and outcomes; no success is inferred from the design.
