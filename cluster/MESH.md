# Bob cluster mesh: any computer can join, LLM capacity is pooled by routing

Status: design + first working version (draft PR). Exact permissions, threat
model and the Codex security review: `cluster/AUTH-DESIGN.md`. Live NATS config is NOT changed
until Karen gates this and Jeff approves the rollout.

## Recommendation (plain words)

1. **One hub, everyone dials in over Tailscale.** NATS stays on NucBox WSL, bound
   to its kernel-mode Tailscale interface, reached only through the tailnet. No public port, no TLS endpoint
   on the internet, no Funnel. Jeff's own machines are ordinary NATS clients.
   Leafnodes are only worth it later for a friend's *site* with several machines
   (their leaf dials out to us, bound to the GUEST account).
2. **Accounts split trust.** `BOB` = Jeff's machines (repo jobs, LLM jobs).
   `GUEST` = friends' machines, which only ever see sandboxed jobs. `JOIN` =
   one-shot redemption of join codes. Accounts are hard subject-space walls in
   NATS; a guest cannot even name a BOB subject.
3. **"Pooling" = routing, not sharing.** Each machine advertises which CLIs it is
   signed into (codex, claude, cursor-agent, gemini, minimax), a quota hint and
   how many lanes it will take. Jobs ask for a capability (`llm.codex.high`,
   `llm.composer`, `llm.claude`, `shell.build`, `sandbox.test`) and only nodes
   that have it, and are allowed it by the hub, pull it. Credentials never leave
   the machine they were signed in on.
4. **Subscriptions stay on their owner's machines.** OpenAI: an account is for
   the individual, multiple devices are fine, credential sharing is not.
   Anthropic: Pro/Max OAuth is only for the unmodified Claude Code / claude.ai,
   never routed for other people. Same idea for Cursor/Gemini/MiniMax keys. So:
   Jeff's LLM jobs run only on Jeff's machines with Jeff's own logins; friends
   contribute CPU for sandboxed build/test jobs, never their LLM logins for
   Jeff's work and never Jeff's logins for theirs. Guest permissions make this
   structural, not a promise.

## Topology

```text
            Tailscale (WireGuard)               no public port
 Mac mini ──┐                                   ┌── friend laptop (shared node,
 MacBook  ──┼──► NucBox nats-server :4222 ◄─────┘   GUEST account, sandbox only)
 new box  ──┘    (kernel Tailscale interface; remote sources preserved)
                 accounts: BOB | GUEST | JOIN | SYS
```

## Subjects

| Account | Subject | Who | Purpose |
|---|---|---|---|
| BOB | `jobs.n.<node>.<class>.<id>` | operator publishes | isolated legacy class queue |
| BOB | `cap.<cap_token>.n.<node>.<id>` | operator publishes | capability-routed jobs, stream `CAPJOBS` |
| BOB | `caps.<node>` | each member | capability heartbeat (node info + LLM caps) |
| BOB | `results.<node>.<id>` | members | results, scoped to the publishing node (stream `RESULTS`) |
| GUEST | `sandbox.jobs.<node>.<id>` | hub's `guestops` user (lives in GUEST) | stream `SANDBOX`, consumer `guests_n_<node>` |
| GUEST | `sandbox.results.<node>.<id>` | guests | results, exported to BOB |
| GUEST | `sandbox.caps.<node>` | guests | heartbeat, exported to BOB |
| JOIN | `join.redeem.<user>` | pending join users | redeem a join code (served by `joind`) |

`cap_token` is the capability with dots replaced by `_` (`llm.codex.high` →
`llm_codex_high`) so each node/capability pair gets a non-overlapping work-queue
consumer `cap_<token>_n_<node>` filtered on `cap.<token>.n.<node>.*`.
Guests use `guests_n_<node>` filtered on `sandbox.jobs.<node>.*`.
Each node can publish acknowledgements only to its own consumer. Shared
consumers cannot enforce delivery ownership and are no longer authorized.

## Routing is enforced by the hub, not by the worker

A node's NATS user only has permission for
`$JS.API.CONSUMER.MSG.NEXT.CAPJOBS.cap_<token>_n_<node>` for the capabilities the hub
granted it (`granted_caps` in the registry). A node that lies in its heartbeat
still cannot pull a job it was not granted. The worker additionally only pulls
capabilities that are both granted and detected locally (logged in), so a
logged-out Codex never takes a Codex job.

Cost tiers (Jeff's routing): `llm.codex.high` for hard logic, security and
Karen-fail fixes; `llm.composer` / `llm.minimax` / `llm.gemini` for routine
work; `llm.claude` when a node is signed in to Claude Code. `cluster/routing.py`
`pick_capability(kind)` encodes this.

## Join flow (one command)

On the NucBox: `python3 -m cluster.bobctl mint --name macbook-mesh --role member`
prints pinned install lines and a separate single-use join code (expires in 30 min).
Enter the code only at the hidden prompt; never put it in argv or shell history.
Whoever redeems it first gets the node slot.

On the new machine, paste the line (macOS/Linux bash, or Windows PowerShell which
installs into WSL). The installer: installs Python deps + worker into
`~/.local/share/bob-cluster`, connects to the hub as the code's pending user in
the `JOIN` account, sends `join.redeem.<user>`, receives its own permanent per-node
token (the join code is then dead), writes `~/.config/bob-cluster/node.env`
(0600) and registers a service (launchd / systemd --user / WSL + schtasks).

Revoke: `python3 -m cluster.bobctl revoke <name>`; the user is removed from the
rendered config and nats-server is reloaded, which drops its connection.

Tokens are stored on the hub only as bcrypt hashes (nats-server accepts bcrypt
passwords natively).

## Outside members

* **Jeff's own machines outside the house**: just log them into Jeff's tailnet.
  Same as inside.
* **Friends**: Tailscale *node sharing* of the NucBox to the friend's Tailscale
  account (Machines → NucBox → Share). Shared nodes are quarantined, strip tags,
  and obey the owning tailnet policy. Restrict to the NATS port only:

```json
{
  "tagOwners": {"tag:bob-hub": ["autogroup:admin"], "tag:bob-node": ["autogroup:admin"]},
  "grants": [
    {"src": ["autogroup:member"], "dst": ["*"], "ip": ["*"]},
    {"src": ["autogroup:shared"], "dst": ["tag:bob-hub"], "ip": ["tcp:4222"]}
  ]
}
```

  The friend then gets a `--role guest` join code. Guests land in `GUEST`, see
  only `sandbox.*`, and run jobs inside Podman with no network, read-only
  root, dropped capabilities and memory/CPU/pid caps. No repo secrets, no keys,
  no host fallback: a guest without Podman refuses sandbox jobs.
* Not recommended: a public TLS endpoint (needs a public port, cert rotation,
  internet-facing auth surface) and Tailscale Funnel (public internet).

## What is out of scope for v1

* Leafnodes (only needed for multi-machine friend sites).
* Workers pushing branches from LLM jobs (v1 runs the local CLI in a fresh
  checkout and returns the sanitized tail; pushing stays with the existing lane
  scripts).
* Changing the live `nats.conf` (rollout after Karen gates).

## Source limits and rollout

Use kernel-mode Tailscale in WSL on NucBox. Set the same
`--host <tailnet-address>` on all hub commands and joind so config regeneration
keeps NATS on that interface. Keep monitoring loopback-only. Jeff's friends use
Tailscale node sharing and an ACL granting only `tcp:4222` on `nuc-wsl`.

Joind resolves the authenticated pending user's address from `/connz`, never
from headers or the request body. Each code and each source has a 30-attempt
rolling-minute bucket. Missing or ambiguous source information is refused.
Loopback sources are refused by default. With userspace Tailscale forwarding,
explicit `joind --allow-loopback` permits only three attempts per code and six
aggregate loopback attempts per minute. Exhausted code buckets are checked
first, so they cannot keep consuming the shared bucket. This fallback cannot
recover remote IPs; several codes can still exhaust its small aggregate limit.

The hub selects an available node from fresh broker-scoped heartbeats and
publishes to its private filter, creating the consumer if necessary. Workers
pull only capabilities they are granted and detect locally. Guests execute
sandbox jobs with Podman; Docker-only hosts do not advertise sandbox capacity.
No provider keys are sent to guests. Offline assigned jobs are retained for
that node; an operator must reconcile execution before rerouting them.

Use a hard cutover: pause enqueue/join, drain or record pending old deliveries,
stop workers and joind, invalidate outstanding codes, replace hub/clients and
permissions together, remove old shared consumers, and provision per-node
consumers. Re-join existing members under fresh codes; use new names for
reserved legacy identities, then revoke the old identities. Old pinned scripts
and previously printed bobctl commands must be discarded and regenerated.
There is no dual-subject `join.redeem` window. Verify owner ack and peer denial,
private inboxes, real source addresses, joins and revocation before reopening.
Rollback keeps ingress paused: restore a consistent saved queue/config/code
set and reconcile jobs; never restore shared-consumer access while peers run.
