# Bob cluster auth design: status

The review below was written by Codex (high reasoning) on the Mac mini against
the first cut of `registry.py`/`join.py`. Its line numbers refer to that first
cut. This table says what the PR did with each finding.

| # | Finding | Status in this PR |
|---|---|---|
| 1 | env/URL injection via join code or hub reply | **Fixed.** `validate_hub_url`, strict pending-user/secret/expiry checks in `decode_join_code`; `join.validate_reply` checks user/role/account/caps/token before anything is written; `render_node_env` re-validates. Tests: `JoinClientTests`, `test_code_roundtrip_and_rejects_garbage`. |
| 2 | legacy mini/macbook subscribe `_INBOX.>` | **Open by design until migration.** Legacy users keep their exact current permissions so the running `worker.py` keeps working. New members, guests, pending users and guestops all get private `_INBOX_<user>` prefixes. Rollout step: re-join Mini/MacBook as members, then `bobctl revoke mini` / `macbook`. |
| 3 | revoke ignores legacy identities | **Fixed.** Revoke deletes and tombstones legacy users; `import_legacy` never resurrects them. Test: `test_legacy_revoke_is_sticky`, integration test drops a live `mini` connection. |
| 4 | JSON changes not deployed / expired codes still rendered | **Mostly fixed.** `Hub.apply()` renders + reloads; `joind` re-renders before replying and reloads after; a sweep removes expired codes every 60 s; `render_conf` skips expired codes even before the sweep. **Open:** reload success is not verified (SIGHUP is fire-and-forget). |
| 5 | members keep legacy `JOBS.workers` regardless of caps | **Accepted, documented.** JOBS carries only allowlisted shell test/build/render argv; LLM work goes only to CAPJOBS (`bobctl submit` never publishes LLM jobs to JOBS). |
| 6 | results/ack authority | **Partly fixed.** Members can publish only `results.<self>.>`. Ack ownership on a shared consumer cannot be enforced by static ACLs: accepted (members are Jeff's machines; guests share one sandbox queue). |
| 7 | guestops has `$JS.API.>` | **Fixed.** Narrowed to create/update/info of the two GUEST streams and the `guests` consumer, plus `sandbox.jobs.*`. Test: delete/purge/msg-get denied. |
| 8 | joind request controls | **Fixed (basic).** Size cap, type checks, reply must be the requester's private inbox (checked before the code is consumed), serialized bcrypt work, 1 s failure delay, 30 attempts/min global cap. **Open:** per-source limits. |
| 9 | service password race | **Fixed.** One lock covers env file + hash; only `FileNotFoundError` is swallowed. |
| 10 | filesystem hardening | **Mostly fixed.** Private 0700 owned config dir, lock opened `O_NOFOLLOW` 0600, fsync of file and dir on every atomic write. |
| 11 | renderer trusts registry | **Fixed.** Only bcrypt hashes (format + cost 4..14) and safe names render; grants re-validated per role; fails closed. |
| 12 | guest grants ignored / member sandbox / default caps | **Fixed:** guest SANDBOX permissions require `sandbox.test`; members can no longer be granted sandbox caps. **Choice kept:** a member minted without `--caps` is granted all member caps, because the worker only pulls caps it is also signed in to locally. Use `--caps` to narrow. |
| 13 | error output / code in argv | **Fixed.** Fixed failure categories; installers pass the code via `BOB_JOIN_CODE`, never argv. |
| 14 | missing SYS | **Fixed.** `SYS` account + `system_account: SYS`. |
| 15 | 32-bit pending user names | **Fixed.** 128-bit, collision retry under the lock. |

Mutation check (each guard removed on its own, tests must fail): 16/16 killed
(all-caps grant, guest publish/gate, guestops admin, revoke skip/hash, legacy
revoke/tombstone, pending expiry, bcrypt check, member results scope, code
replay, URL check, joind reply check, join reply validation, member sandbox).

---

# Bob cluster authentication and permissions

Review baseline: `registry.py` and `join.py`; nats-server 2.15, config-mode
username/bcrypt authentication, no operator/JWT. Code is unchanged.
The flat template is the existing deployment. Findings below are outstanding fixes.

## Accounts and traffic

Application topology from `registry.py:390`. `$BOB_USERS`, `$GUEST_USERS`,
`$JOIN_USERS` denote arrays of `{user: "<name>", password: "<bcrypt>",
permissions: {...}}` expanded from the table, not supplied runnable credentials.

```hcl
accounts {
  BOB: { jetstream: enabled
    users: $BOB_USERS
    imports: [
      { stream: { account: GUEST, subject: "sandbox.caps.>" } }
      { stream: { account: GUEST, subject: "sandbox.results.>" } }
    ]
  }
  GUEST: { jetstream: enabled
    users: $GUEST_USERS
    exports: [
      { stream: "sandbox.caps.>", accounts: [BOB] }
      { stream: "sandbox.results.>", accounts: [BOB] }
    ]
  }
  JOIN: { users: $JOIN_USERS }
}
```

BOB contains legacy users and active members; GUEST has active guests and guestops;
JOIN has pending users and joind. Revoked nodes are omitted. JOIN has no JetStream. There are no application service imports,
BOB exports, GUEST imports, or JOIN imports/exports. Only the two GUEST streams
cross accounts, with unchanged subjects. Stream imports forward live traffic;
they do not grant access to GUEST JetStream APIs or historical replay.

**SYS is missing.** At `registry.py:390`, add `SYS: { users: [] }` inside accounts and `system_account: SYS` at top level.
SYS has no JetStream, external users, or application imports/exports. Keep nucbox in BOB. Server-internal system/JetStream plumbing is separate from the
two application exports; it does not authorize BOB clients to access GUEST APIs.

The hub uses a second connection authenticated as **guestops in GUEST** to
`js.publish("sandbox.jobs.<id>", payload)`. GUEST's SANDBOX captures that direct
publish and sends its PubAck to that connection's reply inbox in GUEST.
There is no service-import capture/reply mapping. A BOB publish cannot enqueue in GUEST (and nucbox's ACL denies that subject). `allow_responses` is unnecessary for receiving a PubAck.

Provision before workers start: BOB JOBS (`jobs.>`, WorkQueue, `workers`),
RESULTS (`results.>`, Limits), CAPJOBS (`cap.>`, WorkQueue); one explicit-ack pull
consumer per non-sandbox capability, named `cap_<token>`, filter `cap.<token>.*`.
GUEST SANDBOX (`sandbox.jobs.*`, WorkQueue, explicit-ack pull consumer `guests`,
filter `sandbox.jobs.*`) and SANDBOX_RESULTS (`sandbox.results.>`, Limits).
No push delivery subjects, domains, mirrors, sources, republish, or rollup headers.
`setup_streams.py` provisions only JOBS/workers and RESULTS; mesh provisioning is missing.

## Exact permissions currently rendered

P = publish allow, S = subscribe allow; expand unions literally. I(u) = `_INBOX_<u>.>`.
T(s,c) expands to these **three publish subjects**:

```text
$JS.API.CONSUMER.INFO.<s>.<c>
$JS.API.CONSUMER.MSG.NEXT.<s>.<c>
$JS.ACK.<s>.<c>.>
```

All roles omit/disable `allow_responses` except joind. Empty P renders as
`publish: { deny: [">"] }`, not `publish: []`; nonempty lists are restrictive allows.

| User / account | P | S |
|---|---|---|
| nucbox / BOB | `metrics.nucbox`, `results.>`, `jobs.>`, `cap.>`, `$JS.API.>`, `$JS.ACK.JOBS.workers.>`, `$JS.ACK.CAPJOBS.>` | `$JS.API.>`, `$JS.ACK.>`, `$JS.FC.>`, `_INBOX.>`, `results.>`, `caps.>`, `sandbox.caps.>`, `sandbox.results.>` |
| member u / BOB | `caps.<u>`, `metrics.<u>`, `results.>`, T(JOBS,workers), union of T(CAPJOBS,cap_&lt;token&gt;) for each granted non-sandbox cap | I(u) |
| legacy mini or macbook / BOB | `metrics.<u>`, `results.>`, T(JOBS,workers) | `$JS.API.>`, `$JS.ACK.>`, `$JS.FC.>`, `_INBOX.>` |
| dashboard / BOB | none | `metrics.>`, `results.>`, `caps.>`, `sandbox.caps.>`, `sandbox.results.>` |
| guest u / GUEST | `sandbox.caps.<u>`, `sandbox.results.<u>.>`, T(SANDBOX,guests) | I(u) |
| guestops / GUEST | `sandbox.jobs.>`, `$JS.API.>` | `_INBOX.>`, `sandbox.results.>`, `sandbox.caps.>` |
| pending join-u / JOIN | `join.redeem` | I(join-u) |
| joind / JOIN | none statically; `allow_responses: { max: 1, expires: "30s" }` | `join.redeem` |

`<token>` replaces dots with underscores: `llm.codex.high` -> `llm_codex_high`;
consumer `cap_llm_codex_high`; no wildcard consumer grant. Legacy workers lack `caps.<u>`. Dashboard's sandbox access means the
two namespaces above: `sandbox.*` would match neither nested results nor caps.
Nucbox and guestops have full JetStream administration in their own accounts. Nucbox cannot administer GUEST through the stream imports.

With nats-py 2.16, pass the stream and durable explicitly; `pull_subscribe` checks
CONSUMER.INFO and binds an existing consumer. A missing consumer must fail closed:
its create attempt is unauthorized. `pull_subscribe_bind` can bind directly,
without the INFO round trip. `fetch` publishes CONSUMER.MSG.NEXT with an inbox reply.
Configure **both** the connection's `inbox_prefix=b"_INBOX_<u>"` and the pull
subscription's `inbox_prefix=b"_INBOX_<u>."`; the latter controls delivery inboxes.
Use synchronous `js.publish` for worker results; verify any asynchronous publish
helper separately for its inbox prefix. Existing worker.py uses default inboxes.

`ack`, `nak` (including delayed), `in_progress`, `term`, and `ack_sync` publish
to the delivered message's `$JS.ACK.<stream>.<consumer>.…` reply subject, using
`+ACK`, `-NAK`, `+WPI`, `+TERM` payloads respectively; ack_sync also receives a reply.
Workers need no `$JS.API.STREAM.>` (NAMES/INFO/MSG.GET included),
`$JS.API.DIRECT.GET.>`, CONSUMER.CREATE/DURABLE.CREATE/DELETE or consumer listing,
`$JS.FC.>`, or subscriptions to `$JS.API.>`/`$JS.ACK.>` or original job subjects.
They subscribe only to their inbox; job deliveries retain the original subject.
Joind's deny-all static P plus dynamic responses **works** in 2.15: a received
request authorizes one publication on its reply subject for 30 seconds, even
when static P denies it. This is not proof that the reply belongs to the caller.
See [2.15 response checks](https://github.com/nats-io/nats-server/blob/v2.15.0/server/client.go#L3959)
and [nats-py inbox support](https://github.com/nats-io/nats.py/releases/tag/v2.13.0).

## Findings and exact fixes (severity order; all outstanding)

1. **High — credential/env injection:** `join.py:37` interpolates untrusted URL
   and reply fields into an env file; `registry.py:88` accepts arbitrary suffixes
   after `nats://`. Newlines can inject variables; shell syntax executes if sourced.
   Fix decode/render boundaries: require strings, strict node/pending names,
   URL-safe generated token syntax, catalog-valid caps and role, positive bounded
   lanes; parse URL and reject controls, userinfo, path/query/fragment and bad ports.
   Serialize values with shell-safe quoting for a documented parser; reject CR/LF.
   At `join.py:63`, require a dict, `ok is True`, matching requested user/role and
   matching account; validate everything before writing. Never trust a code's
   endpoint without an operator-trusted delivery channel; base64 is not a signature.
2. **High — legacy inbox/control snooping:** `registry.py:322`, `:325`, `:329`
   authorize broad inbox/API/ACK/FC subscriptions. Mini/macbook can read operator
   replies, including jobs for ungranted caps, by subscribing `_INBOX.>`.
   Replace those four S entries with I(user), retaining nucbox's business S entries;
   change guestops's `_INBOX.>` at `:368` to I(guestops). Coordinate all clients'
   connection AND pull prefixes first; otherwise existing worker.py stops fetching.
3. **High — revoke does not revoke legacy identities:** `registry.py:221` only
   handles nodes/pending; `:354` continues emitting mini/macbook/nucbox/dashboard.
   Delete/tombstone `legacy[name]` under the same lock, return true, and prevent
   `import_legacy` (`:169`) from resurrecting tombstoned credentials implicitly.
4. **High — authentication state is not deployed:** `registry.py:175`, `:206`,
   `:221`, `:235`, `:246` only mutate JSON; `:372` renders expired pending entries.
   No joind/reload orchestrator exists here. Filter expiry during render; implement
   a serialized state-to-config publisher and expiry timer.
   Validate config, atomically install, reload, verify success; report failure,
   never claim revoke/set_caps took effect from a successful JSON write alone.
   Mint must install pending auth before returning a usable code. Redemption must
   flush its single token reply before removing the pending connection on reload;
   define recovery for lost replies (revoke/re-mint), never resurrect a used code.
5. **High — capability boundary has a legacy bypass:** `registry.py:297` gives
   every member JOBS.workers regardless of caps. Keeping this compatibility grant
   means JOBS must never carry capability-restricted/LLM work. Enforce that in the
   trusted enqueue path; use CAPJOBS exclusively for such work. If legacy jobs
   require per-cap authorization, remove this grant and migrate those jobs first.
6. **Medium — result/ack authority exceeds assignment:** `registry.py:296`,
   `:320`, `:323` permit forging any BOB result; `:298`, `:305`, `:314` permit
   acknowledging any known message in the shared consumer. Fix result subjects to
   `results.<self>.<id>` and update publishers; validate assignment at the collector.
   For ack ownership use a trusted pull/ack broker; static shared-consumer
   ACLs cannot enforce it. GUEST's shared `guests` queue likewise is not per-guest
   job isolation. Accept that explicitly or replace it with isolated queues.
7. **Medium — guestops is an unnecessary administrator:** `registry.py:367`:
   runtime P should be only `sandbox.jobs.*`, S only I(guestops) plus the two
   current observation subjects. Provision using a separate hub-only setup user
   and remove its credentials from runtime. `$JS.API.>` currently allows arbitrary
   GUEST streams/consumers, deletion and stored-result reads. Nucbox's API-wide
   grant is intentional operator authority; its API/ACK/FC subscriptions are not.
8. **Medium — missing redemption request controls:** `registry.py:206` and
   joind's ACL at `:370` provide no rate limit, payload/type bounds, or reply
   validation. The future handler must bound JSON, authenticate user/name/secret
   through redeem, and accept only a literal reply under `_INBOX_<user>.` before
   consuming a code. Never reply to `join.redeem`, another user's inbox, or a
   wildcard. Bound concurrent bcrypt work and per-source/user/global attempt rates;
   connection-auth flooding also needs ingress limits. Do not trust payload user
   alone or client-supplied headers as authenticated identity.
9. **Medium — service credential race/data loss:** `registry.py:139` reads/writes
   service env outside the registry lock; concurrent initializers can overwrite
   each other's passwords. At `:266`, all read errors silently become empty data.
   Hold one lock over env read/create and hash reconciliation; catch only
   FileNotFoundError, fail on other errors, and reconcile interrupted two-file writes.
10. **Medium — filesystem promises incomplete:** `registry.py:114`, `:273`
    leave existing directories' mode/owner unchecked; `:115` follows lock symlinks.
    Require a private owned directory (0700), reject symlink/untrusted parents,
    open lock with O_NOFOLLOW and 0600, and verify ownership. `_atomic_write` already
    writes 0600 and replaces atomically; fsync file before replace and parent after
    replace (`:279`) to avoid crash rollback of consumed codes or revocations.
    Apply the same checks to `join.py:98`; protect config, env, registry and backups.
11. **Medium — renderer does not enforce bcrypt/schema:** `registry.py:336`
    accepts plaintext passwords despite its docstring; `:357` trusts registry
    names/caps. Validate full bcrypt hash format/cost, unique safe names, roles and
    grants before rendering; fail closed on malformed records. Validate request
    types before dict lookup in `:210`, so malformed users cannot crash a handler.
12. **Medium — guest grants ignored:** `registry.py:363` always grants SANDBOX
    pulls, even after `set_caps(name, [])`. Gate T(SANDBOX,guests) on sandbox.test.
    At `:300`, members' sandbox grants are silently skipped: reject that unsupported
    member grant or design an explicit separate GUEST identity; do not imply it works.
    At `registry.py:184`, caps=None grants every member capability, including all
    LLM providers. Require explicit member grants or default to an empty list.
13. **Medium — credential-bearing error output:** `join.py:96` prints arbitrary
    exception strings (including remote errors/URLs). Emit a fixed failure category
    without server text or credentials. `:76` puts the code in argv/history: add
    stdin/hidden-input support and make that the recommended invocation.
14. **Low — missing SYS:** implement the exact account delta above at
    `registry.py:390`; do not mistake the automatic system account for explicit SYS.
15. **Low — pending username collisions:** `registry.py:191` uses only 32 random
    bits and `:201` overwrites on collision. Generate >=128 bits and retry under
    the lock until absent from pending and all other user namespaces.

## Threat classification and operating boundary

| Threat | Classification and limit |
|---|---|
| Lying heartbeat | **Enforced by server:** own heartbeat subject and CAPJOBS consumer grants. **Worker convention:** truthful caps/login/quota, local detection; legacy bypass is finding 5. |
| Guest pulling BOB jobs | **Enforced by server:** account isolation, no job/API imports, only SANDBOX.guests API grants. A payload called an LLM job is not detected by ACLs. |
| Guest reading another guest's results | **Enforced by server:** no result subscriptions, foreign inbox subscriptions or result-stream APIs. Own result P only. Shared sandbox jobs remain visible to any guest that pulls them. |
| Used join-code replay | **Worker convention (hub application):** flock + delete pending entry makes redeem single-use. **Enforced by server** only after config removal/reload; expiry alone is not config-user expiry. |
| Join brute force | **Enforced by server:** bcrypt connection authentication. **Worker convention (hub):** 192-bit random join secrets, 256-bit node tokens, TTL and uniform redeem error; rate limiting is missing. |
| Revoked node retains connection | **Enforced by server after successful reload:** removed user is disconnected and reconnect rejected. **Worker convention (hub):** deploy removal; JSON mutation alone does nothing. |
| Inbox sniffing | **Enforced by server** for generated member/guest/pending I(u), including concurrent joiners; legacy users remain overbroad (finding 2). **Worker convention (joind):** validate reply destination. |
| metrics.other spoofing | **Enforced by server:** direct PUB limited to metrics.self; guests cannot publish metrics. **Worker convention:** payload identity/truth; collector must use subject identity. |
| Member publishes jobs | **Enforced by server:** direct jobs/cap/SANDBOX job PUB and administration denied; receiving jobs via authorized pulls is allowed. |
| bcrypt / permissions | **Enforced by server:** bcrypt verification, not plaintext-on-wire encryption. **Worker convention (host/OS):** private 0700 dirs, 0600 files, trusted tailnet transport, no credential logs. |

Reload behavior: [2.15 authorization reload](https://github.com/nats-io/nats-server/blob/v2.15.0/server/reload.go#L2224).
Subscription-funded LLM use is restricted to the owner's own machines/logins for
the owner's work. Guests never receive llm.* grants; validate_grant rejects them.
Owner identity, job content and container isolation are **worker/operator conventions**. Never send LLM/repo-secret payloads
to SANDBOX or capability-restricted work to JOBS. This is a deployment boundary,
not a claim of blanket permission under every provider's current terms.

## Integration acceptance tests (ephemeral server only)

1. Render synthetic hashes/users; `nats-server -t -c`; start 2.15 with isolated
   storage. Assert explicit SYS, no anonymous/plaintext-hash login, bcrypt login,
   and account assignments. Missing SYS currently fails the target assertion.
2. guestops direct publish -> SANDBOX PubAck + exactly one stored job; nucbox
   publish there denied. BOB observes both imported namespaces; GUEST cannot
   observe BOB jobs/results/caps. No reverse or third-namespace import exists.
3. For every role/table entry exercise positive PUB/SUB and representative negative
   subjects; collect async permission errors and verify no delivery/state change.
   Include dashboard P deny-all, foreign metrics/caps/results and foreign inboxes.
4. Pre-create consumers; member binds only granted cap plus JOBS.workers, guest
   only SANDBOX.guests. Fetch, ACK/ACK-sync, delayed NAK/redelivery, WPI, TERM and
   result PubAck work with private prefixes. All stream/direct-get/create/delete/
   list APIs and ungranted consumers fail; missing consumer is never auto-created.
5. Lie in caps payload; ungranted fetch still denied. Show legacy jobs remain
   pullable. Demonstrate current legacy inbox leak and empty-guest-grant defect;
   after fixes assert denial, including wildcard/queue subscriptions and reloaded grants.
6. Two concurrent joiners: each receives only its token; cross-inbox SUB fails.
   joind unsolicited PUB, second reply and >30s-late reply fail; first reply works.
   Malicious reply destinations fail handler validation before redeem consumes code.
7. Race same-code redemption across processes: exactly one success; wrong secret,
   expired and used code fail. Mint/redeem/expiry reloads install/remove auth;
   lost-reply recovery does not reuse the code. Exercise bounded bad-request floods.
8. Keep connections open through revoke/reload: node and legacy user disconnect,
   cannot reconnect; unrelated users work. Failed reload must not report success.
9. Reject env injection, guest llm.* grants, bad replies/hashes, unsafe dirs/symlinks;
   assert 0600/0700, no secrets in logs, concurrent service initialization stability,
   and crash/restart persistence. No live credentials needed for any test.

Validation: static review only; nats-server/nats-py/bcrypt unavailable; integration tests not run.
