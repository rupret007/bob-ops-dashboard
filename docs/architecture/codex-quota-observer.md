# Actual Codex plan quota observations

The dedicated quota observer refreshes actual plan limits without creating a thread,
starting a turn, running a review, or consuming a reset. The dashboard, budget tracker
and admission reader remain bounded local-file readers. No token count or dollar
estimate substitutes for a quota percentage.

The observer uses the documented [OpenAI App Server protocol](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt):
initialize the stdio connection, acknowledge initialization, then fetch account limits.
The only client methods permitted by this component are `initialize`, `initialized`
and `account/rateLimits/read`. Existing CLI authentication stays under the CLI; this
component never inspects, copies or changes credentials or answers a token request.

Both normal platform installers register an independent five-minute native observer:
`com.storypool.codex-quota` on macOS and `storypool-codex-quota.timer` on Linux. The
Linux service has a 30-second supervisor deadline and owns its whole process group.
The CLI exchange itself has a 20-second deadline, output/message bounds and owned-group
cleanup, including helpers that outlive the app-server parent. Raw stdout/stderr and
server error messages are never logged. Registration belongs to normal self-update;
this document does not authorize bypassing installation or rollout checks.

The sanitized cache is `~/.local/share/storypool/quota/codex.json`, with private
permissions, atomic replacement and a nonblocking observer lock. It stores only a
schema/source identifier, conservative request-start timestamp and typed quota fields.
Account IDs, credits, upsells, tokens, session text and arbitrary server data are
excluded. Malformed authoritative replies replace older success with an unknown
observation. A failed transport does not renew the earlier successful observation's
timestamp. Missing or expired observations cannot authorize new work.

The reader selects the freshest authoritative observation across actual session
events and this cache. It requires the `codex` bucket and a 10080-minute window in
either primary or secondary, finite usage in 0–100, a valid future reset and a nonfuture
observation timestamp. A newer session at 75% cannot be hidden by an older cache at a
lower percentage. Conflicting observations and unsafe/malformed cache files fail
closed. The existing one-hour freshness limit and hard 75% stop remain in force;
a configured higher cap cannot raise that stop. A server-classified reached limit or
explicitly denied ordinary usage additionally blocks admission.

Observations are specific to the host whose installed CLI fetched them. Do not assume
authentication accounts or quotas are interchangeable between Mini and NucBox. The
fixed installed `~/.local/bin/storypool-codex-quota-read` boundary reads only that
execution host's local cache/session observations, returning sanitized quota plus its
own `served_at` clock and an explicit Codex bucket identifier. It never starts a CLI
or contacts a provider. A nonce binds the response to the current bounded SSH read;
freshness includes source-clock age and the whole transport interval, so clock skew
cannot rejuvenate stale evidence. No account identifier crosses this boundary.

Karen resolves its actual SSH/local target before reading that target's quota. A
low Mini balance cannot authorize Nuc work, and a high Mini balance cannot suppress
an eligible Nuc reviewer. If SSH preflight fails, a local fallback requires a fresh
local quota check immediately before review. Unknown, stale or capped remote quota
does not trigger an account switch; it declines that Codex invocation and allows the
normal independent reviewer chain to continue. An SSH execution failure is reported
without automatically replaying uncertain model work locally. Availability checks
never authorize a later fallback on a different host.

Offline protocol fixtures cover exact handshake/method ordering, malformed and
conflicting buckets, primary/secondary weekly windows, private-data exclusion,
response IDs, refused server requests, time/output bounds, process-group cleanup,
cache privacy, duplicate observers, freshness selection and the 75% cutoff. These
fixtures prove the implementation's boundaries; production refresh and worker
outcomes must still be observed after rollout.
