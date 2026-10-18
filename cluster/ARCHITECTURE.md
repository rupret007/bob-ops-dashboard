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
other test files and adjacent credentials still fail the scanner. A reviewed, finite
set of complete synthetic credential literals is also accepted in test sources
(including .test.mjs and release/test_*.py); prefix matches and altered values
are never exempt. Both secret and static scans call the same rules. Documentation
and comment text are excluded from command/security scans, while secrets remain
scanned there. Only literal RegExp .exec calls and inert static test DOM markup
are excluded; dynamic DOM, child_process.exec and window.eval remain findings.
Test description renames preserve bodies; changed/deleted assertions and new
skips/xfails remain FAIL with file:line evidence. Findings never include source
or credential values, and all evidence is passed to the fix-loop verdict reason.
Missing result-label removal is an idempotent DEBUG no-op; other GitHub failures
still propagate.

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
| Karen poller/review | Shared PR claim; Karen owns its lease | Cluster repo/PR, pinned full SHA | Implemented: gate and CLI acquire the same create-only key as fix workers; heartbeat and provider/publication fences; contention/outage returns retryable ERROR with no verdict or label failure. Local poll history is only an observation. Sequential duplicate review/cache deduplication remains open. |
| Verdict storage/sync | Signing reviewer | Cluster immutable repo/PR/full SHA | GAP: signed sync exists; local caches are not one canonical artifact index. |
| Merge attempt | Conductor under shared PR claim | Cluster repo/PR, pinned full SHA | Implemented: claim before live merge; revalidate Karen and GitHub under ownership; pinned GitHub merge; heartbeat through CI observation. Contention/outage defers without counting a failed merge. Global durable outcome/event deduplication remains open. |
| Priority queue/fix attempts | Dispatcher/conductor | Cluster | Fix admission now reserves a durable exact-head retry record under the shared PR lease; CAS prevents stale record writes. Local queue snapshots and starvation prevention remain gaps. |
| Codex weekly accounting | Provider account | Cluster, one weekly plan | GAP: machine-local usage cannot authorize increased lanes. Shared stop and fresh provider quota needed. |
| Lanes and guards | Node supervisor | Cluster allocation + node limits | GAP: local caps only. Keep Mini off and NucBox at existing limits. |
| Drain/maintenance | Operator | Cluster desired state, node enforcement | GAP: node JSON/control proxy can diverge during outages; central revisioned desired state needed. |
| Pause/cancel/requeue/actions | Operator | Cluster idempotency key + target | GAP: controls need shared claims and durable action IDs, not only local audit. |
| Metrics/scorecard | Event reducer | Cluster unique event/job/verdict IDs | Dashboard review observations deduplicate synced regular LATEST/result copies by owner/repo/PR/full SHA/review timestamp, preserve distinct retries, normalize legacy boolean strings, and exclude ERROR from PASS/FAIL rates. Conflicting copies display FAIL conservatively. GAP: cross-node aggregates still need canonical event IDs and trusted first-pass attribution; display counts never authorize merges. |
| Digests/NEEDS_JEFF | Conductor/monitor | Cluster date/alert fingerprint | GAP: local append can duplicate alerts; daily leader claim + deduplication needed. |
| Ops doctor | Shared task claim; local supervisor | Cluster repository/repair branch | Implemented: stable task key excludes machine and head, exact base SHA pinned, heartbeat and command fences through draft PR publication. Contention/outage does not consume a paid attempt. Local daily counters and shared provider quota still need migration. |
| Web Bob/website | Scheduler | Cluster task/branch | GAP: independent entry points must use claims before launching Codex. Web Bob remains draft-only. |

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

### Karen and merge admission

Local conductor merge history preserves rate counts and operator/CI pauses with
a flushed, file-synced temporary file and same-directory atomic replacement.
Unreadable, malformed or structurally invalid history (including invalid numeric
timestamps and dangling links) blocks merges instead of resetting limits. Resume
cannot overwrite damaged history; an operator must restore valid history first.
Only an absent file on first start initializes empty history. Failed writes retain
the previous complete state and remove temporary files. This protects each local
history file; shared merge counters and cross-process read/modify/write fencing
remain part of the outstanding cluster-state migration above.

Karen publishes each complete artifact with a same-directory atomic rename,
then atomically replaces `LATEST` with a prepared relative symlink. Readers see
the old or new complete marker without an unlink gap; failed writes/replacements
clean temporary files and preserve the prior marker. Newer FAIL siblings still
block an older PASS marker, and a retained FAIL cannot authorize a newer PASS.
This protects publication during process interruption; it does not claim power-loss
durability or replace the shared PR claim or trusted verdict sync.

The production Karen gate acquires the PR claim after fetching the full head,
before any deterministic verdict publication or provider call. CLI and poller
use this same entry point. A ContextVar carries the lease through chunked review;
each primary/fallback and second-judge call checks ownership before and after the
request. A background heartbeat renews long requests. Lost ownership prevents the
next provider request, verdict save, or optional comment. In-flight provider
requests may finish but cannot authorize publication after ownership is lost.
Infrastructure failures are ERROR diagnostics, never durable KARENFAILs.

Conductor acquires that same claim for a live merge and revalidates Karen at the
full pinned SHA under ownership. Missing GitHub state fails closed, and closed or
merged PRs cannot issue another merge. GitHub receives --match-head-commit as a
final fence. Claims are held through the initial main-CI observation, then released
with revision checks. Broker errors and contention defer work without recording a
failed merge. A request already accepted by GitHub cannot be retracted on later
lease loss; durable global merge receipts and post-merge CI reconciliation remain
required. Shadow mode never acquires or mutates claims.

This change intentionally extends the existing configuration requirement to
Karen and conductor merges: both pause new work until the shared account and KV
identity described above exist on both nodes. It does not enable Mini Codex or
assert that the remaining global-state migrations are complete.

### Ops doctor admission

The hourly doctor may run local smoke/health checks independently. A paid repair
first resolves the full remote base SHA, then atomically claims
`task.<hash(repository#ops-doctor:branch)>` in the same shared bucket. Both nodes
therefore contend even when they observed different base heads. The claim stores
the real task name and full SHA; no invented PR number is used. The winning node
reserves its local daily attempt only after acquiring and renewing the claim.
Busy/missing/unreachable coordination defers normally, without a failed-service
state, paid attempt, or repeated Jeff escalation.

The worker checks out the pinned base and holds/renews ownership through Codex,
tests, secret scanning, commit, branch push and draft PR publication. Every command
uses the existing supervised process-group runner and checks the lease before and
after execution. Ownership loss stops a running command and fences subsequent
publication. Raw provider/test output is not appended to doctor diagnostics;
shared structured logs retain safe failing test names and command timing. As with
PR claims, TTL plus revision fencing allows recovery without deleting a successor.
An already accepted remote GitHub request cannot be retracted. Global Codex quota,
centralized daily counters, Web Bob scheduling and action deduplication remain
open; this change does not authorize enabling Mini Codex.

### Deterministic merge admission and Karen disk contract

Every conductor tick evaluates the open PR snapshot before optional model calls.
The default verdict root is `~/.local/share/karen/verdicts`; the flat
`owner_repo_N_sha12_LATEST.json` marker is required. The newest embedded timestamp
across LATEST/PASS/FAIL wins, with conflicting equal-time failures blocking merge.
The authenticated signed receiver atomically writes LATEST after validation and
replay rejection; previously only outcome files were synced to NucBox.
Artifacts must match owner, repository, PR and full SHA. Two distinct reviewers
must agree on PASS; a GitHub review from the repository owner is unnecessary.
Malformed markers, partial/error reviews and legacy verdicts cannot authorize a
merge. Legacy reads remain available for diagnostics. Drafts, missing/red CI,
non-CLEAN mergeability and never-touch repositories fail closed.

Live attempts retain the shared PR claim, under-claim revalidation, rate limits,
SHA-pinned squash merge and initial main-CI/revert handling. The Mini plist now
contains a real `--live` argument; the self-update installer reloads and kickstarts
the conductor. Shared coordination provisioning is still required on both hosts.
Durable shared merge receipts/counters and complete delayed main-CI reconciliation
remain open architecture work; an initial pending CI result is not verified green.


### Main CI recovery (GitHub authority)

PR CI rollups use one shared parser in the conductor and shadow runner. Check
names are display labels, not unique identities: a green result cannot overwrite
a same-named failed or unfinished result. Malformed entries and explicit
unfinished check runs fail closed, including during revalidation under the
shared merge claim. The rollup does not establish rerun identity from list order;
the separate main-CI API reader uses app/check IDs for that purpose.

Conductor now checks main before each live merge and defers on red, pending or
unknown results. This is a repository-wide pause observed by both hosts rather
than a machine-local failure counter. CI inspection combines all paginated check
runs and commit statuses, using the newest rerun per app/check. Missing or failed
API responses cannot establish green health.

The existing ten-minute watchdog cadence revisits GitHub's merged PR receipts
from the past 48 hours, including after a restart. No new timer or local receipt
store is introduced. The initial post-merge observation and watchdog follow-up
inspect the actual squash merge commit, not the pre-merge PR head. Follow-up uses
the same shared PR claim and heartbeat as merge admission; contention/outage
pauses work. A red result creates an actual git revert on a separate deterministic
branch and opens a needs-karen PR. It never pushes main, force-pushes or merges the
revert. Existing revert PRs are reused; a crash after branch push is recoverable
only when the rebuilt and published trees agree. A newer healthy main suppresses
obsolete reverts. Conflicts and publication failures defer for retry; no success
is inferred from them. Main remains protected by the live pre-merge check.

Both hosts must receive this version and the restricted shared coordination
configuration before autonomous merge verification is possible. The existing
watchdog cadence allows a normal follow-up within ten minutes; broker/GitHub
outages cannot guarantee the fifteen-minute objective and must remain visible as
deferred. Global rate counters, quota and remaining authority gaps above are
still open; this change does not enable Mini Codex.
Follow-up diagnostics use `~/.local/share/storypool/main-ci.jsonl`, separate from
the watchdog health snapshots so an action record cannot imply healthy checks.

## Supervisor registration and recovery

Every Mini installer now validates the rendered plist, explicitly enables its GUI
service, boots out the old definition, bootstraps the new definition and verifies
registration. Previously the installers used legacy unchecked unload/load pairs;
unload also interacts with launchd's persistent disabled state, and a failed load
could leave the watchdog absent. Historical logs do not establish which caller
unloaded it twice. Per-agent file locks prevent installer/guardian reload races.
The self-updater writes a pending reload marker while its launchd PID is running;
the guardian applies the new definition only after that process exits, avoiding
self-termination during reinstall. Invalid definitions never unload the old agent.

The Mini installer installs the repository guardian wrapper and renders both its
repository path and the plist's `__HOME__` placeholder. The existing two-minute
LaunchAgent cadence is retained. NucBox gets a two-minute user timer which restores
only installed `storypool-*.timer` units that are inactive or disabled. Neither
guardian starts provider work directly; existing maintenance and shared-claim gates
still apply. Shadow Karen stays unloaded until live mode has been authorized.
Recovery outcomes and failures use rotating structured `logs/guardian.jsonl`;
`guardian-status.json` retains the last check and cumulative verified recoveries.
The dashboard shows recovery counts and peer sync age; reachable peers with old
health evidence stay yellow with a stale-report explanation. Guardian telemetry is
copied with peer engine snapshots, never echoed back as the local host's state.

Mini peer health defaults to its verified tailnet HTTPS hostname on port 8790;
`STORYPOOL_MINI_URL` remains the explicit deployment override. Plain HTTP to the
TLS listener returns 400 and cannot be treated as machine failure.

The guardian also GETs `http://127.0.0.1:8790/` every cycle, without proxy
settings or redirects. HTTP failures trigger launchd dashboard reload on Mini or
a systemd service restart on NucBox, followed by bounded HTTP verification.
The shared Mini reload helper requires dashboard HTTP success after bootstrap
and kickstart, so installer/self-update cannot report a registered but dead
dashboard as successful. Periodic agents may legitimately be idle; their reloads
verify launchd registration. A failed dashboard restart is logged and appended to
NEEDS_JEFF.md once per host per UTC day. Guardian status and the dashboard show
verified/failed/unknown HTTP evidence separately from service registration.

A failed reinstall now returns FAILED and retains `.storypool-reinstall-pending`
in the installation checkout. At the next tick, even when local and remote SHA
match, the updater reruns the gate and retries installation. Only a verified
installation removes the marker; updating Git alone cannot hide a failed deploy.


### Exact-head retry admission

`STORYPOOL_CLAIMS_RETRIES` is a second KV bucket in the same NATS account,
with **no TTL** and history=1. Work claims still expire after 300 seconds;
retry records must survive reboot, node changes and claim expiry. The existing
`coordination --bootstrap` operator command provisions both buckets. Admission
never creates storage, and absent storage or permission denial pauses new fixes.
The service identity additionally needs publish to `$KV.STORYPOOL_CLAIMS_RETRIES.>`,
stream info for `KV_STORYPOOL_CLAIMS_RETRIES`, and read access via
`$JS.API.STREAM.MSG.GET.KV_STORYPOOL_CLAIMS_RETRIES` (or the corresponding
`$JS.API.DIRECT.GET.KV_STORYPOOL_CLAIMS_RETRIES.>` subjects when direct reads
are enabled), plus its own inbox. Bucket creation remains operator-only.

Dispatcher owns the shared PR lease while reading and reserving a retry record
keyed by normalized repo, PR number and **full** head SHA. Every read/write renews
the claim, and writes use the record revision (create-only for new keys). A stale
writer cannot overwrite a successor's reservation. Reservation consumes one
attempt before worker launch, so crashes and submission errors cannot evade the
three-attempt cap. Transient attempts wait 5, 10, then 20 minutes; the third attempt
exhausts the exact-head budget. Existing local history is imported conservatively
when reserving. A new full SHA has a fresh budget.

Workers use controlled exception codes for missing package-lock.json, protected/
closed/fork PRs, changed heads and unsupported test commands. Ledger and durable
state preserve the code and a fixed, useful reason without arbitrary exception
text, provider output or credential values. These failures never launch Codex and
remain blocked until the head changes. A verified push closes the old head's
budget. Existing lockfile, test, secret, protected-repo and exact-head Karen merge
gates remain required. Dashboard shows blocked PR, head, reason and next action
in a safe-text, read-only list. Real deployment requires provisioning/permissions;
unit-test success does not establish that the live bucket is ready.

Claim expiry and operator visibility: JetStream can expire a key between the
create-only CAS and the client's follow-up read. A missing key at admission is a
busy/deferred attempt; missing keys during renewal or release mean claim lost.
Neither path authorizes work, exposes broker details, or deletes a successor.
Incomplete broker API errors become a generic coordination pause. Real expiry
races and injected client exceptions are covered on Python 3.9 and 3.14.
The engine snapshot separately reports fresh dispatcher admission state; shared
coordination failures turn the overall engine yellow while machine health badges
retain their actual health. Reports older than fifteen minutes are unknown.
The Python 3.9 CI job installs the pinned test broker, and shared requirements
include pytest so unittest discovery can import every safety test module.

Peer endpoint discovery (2026-10-18): private addresses belong in runtime
configuration, never the published checkout. Health sync, remote controls and
signed verdict push share `peer_endpoints.py`: explicit `STORYPOOL_MINI_URL` /
`STORYPOOL_NUCBOX_URL`, then `~/.config/storypool/peers.json` (override path with
`STORYPOOL_PEERS_FILE`), then bounded `tailscale status --json` discovery by unique
machine hostname. Mini dashboard discovery always uses its HTTPS MagicDNS origin;
missing or ambiguous identity fails closed. Explicit origins accept only
localhost, loopback, tailnet IPs or HTTPS MagicDNS, without credentials or queries.
The verdict share discovers this host's tailnet interface, refuses wildcard bind,
and retains the explicit `STORYPOOL_VERDICT_SOURCE` / `STORYPOOL_VERDICT_SHARE_BIND`
overrides. Raw verdict pulls retain the existing tailnet-only share on 8791;
signed verdict push remains the authoritative delivery path. Discovery failure
never changes claim, verdict authentication, exact-head approval or merge guards.
Watchdog records redacted `machines/<peer>/sync-status.json` on each successful
or failed sync, retaining the last health snapshot. Dashboard badges show sync
failure and its next action separately from the peer's last health evidence.

### CI identity and peer evidence freshness

Main-CI follow-up groups superseding attempts by app, check name and check-suite
ID. Independent workflows with identical job names retain every red or pending
result; absent suite identity retains each run. Karen requests workflow identity
from GitHub and conservatively retains the worst duplicate status. List order is
never evidence that an unrelated workflow has superseded a failing check.

The dashboard preserves the last reported machine health separately from peer
contact. A newer failed sync or a peer snapshot older than 15 minutes makes the
peer badge yellow even when the last watchdog report was green. Fresh snapshots
clear superseded sync failures. A real red watchdog result remains red.

The shipped broker template includes a dedicated `coord` service identity with
claim/retry KV publish, exact stream-info and read subjects, and only its own
reply inbox. It cannot create/delete/purge streams or publish jobs. The broker
installer adds a missing COORD_TOKEN without rotating existing credentials and
renders protected config without passing credentials in process arguments.
An ephemeral secured-broker regression exercises acquire/renew/retry read-write/
release and verifies administrative subjects remain denied on both CI runtimes.
This template is a fresh-install contract: existing broker ACLs still require
the documented operator migration/reload; monitors never bypass unavailable
retry storage or silently rewrite an installed broker configuration.

Peer endpoint provisioning: watchdog, verdict push and dashboard controls resolve
explicit `STORYPOOL_<MACHINE>_URL`, then private `~/.config/storypool/peers.json`.
Managed monitors can provision the same non-secret mapping in
`~/.local/share/storypool/peers.json` (`STORYPOOL_DATA_DIR` overrides that directory).
An explicitly selected `STORYPOOL_PEERS_FILE` is authoritative. Each endpoint must
remain a private HTTP(S) origin; malformed configuration fails closed. Runtime
provisioning avoids requiring a local tailscaled in WSL when its Windows host
provides tailnet routing. Without either mapping, discovery still uses tailscale.
Peer address failures are distinguished from transport failures in dashboard
sync evidence, and last verified health is retained.

Coordination bootstrap also disables stream-wide rollup headers on both KV
streams. Ordinary CAS create/update/delete remains available, but a service
credential cannot erase every claim/retry by publishing Nats-Rollup: all.
Existing operators must rerun bootstrap with the operator identity when applying
the restricted ACL migration; production rollout is not inferred from tests.
Dashboard blocker projections survive log rotation and are validated before
rendering; full-head observations supersede stale blockers. Verified successful
exact-head ledger history prevents redispatch even if final KV persistence fails.
