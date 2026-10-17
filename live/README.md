# Bob Ops Live

Real-time ops dashboard: thin **node agents** POST metrics; **aggregator** on the NucBox merges GitHub + fleet state and serves the web UI over SSE.

Public Pages board (`index.html` / `refresh.sh`) is unchanged. Access the live UI through your private Tailscale address or loopback. Keep addresses and credentials outside this repository: GitHub Pages publishes the root of `main`.

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

Authentication is required: `Authorization: Bearer <token>` (or the agent-compatible `X-Live-Token` header). Browser session cookies cannot authorize uploads. POST bodies are limited to 256 KiB; oversized bodies return 413, and malformed or negative Content-Length returns 400.

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

## Private configuration and browser access

The aggregator refuses to start without a non-empty `LIVE_TOKEN`. Both launchers
load it from `~/.config/bob-live/token` (override with `LIVE_TOKEN_FILE`) and fail
if it is missing, empty, inside the repository, or not owned by you with mode 600.
An existing environment token does not bypass this file requirement.

Prepare each machine locally (do not commit the generated token):

```bash
mkdir -p ~/.config/bob-live
chmod 700 ~/.config/bob-live
(umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > ~/.config/bob-live/token)
chmod 600 ~/.config/bob-live/token
cp live/live.env.example ~/.config/bob-live/live.env
chmod 600 ~/.config/bob-live/live.env
```

Use the same token on the aggregator and agents, transferring it privately.
Edit the private `live.env` with your addresses; the launchers source this trusted
shell file before startup. Set `BOB_LIVE_PUSH` to the aggregator's private
`http://<host>:8787/api/node` URL on remote agents. It can also come from the
environment. `LIVE_CONFIG` overrides the optional config path; set
`LIVE_CONFIG_REQUIRED=1` to fail if that config file is absent. Config files must
also be outside the checkout, owned by you, and mode 600. Accidental in-repo
`live.env`, `live/token`, and `.config/bob-live/` copies are gitignored, but must
never be used or published. `nodes.json` contains labels and capacities only.

`LIVE_BIND` defaults to `127.0.0.1`. To listen on a tailnet interface, set its
numeric Tailscale address explicitly in private config. Only loopback and the
Tailscale shared address range (prefix length 10) are allowed; wildcard, LAN,
public addresses, hostnames, and IPv4-mapped IPv6 addresses are refused. The
local NucBox agent uses the same bind address unless `BOB_LIVE_PUSH` overrides it.
`LIVE_PORT` defaults to 8787.

Mint a single-use view code on the aggregator machine, under the same OS user
as the server. The command does not need the upload token. Capture its stdout
privately rather than printing the credential into logs:

```bash
view_code=$(python3 live/aggregator.py mint-view-code --ttl 300)
```

The default lifetime is 300 seconds; `--ttl` accepts 1 through 3600 seconds.
Codes are random and separate from `LIVE_TOKEN`. Their hashes and expiries live
in `~/.config/bob-live/view-codes.json`, owned by you with mode 600 and outside
the checkout. `LIVE_VIEW_CODES_FILE` overrides this path; set it identically
for the CLI and server. The running server reads this file on redemption, so
minting does not require a restart. File locking makes redemption single-use
even across simultaneous requests. Missing or unsafe stores fail closed.

Use the captured code privately in `http://<private-host>:8787/?k=<view-code>`.
Never put `LIVE_TOKEN` in a URL: it is rejected by this login route. The server
validates the code hash with `hmac.compare_digest`, consumes the code, sets an
HttpOnly, SameSite=Strict cookie with Max-Age of 12 hours, then redirects to `/`
to strip the query string. Reused or expired codes are rejected; mint a new code
when needed. Clear the local shell variable afterward with `unset view_code`.

Later HTML, `/api/state`, and `/api/stream` requests authenticate with this cookie.
The cookie contains a signed issue time, and the server enforces its 12 hours
of validity, including closing expired browser streams. Its HMAC key is derived
from `LIVE_TOKEN`, so token rotation invalidates existing cookies. API clients
can use token headers; `/api/node` requires them. Responses are not cached, do
not send referrers, and request URLs are not logged. Keep the initial code URL
private. Only `/healthz` is unauthenticated and returns `ok`.
HTTP is intended for loopback or encrypted Tailscale transport.

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
```

**MacBook Pro** — add `mbp` to `live/nodes.json`, run agent with `--node-id mbp`.

Env: `LIVE_BIND`, `LIVE_PORT`, `LIVE_TOKEN`, `BOB_LANE_LOG_GLOB`, `BOB_WORKTREE_ROOTS`, `BOB_PROGRESS_GLOB`, `BOB_AI_CAPACITY`.

## Tests

```bash
python3 -m unittest discover -s live/tests -v
python3 qa-offline.py
```

Design reference: `live/ops-live-mockup.html`, notes: `live/RESEARCH.md`.
