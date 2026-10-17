# StoryPool security

StoryPool runs other people's work on Jeff's machines, and (soon) Jeff's work on friends' machines. This page explains what we're protecting, from whom, and exactly how. The short version:

- **The network is private.** The queue only listens on localhost; the only way in is Tailscale.
- **Every machine has its own credential**, and the NATS server decides what each one may publish and subscribe to.
- **A host can only touch its own work.** It can't write leases directly, ack someone else's job, or read someone else's replies.
- **Guests get a sandbox with no keys.** Friends' machines only ever run sandboxed, no-key jobs.
- **Secrets never ride in jobs**, and donor credentials never leave the donor's machine.

> **Where things stand.** The lanes permission model below was merged and Karen-passed in bob-ops-dashboard #71 as data plus tests (`cluster/lanes_perms.py`, `cluster/tests/test_lanes_perms.py`). It is proven against a real nats-server 2.15.1 in CI, **but it is not yet applied to the live hub**; the live hub still runs the stage-1 grants in `cluster/nats.conf.template`. Guest enrollment and the podman sandbox are in draft PR #68. Friend access and donations are **Planned**.

## 1. What we protect

| Asset | Why it matters |
|---|---|
| AI subscriptions and API keys (Codex, Cursor, Claude, MiniMax, Gemini) | Money and account standing. A leaked key can be drained or get an account banned. |
| GitHub write access | Lanes open and update PRs. Misuse could push bad code. |
| The machines themselves | Jeff's Mini, NucBox and MacBook, and friends' machines. |
| Lease and queue integrity | A stolen or dropped lease means two lanes fighting over one PR, or lost work. |
| Tailnet details | Tailnet IPs and host names are kept out of the repo and the public Pages site. |

## 2. Threat model

| # | Threat | Example | Defense |
|---|---|---|---|
| T1 | **Outsider on the internet** | Port scan finds the queue | Server binds `127.0.0.1` only. Tailscale userspace forwards tailnet traffic; nothing listens on a public interface. |
| T2 | **A member host misbehaves** (bug or compromise) | Mini tries to steal the NucBox's lease, or ack its job | Per-host NATS permissions: own lease subject, own consumer, own inbox. Raw `$KV` writes denied. |
| T3 | **Identity spoofing** | A host claims "I'm nucbox" in the request body | The lease service takes the host from the **NATS subject**, which the server only lets each host publish for itself. The body is never trusted for identity. |
| T4 | **Forged system events** | A host publishes a fake `MAX_DELIVERIES` advisory to get a job dead-lettered | Hosts have no publish grant on `$JS.EVENT.>`, so forged advisories are denied by the server. |
| T5 | **Lost work during dead-lettering** | Crash between copy and delete | The DLQ copies, **waits for the server's confirm**, then deletes. Idempotent `Nats-Msg-Id` makes a repeat copy harmless. |
| T6 | **Malicious job spec** | `cmd: ["bash","-c","curl … | sh"]` or a key pasted into argv | Exact argv allowlist per class, single allowlisted repo, strict ref format, env allowlist, secret scanning across argv (including keys split across items). |
| T7 | **A guest job tries to escape or steal keys** | Guest job reads `~/.config` or calls an API | Rootless podman sandbox with no keys and no host credentials mounted. Guests only get no-key jobs. |
| T8 | **A friend's machine is compromised** | Attacker uses a friend's tailnet node to probe | Tailscale ACL lets that node reach **only tcp:4222 on the queue**, and its NATS user only gets guest subjects. |
| T9 | **Reserved-name squatting** | A friend picks the host name `hub` or `conductor` | **In progress:** these names must be refused at enrollment before any friend joins. |
| T10 | **Runaway spend** | A loop burns API credit | Prepaid plans and prepaid credit only. Donor caps are enforced on the donor's machine. |
| T11 | **Bad change merged** | A PR weakens a permission | Karen gates every merge; CI fails on any skipped required security test; no force-push. |

## 3. Permission matrix

### 3.1 Lanes model (merged and Karen-passed in #71, not yet live)

Exact grants live in `cluster/lanes_perms.py`. `<host>` is one host's own name; it can't use anyone else's.

| Action | Operator (hub) | Conductor (fill loop) | Lane host `<host>` | Guest (friend) |
|---|:-:|:-:|:-:|:-:|
| Publish lane jobs `lanes.*.*.*` | ✅ | ✅ (enqueue) | ❌ explicit deny `lanes.>` | ❌ |
| Raw write/delete `$KV.LANE_LEASES.>` | ✅ (only identity that does) | ❌ | ❌ explicit deny `$KV.>` | ❌ |
| Stream admin `$JS.API.STREAM.>` | ✅ | ❌ (DLQ info/get only) | ❌ explicit deny | ❌ |
| Call lease service | serves `lanesvc.lease.*.*` | ❌ | ✅ only `lanesvc.lease.<host>.<op>` | ❌ |
| Pull jobs (`MSG.NEXT`) | ✅ | ❌ | ✅ only `lane_<provider>_<host>` | ❌ |
| Ack / nak / term (`$JS.ACK`) | ✅ | ❌ | ✅ only its own consumer | ❌ |
| Subscribe to `lanesvc.>` | ✅ (to serve) | ❌ | ❌ | ❌ |
| Subscribe to another host's inbox | ❌ | ❌ | ❌ only `_INBOX_<host>.>` | ❌ |
| `MAX_DELIVERIES` advisories | ✅ subscribe | ❌ | ❌ (cannot publish → forgeries denied) | ❌ |
| Lane work at all | — | — | — | ❌ never: lane jobs carry repo work and never go to guests |

What the tests prove on a real server (`cluster/tests/test_lanes_perms.py`): a host's raw `$KV.LANE_LEASES` put and `KV-Operation: DEL` are denied (also via JetStream publish and the KV stream's delete/purge APIs) and the real holder keeps its lease; the service refuses an owner from another host and a renew/release with another host's revision; another host's lease subject, `MSG.NEXT`, a forged `+TERM` ack, enqueue, and `_INBOX.>`, `_INBOX_mini.>` and `>` subscriptions are all denied; the host's own lease → pull → ack path still works.

### 3.2 Live today (stage 1, `cluster/nats.conf.template`)

| User | Publish | Subscribe |
|---|---|---|
| `nucbox` (operator) | `metrics.nucbox`, `results.>`, `jobs.>`, `$JS.API.>`, its acks | JetStream API replies, `results.>`, inbox |
| `mini`, `macbook` | `metrics.<self>`, `results.>`, `workers` consumer info / next / ack | JetStream replies, inbox |
| `dashboard` | nothing | `metrics.>`, `results.>` |

Members can't create or change streams or consumers. Draft PR #68 tightens this further (private inboxes, per-node `workers_<node>` consumers) before any friend joins.

## 4. Network: Tailscale only

- `nats-server` binds `127.0.0.1:4222`; monitoring binds `127.0.0.1:8232`.
- Tailscale runs in **userspace mode** inside WSL and forwards tailnet connections on 4222 to localhost.
- Jeff's machines are on Jeff's tailnet. **Friends** reach the queue through a **Tailscale share** of the hub node. (Planned)
- The ACL for shared-in friends allows exactly one thing: TCP 4222 on the queue. No SSH, no dashboard, no other ports, no other machines.

Illustrative ACL (HuJSON; group, tag and node names are placeholders, never real addresses):

```jsonc
{
  "groups": {
    "group:storypool-friends": ["friend-a@example.com", "friend-b@example.com"]
  },
  "tagOwners": {
    "tag:storypool-queue": ["autogroup:admin"]
  },
  "acls": [
    // Jeff's own devices: full access to his machines.
    { "action": "accept", "src": ["autogroup:member"], "dst": ["*:*"] },

    // Friends: the queue port and nothing else.
    { "action": "accept", "src": ["group:storypool-friends"], "dst": ["tag:storypool-queue:4222"] }
  ],
  "tests": [
    { "src": "friend-a@example.com", "accept": ["tag:storypool-queue:4222"], "deny": ["tag:storypool-queue:22", "tag:storypool-queue:8787"] }
  ]
}
```

Tailscale also restricts what a shared-in user can reach on a shared node; the ACL above makes the intent explicit and testable.

## 5. Guest sandbox (podman)

Guest jobs (anything that runs on a friend's machine, or friend-submitted work on Jeff's) run in a **rootless podman** container. Draft PR #68 implements the podman path. The table below is the set of controls the sandbox must have before friends join; treat each row as a requirement to check against #68, not as a description of shipped code:

| Control | Setting |
|---|---|
| Keys and credentials | **None.** No API keys, no GitHub token, no NATS credential inside the container. |
| Host filesystem | Only the job's own scratch checkout is mounted. No home directory, no `~/.config`. |
| User | Rootless, unprivileged, no new privileges, all capabilities dropped. |
| Network | Off by default; only what the job class strictly needs. |
| Resources | CPU, memory and time limits from the job spec and the donor's caps. |
| Lifetime | Container and scratch are removed after the job. |

"No-key" means the job carries no keys and the sandbox holds none. A donor who chooses to donate their own pay-as-you-go key keeps it in their own worker, outside the sandbox; see [DONATIONS.md](DONATIONS.md).

Which jobs can go to a friend's machine: **sandboxed, no-key jobs only** (builds, tests, local-model work). A friend's machine never runs a lane that holds Jeff's keys, and lane jobs are never routed to guests.

## 6. Secrets policy

1. **No secrets in the repo.** Tokens live in `~/.config/bob-cluster/tokens.env` and `~/.config/bob-live/token` (mode 600, owned by the user, outside the repo). The repo never contains tailnet IPs or real host names; the public Pages site publishes `main`.
2. **One credential per node.** Copy only that node's own line to each machine. Revoke one node without touching the rest.
3. **No secrets in jobs.** `cluster/spec.py` rejects specs that look like they contain AWS, Slack, OpenAI/Anthropic, Google, Telegram, Brave or GitHub keys, or long base64 blobs, including keys split across several argv items.
4. **Clean environment.** Commands and git run with a fresh minimal environment, a worker-owned `PATH` and an isolated `HOME`. Worker credentials are not inherited.
5. **Sanitized output.** Result tails are cut to 2 KB and scrubbed; dashboard strings pass through `live/sanitize.py`.
6. **Donor credentials stay home.** A donor's AI login or API key is used only by the worker on the donor's machine. The queue only ever sees remaining headroom. (Planned)
7. **Rotate on doubt.** If a token might have leaked, rotate that node's token and reload the server config.

## 7. Governance

- **Karen gates every merge.** Karen is the QA and security agent. Security-relevant changes (permissions, sandbox, enrollment, caps) always get a full Karen review.
- **No force-push.** Fixes are new commits so the review trail stays intact.
- **CI is strict.** `cluster.tests.run_strict` fails on any skipped test and on any required security or integration test that didn't run and pass.
- **Spending is prepaid only.**

## 8. Known follow-ups

| Item | Risk | Status |
|---|---|---|
| **Refuse reserved host names `hub` and `conductor`** | A guest named `hub` or `conductor` could confuse identity checks or inbox names. | **In progress. Must land before any friend joins.** |
| **Lease metadata is readable by any host** | The `holder` op returns who holds any key and its metadata. Low risk among Jeff's machines; worth scoping before guests. | Known, open. |
| **A nak'd job holds its slot during the delay** | On nats-server 2.15.1 a delay-nak'd job keeps its `max_ack_pending` slot until redelivered, so a held lease can cost one lane slot for up to one TTL (900 s). | Known, documented and asserted in tests. |
| **Apply stage-2 permissions to the live hub** | Until then the live hub runs the looser stage-1 grants. | Planned (needs Jeff's approval for the config change). |
| **Reply redirection (lease service reply must not become a lease write)** | A host can set its reply subject to a lease key and trick the hub into writing it. Lease keys must only be written by the service's own compare-and-set, not via an arbitrary reply subject. | **Open (HIGH).** Fix in progress in bob-ops-dashboard #76. |
