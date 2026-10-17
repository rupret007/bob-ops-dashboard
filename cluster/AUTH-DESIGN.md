# Bob cluster authentication and permissions

This describes the current PR implementation, replacing the initial review's
outdated permission table. Deployment requires a coordinated hard cutover;
source edits do not change live configuration.

## Accounts and delivery ownership

BOB contains Jeff's members, legacy workers, dashboard and the hub operator.
GUEST contains friends and hub-only guestops. JOIN contains pending code users
and joind, without JetStream. SYS is explicit with no external users. Only
GUEST sandbox heartbeats and results are exported to BOB; no APIs, inboxes or
jobs cross accounts. Members advertise detected local capabilities intersected
with their hub grants. Default member grants cover all member capabilities.
Guests receive sandbox.test only; jobs run in Podman with no provider keys.

I(u) = `_INBOX_<u>.>`. T(stream, consumer) grants exactly these publish subjects:

```text
$JS.API.CONSUMER.INFO.<stream>.<consumer>
$JS.API.CONSUMER.MSG.NEXT.<stream>.<consumer>
$JS.ACK.<stream>.<consumer>.>
```

| User | Publish | Subscribe |
|---|---|---|
| member u | caps.u, metrics.u, results.u.>, T(CAPJOBS,cap_<token>_n_<u>) for granted member caps | I(u) |
| guest u | sandbox.caps.u, sandbox.results.u.>; T(SANDBOX,guests_n_<u>) only with sandbox.test | I(u) |
| legacy mini/macbook | own metrics/results, T(JOBS,workers_<u>) | I(u) |
| nucbox operator | own metrics/results, jobs.>, cap.>, $JS.API.>, own legacy consumer ack | I(nucbox), results.>, caps.>, sandbox.caps.>, sandbox.results.> |
| dashboard | deny all | metrics.>, results.>, caps.>, sandbox.caps.>, sandbox.results.> |
| guestops | sandbox.jobs.*.*, API info; create/update/info SANDBOX and SANDBOX_RESULTS; create/info SANDBOX consumers | I(guestops), sandbox.caps.>, sandbox.results.> |
| pending user u | join.redeem.u | I(u) |
| joind | deny all statically; one dynamic response within 30 seconds | join.redeem.* |

WorkQueue consumers have disjoint filters: `cap.<token>.n.<node>.*`,
`sandbox.jobs.<node>.*`, and legacy `jobs.n.<node>.>`. Ack, nak, term and WPI
permissions cover only the node's durable, even if it learns a foreign reply
subject or guesses the timestamp/pending tokens. Members have no shared JOBS
fallback. Workers cannot create consumers, list streams or read stored jobs.
All clients use private inbox prefixes; no legacy worker can subscribe to
wildcard API, ACK or inbox subjects, or publish another node's results.

`bobctl setup-streams` provisions consumers from active registry grants.
Submission selects a node from fresh broker-scoped heartbeat subjects, ignoring
payload identity, ensures its consumer, then publishes to that private filter.
New workers wait for provisioning. Consumer errors fail visibly. Retries stay
on the assigned node; operators must reconcile execution before rerouting jobs
from failed nodes. Legacy submission uses `--node` (default nucbox).

## Join, rate limits and topology

Broker permissions bind each authenticated pending user to its own
`join.redeem.<user>` subject. Joind requires the body user to match that subject
and the reply to be in that user's private inbox. Size/type checks and the
200-character secret cap run before registry/bcrypt work. Joind's async lock
serializes redemption independently of registry flock. Codes are random,
single-use, expiring and stored as bcrypt hashes.

Joind resolves sources from local `/connz` monitoring with authenticated users
and pagination. Missing or multiple distinct sources are refused. Every code
and every source gets a 30-attempt rolling-minute bucket. User rejection
precedes source charging, so exhausted codes cannot drain another bucket.

Recommended: kernel-mode Tailscale in WSL on NucBox, with NATS bound to its
tailnet interface. Set the same `--host <tailnet-address>` on every hub command
and joind. Friends use Tailscale node sharing; Jeff's ACL allows only
`tcp:4222` on `nuc-wsl`. Monitoring remains loopback-only. This preserves each
remote client's tailnet address.

Userspace forwarding hides remote sources. Loopback is refused by default,
including IPv6 and mapped IPv4 loopback. Explicit `joind --allow-loopback`
adds a three-attempt per-code limit and six-attempt aggregate loopback limit
per minute, in addition to normal user/source limits. Two codes can each use
their allowance; multiple codes can exhaust the aggregate. This fallback
cannot provide remote-IP isolation. Source/rate refusals are uniform silent
drops (client timeout), never secret-bearing errors.

Monitoring accepts only the literal local IPv4 bind and bypasses HTTP proxies.
Before SIGHUP, reload checks the Linux procfs executable basename is
`nats-server` and verifies the absolute config argument. It then confirms a
stable server identity and changed config load time. A decoy executable with
matching config argv is never signalled. Unconfirmed reloads fail loudly;
revocations remain in the registry for retry.

## Credentials, migration and verification

Private owned directories, atomic writes and fsync protect registry/config
files. Rendering validates names, grants and bcrypt hashes. Legacy revocation
is sticky. Install commands pin a full commit and archive SHA256. Codes enter
through hidden prompts, never argv; PowerShell has no Code parameter.

Use a hard cutover with ingress paused: inventory/drain old work, stop old
clients and joind, invalidate pending codes and discard old installer commands.
Replace hub and client software together, remove shared consumers, provision
isolated consumers, re-join using new codes, then verify before reopening.
Reserved legacy names migrate to fresh mesh names; revoke old identities.
There is no shared `join.redeem` compatibility window. Rollback is a paused,
coordinated restore with job reconciliation, never a live return to broad
inbox/shared acknowledgement grants.

Tests use disposable Linux registries and real NATS servers. They check
routing, guest account isolation, private inboxes, owner ack, peer
ACK/NAK/TERM/WPI denial with pending-state preservation, legacy isolation,
source/user limits, serialization without flock, executable decoys, monitor
restrictions, installer pins and confirmed revocation. Mutations remove each
security guard independently; syntax/import failures do not count as kills.
