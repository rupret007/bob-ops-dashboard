# StoryPool control plane

The conductor snapshots the PR backlog. The dispatcher applies machine and
provider guards, excludes never-touch repositories, requires local Karen findings
at the exact PR head, and creates a leased job in the locked JSONL ledger.

On Linux the dispatcher submits each fix worker as an independent user transient
service (`storypool-job-<id>`, Type=exec, collected on exit). Its working directory
and Python module path point at the installed repository; output goes to the job
log. Submission errors fail dispatch without falling back to a child in the
oneshot dispatch cgroup. No systemd-run helper PID is recorded as a worker PID.
On macOS the worker starts in a new session and the dispatch launchd plist sets
AbandonProcessGroup so it can outlive dispatch. The worker records its own PID and
owns ledger transitions, runs tests, pushes only the PR branch, verifies the
remote SHA, and requests Karen review. Only the Karen gate can merge.

Each dispatch tick reconciles running PIDs before allocating lanes. A missing
worker becomes failed with error `worker-died`; queued leases expire separately.
A successful process exit alone is not a verified fix. The dashboard reads state
and logs without mutating jobs; its separate confirmed POST controls persist node maintenance. Watchdog diagnoses health; self-update deploys
pushed branch commits outside the monitor sandbox. Peer health and complete
provider preflight coverage remain outstanding; unknown health is not success.

The regression suite checks Linux transient service submission, failure without
unsafe fallback, macOS process-group preservation, dead-PID reconciliation, and
Python 3.9 compatibility. Real service survival on each host must also be verified
from post-deployment jobs; unit tests do not establish deployment health.

Karen distinguishes concrete findings from review infrastructure errors. Secret
findings bypass model providers and become a persisted KARENFAIL only after the
full head SHA is rechecked. The artifact contains only the security check, with
file:line and rule names; source lines and credential values are never copied.
These failures retain high-risk handling in the fix dispatcher. Stale heads,
provider failures, and persistence failures remain retryable ERROR with no
published verdict. Known scanner fixtures use exact whole-line patterns in two
specific Karen test files, including the bogus private-key body and terminator;
other test files and adjacent credentials still fail the scanner.

Node maintenance lives in `~/.local/share/storypool/node-control.json`, survives
reboots, and fails closed on malformed state. A locked atomic write records each
operator action in HANDOFF.md with America/Chicago time and source address.
Dashboards bind :8790 and restrict requests to loopback or Tailscale addresses.
Control POSTs additionally require a literal confirmation, JSON, and matching
Origin/Host; public Host names are rejected to prevent DNS rebinding. Both node
buttons proxy only to configured peer URLs, with no public proxy and a bounded
timeout. Unreachable peers return an explicit unconfirmed result and never change
local maintenance. Acknowledged remote state updates the badge without claiming
fresh remote job telemetry.

Draining stops dispatcher allocations, queued workers before execution, Karen
reviews, conductor actions, ops-doctor repairs, and mesh worker fetches/capability
advertisement. Mesh messages already fetched are negatively acknowledged for
redispatch. Running jobs finish normally; stale/dead leases use existing failure
reconciliation and become eligible for redispatch. Watchdog records maintenance
without restarting or escalating the node. Engine health renders drained nodes
grey and excludes them from failed health. Self-update remains available so a
maintenance host can receive repairs; returning to service requires another
confirmed POST. The independent Web Bob shell driver still needs the planned
single-scheduler migration; these controls cover repository-managed services.

Oversized Karen reviews use lossless file-boundary chunks capped at 12,000 diff
characters (long files and single lines are continued with explicit file context).
Every chunk retains the original full head SHA, whole-PR risk tier and complete
file manifest. The primary and independent MiniMax reviewer must both PASS every
chunk. Each stored review includes its chunk number, total, character range, file
header and SHA, so complete coverage can be audited. Gemini receives the complete
supplied chunk with no silent tail truncation. A failing chunk blocks approval;
provider errors stay retryable ERROR. The final gate still rechecks the live head
before persisting any verdict. Cross-file uncertainty must block with BORDERLINE;
chunked review does not certify access to unchanged repository files.


Fix workers now use the shared `service_logging` JSON contract: America/Chicago
timestamp, service, machine, level, job, repository, exact SHA and correlation ID
(the ledger job ID). Commands record start, exit code and duration. Failed pytest
commands emit test node IDs without parameter values or assertion contents.
Exceptions retain the complete frame chain and exception type, excluding source
lines, locals and exception strings. Provider output, prompts and diffs are never
logged. Ledger failures point to the structured log rather than including argv.
Each supervised worker owns a separate log file to avoid multiprocess rotation
races; files rotate at 5 MiB with seven backups and expire after seven days.
`STORYPOOL_LOG_LEVEL` defaults to INFO; DEBUG is available. `STORYPOOL_LOG_DIR`
overrides the default `STORYPOOL_DATA_DIR/logs`. The worker's existing job log also
receives the JSON events on stderr. Other services and the dashboard Logs view
still need migration to this shared contract; this does not claim that coverage.

## Cluster authority audit (2026-10-18)

The target authority is **one NucBox JetStream account and bucket**, reached by
both hosts over the tailnet. Never use a Mini-local broker as a fallback. NATS KV
[create and compare-and-set](https://docs.nats.io/nats-concepts/jetstream/key-value-store)
provide atomic admission and revision fencing. This migration is incomplete;
Mini Codex remains disabled until every work entry point and quota is covered.

| Component/state | Owner | Required scope | Race prevention / current gap |
| --- | --- | --- | --- |
| Fix admission | Shared `STORYPOOL_CLAIMS` KV | Cluster, repo/PR | Implemented: create-only PR key; full SHA in value; separate heads cannot race on one branch. |
| Fix lifecycle/ledger | Worker; local JSONL is an observation | Cluster claim, machine-local process | Implemented: persisted claim handoff, CAS heartbeat, CAS release; worker commands fenced on loss; command process group stopped. Expired claims recover after broker TTL cleanup. |
| Karen poller/review | Karen | Cluster repo/PR/SHA | GAP: local seen/retry state can duplicate reviews. Must adopt shared lease before providers. |
| Verdict storage/sync | Signing reviewer | Cluster immutable repo/PR/full SHA | GAP: signed sync exists; local caches are not one canonical artifact index. |
| Merge attempt | Karen exact-SHA gate | Cluster repo/PR/SHA | GAP: exact-SHA gate exists; add shared admission and idempotent merge outcome. |
| Priority queue/fix attempts | Dispatcher/conductor | Cluster | GAP: local snapshots/counters; shared claims prevent duplicate fix admission, not starvation or attempt double counting. |
| Codex weekly accounting | Provider account | Cluster, one weekly plan | GAP: machine-local usage cannot authorize increased lanes. Shared stop and fresh provider quota needed. |
| Lanes and guards | Node supervisor | Cluster allocation + node limits | GAP: local caps only. Keep Mini off and NucBox at existing limits. |
| Drain/maintenance | Operator | Cluster desired state, node enforcement | GAP: node JSON/control proxy can diverge during outages; central revisioned desired state needed. |
| Pause/cancel/requeue/actions | Operator | Cluster idempotency key + target | GAP: controls need shared claims and durable action IDs, not only local audit. |
| Metrics/scorecard | Event reducer | Cluster unique event/job/verdict IDs | GAP: aggregate observations can double count; canonical deduplicated events needed. |
| Digests/NEEDS_JEFF | Conductor/monitor | Cluster date/alert fingerprint | GAP: local append can duplicate alerts; daily leader claim + deduplication needed. |
| Ops doctor/Web Bob/website | Scheduler | Cluster task/branch | GAP: independent entry points must use claims before launching Codex. Web Bob remains draft-only. |

### Claims deployment and failure handling

`ClaimClient` never creates a bucket during admission and never falls back to a
local lease on missing config, authorization denial, broker outage or TTL
mismatch. New fix dispatch pauses. Existing pre-deployment workers finish under
their original supervisor. A new worker without its handed-off claim fails and
must be redispatched. A dead owner's lease expires in 300 seconds plus broker
cleanup latency. Every command renews before and after execution; a heartbeat
renews during long commands. Ownership loss kills that command's process group
and prevents subsequent commits/pushes. Like any lease around external GitHub
operations, it cannot retract a request already accepted by GitHub; pinned heads
and verified remote outcomes remain required.

Outside the monitor sandbox, provision a dedicated coordination identity in the
**same account on both hosts**, with only publish to
`$KV.STORYPOOL_CLAIMS.>`, `$JS.API.STREAM.INFO.KV_STORYPOOL_CLAIMS` and subscribe
to its own inbox. A separate operator identity needs bucket-create permission
for the one-time bootstrap. The current installed legacy identities lack KV
publish permission; do not broaden general worker permissions or share admin
credentials. Set `~/.config/storypool/coordination.json` on each host with `url`,
`user`, `token_file` (a protected KEY=VALUE token file; no credential in URL), or
use `STORYPOOL_COORD_URL`, `STORYPOOL_COORD_USER`, `STORYPOOL_COORD_TOKEN_FILE`.
The Linux transient unit forwards those three non-secret settings; macOS
inherits them. Run `python3 -m cluster.control_plane.coordination --bootstrap`
with the operator identity before enabling the restricted service identity.
Missing setup intentionally pauses new fixes after deployment. Do not enable the
Mini lane or raise throughput until live claims, remaining entry-point migrations,
shared quota, distinct-PR work and the 24-hour duplicate audit are verified.
