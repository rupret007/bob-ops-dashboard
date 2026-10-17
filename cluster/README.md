# Bob cluster work queue (Stage 1)

NATS JetStream-backed job queue for distributed test/build/render work across Mini, NucBox, MacBook, and the dashboard reader. Jobs carry **no secrets**; each node uses its own NATS credential.

## Architecture

```text
 submit.py / nuc-verify-queue
        │  jobs.<class>.<id>
        ▼
   Stream JOBS (workqueue, 24h)
        │  pull consumer "workers"
        ▼
   worker.py (each machine)
        │  results.<id>
        ▼
   Stream RESULTS (limits, 7d)
        │
 metrics.<node> ──► dashboard (read-only) / live aggregator (--also-post)
```

- **NATS** runs on NucBox (`127.0.0.1:4222`), reachable on the tailnet via userspace Tailscale on `nuc-wsl` (tailnet IP from `tailscale ip -4`).
- **Workers** are identical; filter job `class` with `--classes`.
- **SSH** `nuc-run` / legacy `~/bin/nuc-verify` remain the fallback when the queue is unreachable or git is not clean/pushed.

## Subjects

| Subject | Purpose |
|---------|---------|
| `jobs.>` | Job specs (work queue) |
| `results.<id>` | Completion payload per job |
| `metrics.<node_id>` | Node telemetry JSON |

## Job spec v1

```json
{
  "v": 1,
  "id": "unique-id",
  "class": "test",
  "repo": "rupret007/bob-ops-dashboard",
  "ref": "<sha or branch>",
  "cmd": ["python3", "-m", "unittest", "discover", "-s", "cluster/tests", "-v"],
  "timeout_s": 1800,
  "load_ceiling": 4.0
}
```

Policy lives in `cluster/config.py`. Only the explicitly listed repository is accepted.
Each class has an exact argv allowlist: test runs unittest discovery or `npm test`,
build runs `npm run build`, and render runs `npm run render`. Extra arguments and
shell commands are rejected. The selected repository must provide the requested
entry point; adding repositories or commands requires a policy change.

IDs match `[A-Za-z0-9_-]{1,64}`. Refs are full 40-hex SHAs or strict branch/tag
names (alphanumeric, dot, underscore, hyphen, and slash-separated components;
no option prefixes, `..`, trailing dots, or `.lock` components). Timeout is capped
at 7200 seconds and load ceiling at 32; both must be finite and positive.

Optional `env` accepts only `CI=true/1`, `LANG`/`LC_ALL=C/C.UTF-8`, and `TZ=UTC`.
Commands and Git receive a fresh minimal environment with a worker-owned PATH
and isolated HOME; worker credentials and tool/loader configuration are not
inherited. Secret detection also rejects common provider keys, Telegram bot
credentials, Brave keys, long base64 strings, and keys split across argv items.

Checkouts live under `~/work/cluster-jobs/jobs/<id>`; mirrors are outside that
jobs root. Cleanup rejects paths resolving to the root, outside it, or through
job-directory symlinks.

## Join a machine

1. On NucBox (once): `bash cluster/install-nats.sh` then `bash cluster/nats-up.sh start`.
2. On NucBox: `python3 cluster/setup_streams.py` (uses `NUCBOX_TOKEN` from `~/.config/bob-cluster/tokens.env`).
3. Copy **only that node’s** line from `tokens.env` to the worker host (`chmod 600`).
4. `pip install -r cluster/requirements.txt`
5. Run worker, e.g.:

```bash
export BOB_NODE_TOKEN="$(grep '^MINI_TOKEN=' ~/.config/bob-cluster/tokens.env | cut -d= -f2)"
python3 cluster/worker.py --node-id mini --classes test,build --max-parallel 2 --light
```

Optional bridge to the live dashboard aggregator:

```bash
python3 cluster/worker.py --node-id mini --also-post http://127.0.0.1:8787/api/node ...
```

## Mesh: any computer, pooled LLM capacity (Stage 2, not yet live)

Design: [MESH.md](MESH.md). Auth review and open items: [AUTH-DESIGN.md](AUTH-DESIGN.md).

Every node advertises what it can do (`caps.<node>` heartbeat: which LLM CLIs are signed in locally,
CPU/RAM/load, lanes). Jobs go to `cap.<capability>.<id>` and only nodes holding that capability's NATS
permission *and* the local login can pull them. Each subscription only ever runs on its owner's
machine; keys and logins never leave it.

Add a computer (on the NucBox, after the rollout):

```bash
python3 -m cluster.bobctl mint --name <name> --role member     # or --role guest for a friend
```

It prints one line to paste on the new machine (macOS/Linux `curl … | bash`, or a PowerShell line for
Windows + WSL). Enter the separately displayed code at the hidden installer prompt;
never paste the code as a shell command or argument. The code is single-use, expires
in 30 minutes, and installs a background service. Mint requires a full 40-hex commit
SHA (defaults to the checkout's HEAD), downloads its GitHub archive, and embeds its
SHA256 in both paste commands. The installer verifies that digest before extraction.
There is no branch fallback; direct installer use requires `--ref` and `--sha256`.

Revoke any time: `python3 -m cluster.bobctl revoke <name>`. Revoke always sends
SIGHUP and waits for `/varz.config_load_time` to change on the pidfile's server.
Failure to confirm is a loud nonzero exit; the registry remains revoked. The
server must have been started with the absolute `--conf-out` path via `-c`/`--config`.
Use `--pidfile` and `--http` for that server. This is a live operation even without
`--live`; do not run it against a production hub during a source-only review.

Until Karen's gate, `bobctl` only renders `~/.config/bob-cluster/nats.mesh.conf`; the live `nats.conf`
is not touched unless `--live` is passed, except for revoke as described above.
Joind limits redemption to 30 attempts per source IP per rolling minute, resolved
from loopback NATS `/connz` data. Broker permissions bind each pending user to its
own `join.redeem.<user>` subject. Missing or ambiguous source data is refused.
Clients and joind must use this protocol together; older shared-subject clients
must be updated. Throwaway end-to-end check: `bash cluster/e2e-local.sh <sha> [--llm]`.

## Submit

```bash
python3 cluster/submit.py --spec-file job.json --wait
```

Enqueue requires a user with `jobs.>` publish (default **nucbox** token on the operator host).

## nuc-verify migration

Set `BOB_NATS_URL` (or write it to `~/.config/bob-cluster/nats-url`); with neither set the wrapper falls back to SSH.

Install `cluster/nuc-verify-queue` ahead of `~/bin/nuc-verify` on the PATH (or symlink). When NATS is reachable and the repo is **clean** with **HEAD on origin**, it enqueues a `test` class job instead of rsync+SSH. Otherwise it `exec`s the original `~/bin/nuc-verify` unchanged.

## Security

- Tailscale-only exposure of `<nucbox-tailnet-ip>:4222`; bind address remains loopback on NucBox.
- Per-node passwords in `~/.config/bob-cluster/tokens.env` (never committed).
- Mini/MacBook publish metrics, results, consumer-info/pull requests for `JOBS.workers`,
  and acknowledgements for that consumer. They cannot administer streams or consumers.
  Run `setup_streams.py` with the NucBox credential before starting workers.
- Monitoring binds only to `127.0.0.1:8232` (8222 is reserved for Vaultwarden).
- Dashboard is subscribe-only on `metrics.>` and `results.>`.
- Job specs must not contain credentials; result tails are sanitized (2KB).

## Tests

```bash
pip install -r cluster/requirements.txt
bash cluster/install-nats.sh --ci /tmp/nats-bin   # optional local binary
export PATH="/tmp/nats-bin:$PATH"
PYTHONPATH=. python3 -m unittest discover -s cluster/tests -v
```
