# Bob Ops Live

Real-time ops dashboard: thin **node agents** POST metrics; **aggregator** on the NucBox merges GitHub + fleet state and serves the web UI over SSE.

Public Pages board (`index.html` / `refresh.sh`) is unchanged. Access live UI only on Tailscale, e.g. `http://100.93.165.1:8787` (userspace `tailscaled` forwards to `127.0.0.1:8787`).

## Architecture

```
[Mac mini / NucBox / MBP]  --POST node/v1-->  [aggregator :8787]  --SSE-->  browser
                              gh poll 60s
```

- `live/agent.py` — one process per machine (`live/nodes.json`)
- `live/aggregator.py` — `ThreadingHTTPServer`, stdlib only
- `live/web/index.html` — single-file UI (no CDN)
- `live/sanitize.py` — all outward strings scrubbed

## JSON contract

### node/v1 (agent → `POST /api/node`)

```json
{
  "version": "node/v1",
  "node_id": "mini",
  "label": "Mac mini",
  "ts": 1697480000.1,
  "load": {"l1": 10.8, "l5": 9.2, "limit": 14},
  "cpu": {"pct": 72.5, "note": "est"},
  "memory": {"total": 8589934592, "used": 7000000000},
  "swap": {"total": 0, "used": 0},
  "disk": {"path": "/", "free_gb": 20.4, "total_gb": 256},
  "max_lanes": 3,
  "lanes": [{"name": "lane-foo", "repo": "bob-brain", "status": "running"}],
  "throughput": {"loc_per_min": 120.0, "test_runs_per_min": 2.0, "actions_per_min": 15.0},
  "gates": [{"pr": 79, "sha": "abcd1234", "verdict": "PASS", "repo": "Turdanoid"}],
  "ai_capacity": [{"provider": "Codex", "remaining_pct": 2, "reset_ct": "Thu 1:29 AM"}]
}
```

Optional header: `X-Live-Token` when `LIVE_TOKEN` is set on the aggregator.

### state/v1 (`GET /api/state`, SSE `event: state`)

```json
{
  "version": "state/v1",
  "updated_at": "2026-10-16T16:45:00-05:00",
  "timezone": "America/Chicago",
  "speed_index": {"score": 42.5, "weights": {}, "components": {}, "inputs": {}},
  "speed_history": [{"ts": 1697480000, "score": 40}],
  "nodes": [{"id": "nuc", "label": "NucBox K6", "online": true, "speed": {}}],
  "needs_you": [],
  "pipeline": {"draft": 4, "ci": 2, "karen": 3, "ready": 1, "merged_today": 14},
  "throughput": {"merged_today": 14, "merged_7d_avg": 6.1},
  "events": [],
  "formula": {"description": "Speed index 0-100", "weights": {}, "full_scale": {}}
}
```

Speed index: 30% LOC/min (log, 500=full) + 20% tests/min (6=full) + 20% actions/min (60=full) + 15% CPU% + 15% active lanes (12=full).

## Run

**NucBox (aggregator + local agent)**

```bash
chmod +x live/run-nucbox.sh
./live/run-nucbox.sh
```

**Mac mini (agent only)**

```bash
chmod +x live/run-mini.sh
./live/run-mini.sh
# or explicitly:
python3 live/agent.py --node-id mini --push http://100.93.165.1:8787/api/node --interval 2
```

**MacBook Pro** — add `mbp` to `live/nodes.json`, run agent with `--node-id mbp`.

Env: `LIVE_BIND`, `LIVE_PORT`, `LIVE_TOKEN`, `BOB_LANE_LOG_GLOB`, `BOB_WORKTREE_ROOTS`, `BOB_PROGRESS_GLOB`, `BOB_AI_CAPACITY`.

## Tests

```bash
python3 -m unittest discover -s live/tests -v
node --check live/web/index.html 2>/dev/null || true
```

Design reference: `live/ops-live-mockup.html`, notes: `live/RESEARCH.md`.
