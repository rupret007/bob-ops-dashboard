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

- **NATS** runs on NucBox (`127.0.0.1:4222`), reachable on the tailnet via userspace Tailscale on `nuc-wsl` (`100.93.165.1`).
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
  "repo": "owner/name",
  "ref": "<sha or branch>",
  "cmd": ["argv", "only", "no-shell"],
  "timeout_s": 1800,
  "load_ceiling": 4.0
}
```

Optional `env` object (string values). Specs are rejected if `cmd`/`env` match common secret patterns (`AKIA…`, `xox…`, `sk-…`, `ghp_…`, `github_pat_…`).

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

## Submit

```bash
python3 cluster/submit.py --spec-file job.json --wait
```

Enqueue requires a user with `jobs.>` publish (default **nucbox** token on the operator host).

## nuc-verify migration

Install `cluster/nuc-verify-queue` ahead of `~/bin/nuc-verify` on the PATH (or symlink). When NATS is reachable and the repo is **clean** with **HEAD on origin**, it enqueues a `test` class job instead of rsync+SSH. Otherwise it `exec`s the original `~/bin/nuc-verify` unchanged.

## Security

- Tailscale-only exposure of `100.93.165.1:4222`; bind address remains loopback on NucBox.
- Per-node passwords in `~/.config/bob-cluster/tokens.env` (never committed).
- Workers publish only `metrics.<self>` and `results.>`; dashboard is subscribe-only on `metrics.>` and `results.>`.
- Job specs must not contain credentials; result tails are sanitized (2KB).

## Tests

```bash
pip install -r cluster/requirements.txt
bash cluster/install-nats.sh --ci /tmp/nats-bin   # optional local binary
export PATH="/tmp/nats-bin:$PATH"
PYTHONPATH=. python3 -m unittest discover -s cluster/tests -v
```
