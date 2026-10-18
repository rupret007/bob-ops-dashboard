# Live metrics transport decision (2026-10-17)

Research first, before implementation. Gemini CLI and configured Gemini API were
not available in this environment; this comparison uses official documentation.
Status: **not enabled; Karen gate before live**. No deployment is part of this PR.

| Option | Push behavior | Browser/security/operational tradeoff |
| --- | --- | --- |
| JetStream KV watch | Snapshot followed by change notifications through a consumer | Useful for persistent state, but involves JetStream consumer/API permissions and KV subjects. Unnecessary storage and permission surface for disposable 1–2 second measurements. |
| NATS over WebSocket (`nats.ws`) | Direct subscriptions, no polling | Requires a WebSocket listener, origin policy and browser-held read-only NATS credentials. Supports reconnect; exposes the broker transport to the browser. |
| Server-Sent Events | One-way HTTP stream using native `EventSource` | Automatic reconnect, simple text frames and heartbeat comments. A local bridge holds the subscribe-only credential. HTTP/1 browsers limit concurrent connections; use one stream per tab. |
| Plain WebSocket | Bidirectional browser connection | Requires bridge protocol, reconnect and queue management; bidirectionality brings no benefit for a read-only metrics view. Browser WebSocket lacks automatic backpressure. |

Decision: workers publish allowlisted aggregates to `metrics.<host>` on a two-second
cadence and immediately on lane start/finish. A separate small Mini service
subscribes to `metrics.*` using a **viewer-only identity**, validates every payload,
and serves a same-origin SSE page. No file polling between publisher and browser.
Core NATS is intentionally ephemeral: reconnect delivers the latest cached sample
and then fresh samples, not history. Bounded per-browser queues discard old samples;
timestamps and stale/disconnected indicators prevent presenting old data as live.

Bind only loopback or an explicitly configured Tailscale address; reject wildcard,
public and ordinary LAN addresses. Validate Host/Origin, expose no CORS access or
write endpoints. Tailnet access policy remains an operator prerequisite for any
future enablement. Credentials stay in private environment configuration and never
enter payloads, URLs, browser JavaScript, logs or committed files. Host labels are
`mini`/`nucbox`, never addresses. Publisher and viewer permissions are separate from
worker permissions and from LeaseService; no lease reply paths or request/reply.
Publish identity allows ONLY `metrics.*`, with explicit denies for `$KV.>`,
`lanes.>`, `lanes_dlq.>`, `_INBOX.>`, `$JS.>`. Viewer may only subscribe `metrics.*`
and cannot publish anything. Real ephemeral nats-server tests must prove this.

Measurements are numeric aggregates or null. No prompts, job names, paths,
credentials or raw session records. Codex weekly usage needs a real rate_limits
observation, otherwise null. Grok Bot (conserve-only) remains `unknown` without a
direct source, and lanes never consume it. Missing total_tokens is unknown/partial,
not zero. GitHub Pages keeps its existing 15-minute refresh and receives no live
endpoint or per-sample commits. This draft adds no service activation/config install.

## Sources

- [NATS KV and watch](https://docs.nats.io/nats-concepts/jetstream/key-value-store): KV uses JetStream streams/consumers and `$KV` subjects.
- [NATS WebSocket transport](https://docs.nats.io/running-a-nats-service/configuration/websocket): additional listener and browser connectivity.
- [Official nats.ws README](https://github.com/nats-io/nats.ws/blob/main/README.md): browser client, subscriptions and authentication.
- [MDN SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events): event framing, reconnect, comments and connection limits.
- [MDN WebSocket](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket): bidirectional connection and lack of backpressure.
