# Private live metrics (draft)

**Not enabled; Karen gate before live.** The PR installs no service, modifies no
running broker configuration and changes no Pages refresh schedule. Research and
transport decision were committed before implementation in live-metrics-research.md.

`cluster.live_metrics.Publisher` uses a dedicated metrics connection and sends
`live-metrics/v1` aggregates every two seconds and on every lane start/finish,
including exceptional completion. A host runner shares one publisher across its
provider lanes and wraps execution in `async with publisher.lane(provider)`.
Grok Bot is not an allowed provider. The existing worker has an opt-in
`--live-metrics --metrics-provider <provider>` integration; its default is unchanged.
The single-provider worker reports other providers as unknown, not zero.
Only one aggregate owner per host should be configured; multiple independent
publishers for the same host would overwrite each other's snapshots.

The read-only sources are Codex session JSONL and gemini-spend.txt; unknown
measurements remain null. Weekly percentage requires a timestamped 10080-minute
rate_limits window, at most seven days old and not past its reported reset.
Tokens today are partial/unknown if either token source is absent. Queue depth uses remaining-ready-message metadata from the existing worker
consumer delivery, or a measured count supplied by the owning lane runner. An
empty/timed-out fetch stays unknown. It is a delivery-time observation, not a
new broker query: the metrics identity cannot query JetStream. No worker leases, prompts,
job IDs, paths, credentials, or tailnet addresses enter the schema. Machine values
come from OS counters; unavailable Mac memory/swap remain null, not guessed zero.
Grok Bot (conserve-only) is always explicitly unknown without a direct source.

## Proposed private wiring, after approval only

- Provision separate metrics-publisher and metrics-viewer logins using the permission
  functions in `cluster/lanes_perms.py`. These functions are proposals/test inputs;
  they are not installed into the live template. Never reuse a worker login.
- Keep `BOB_METRICS_PUBLISHER_USER` / `BOB_METRICS_PUBLISHER_PASSWORD` in private
  environment configuration for the opt-in worker. Broker URL remains private.
- The Mini bridge uses `BOB_METRICS_NATS_URL`, `BOB_METRICS_VIEWER_USER`, and
  `BOB_METRICS_VIEWER_PASSWORD`. Its entry point is `python -m cluster.metrics_server`.
  Default bind is loopback, port 8787. An explicit tailnet literal is accepted;
  all wildcard, public, DNS-name and ordinary LAN binds are refused. Configure
  tailnet ACLs before any future enablement. No public reverse proxy or Funnel.
- Open the exact bound address/port to preserve Host/Origin checks. The bridge
  serves its own page and JavaScript, with no cross-origin API or write endpoint.
  No credential is exposed to the browser. Use a private tunnel for loopback access.

One SSE connection per tab; maximum 32 clients and four queued samples per client.
Reconnect gets the latest per-host snapshot followed by push updates; there is no
history replay. The UI marks samples stale after six seconds and reports transport
disconnect separately. A display-only timer ages samples; it makes no requests.
Malformed/oversized/extra-field broker messages are rejected. Heartbeats keep idle
HTTP streams alive. Slow/disconnected clients are cleaned up. No LeaseService
reply subject is used. No `$KV` or lane writes occur in this path.

## Verification

`python -m unittest -v cluster.tests.test_live_metrics test_usage_collect` exercises
schema allowlisting, unknown totals, publisher cadence and lifecycle, SSE delivery,
reconnection cache, bounded queues and origin/bind rejection. Real EphemeralNats
checks prove publisher denies KV/lane/DLQ/inbox/JetStream/other subjects and all
subscriptions; viewer denies all publishes and non-metrics subscriptions. The
strict cluster suite rejects missing dependencies and skipped tests.
