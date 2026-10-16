# Bob Ops redesign: research notes (2026-10-16, CT)

## What exists today (patterns worth taking)
- **Grafana / Datadog NOC layouts:** a top row of big status stats (RED/USE: rate, errors, duration / utilization, saturation, errors), then time-series panels. Color is used only for state. Takeaway: put a glanceable KPI strip first and keep colors for meaning.
- **Datadog Monitors / PagerDuty:** "triggered first" sorting, plus ack and owner on every alert. Takeaway: a **Needs You** queue, sorted by urgency and age.
- **Linear / GitHub Projects:** dense rows, status pills, grouped by state, with keyboard and filter chips. Takeaway: pipeline columns (PR → CI → Karen → Merged), with each item as one row that has a pill.
- **Buildkite / GitHub Actions dashboards:** a pipeline row showing its latest build color, duration and a link straight to the failing step. Takeaway: show CI per PR with "failing since" and a deep link.
- **LangSmith / AgentOps / Helicone:** agent runs as traces (start, duration, model, cost, status) plus cost and quota meters per provider. Takeaway: Live Work rows show model, machine and elapsed time. AI capacity shows meters, with **unknown shown as hatched gray, never 0 or 100**.
- **Vercel / Railway / Statuspage:** per-service status dots, a "last updated Xs ago" line, and an incident timeline. Takeaway: a freshness stamp in the header that goes stale-red after 20 min (matches the dual-SoT TTL), plus a Recent events timeline.

## Admin dashboard UX principles applied
1. Answer "do I need to do anything?" within 3 seconds, so Needs You comes first and an empty state reads "Nothing needs you".
2. Glanceable top row: 4 to 6 KPIs at most, each with a delta against the 7-day average.
3. Exceptions over inventory: healthy items collapse, and failures and stale items expand.
4. Mobile-first: a single column at 390px, tap targets of 44px or more, sticky section nav, and no hover-only information.
5. Honesty: every number carries a source and an age. "Unknown" is a first-class state. The public repo shows no secrets, phone numbers or private text.
6. Drill-downs open as bottom sheets on phone and side panels on desktop, keeping the main view scannable.

## Information architecture
1. **Header:** "Bob Ops", a freshness stamp (status.json and llm-work-now pair), and the overall health dot.
2. **KPI strip:** Needs You count · Lanes running · PRs open · Merged today (vs 7-day average) · Mini load · Codex left.
3. **Needs You:** approvals, decisions and questions for Jeff, plus activation clicks, each with age and a one-tap link.
4. **Health:** Mini and NucBox cards (online, 5-min load vs 14, memory/swap, free disk vs the 15 GB floor) and AI capacity meters (measured vs unknown).
5. **Live Work:** lanes grouped by machine, each with repo, task, model, start time, elapsed time and its latest log line.
6. **Pipeline:** per project, PRs in stage columns (Draft → CI → Karen → Ready → Merged), with fail reasons.
7. **Throughput:** opened and merged today vs the 7-day average (sparkline).
8. **Recent events:** a timeline of merges, gate results, load spikes and cleanups.
9. **Routines:** last run and next run.
10. **Drill-down sheet:** full row detail (PR checks, Karen notes summary, lane log tail with secrets scrubbed).

## Data sources (for the build, later)
Mini: `uptime`/`vm_stat`/`df`, `screen -ls`, `~/lane-wt/logs/*.log` (HOST/RESULT lines), `sync/ai-capacity.json`, `jobs/*/progress.md` (KAREN_GATE_REQUEST and gate lines). NucBox: `/proc/loadavg`, `free` and `df` over the existing ssh (`nuc_ed25519`, port 2222). GitHub: `gh pr list`/`gh run list`. Collect on the NucBox every 5 min to keep the Mini light, and keep the dual-SoT stamp rules from the SKILL.

## Round 2: web research on live gauges and agent fleets (2026-10-16 4:48 PM CT)
Sources: Maersk Design System (meter chart), Datashow (gauge field guide), usefuldatatips (gauge alternatives), redsurgetechnology (bullet vs gauge vs sparkline), sumboard (live dashboard best practices), Zylos Research (agent fleet observability, 2026-06), autolearningagents (agent dashboards, 2026-05), agenticuxpatterns (fleet health dashboard), Scrim UI (multi-agent ops console).
- **Gauges:** use one hero gauge plus small per-machine gauges at most. Each needs an explicit min and max, 2–3 labeled bands, a target tick, and **the numeric value printed inside the arc**. Pair every gauge with a **sparkline** (a gauge alone has no history). Secondary KPIs read better as bullet bars. The band thresholds should be documented, so add a tap-to-explain formula.
- **Refresh:** set it by how fast a decision is needed, not the fastest possible rate. Fleet overviews typically update every 5–10 s, and machine gauges can justify 1–2 s. Pause updates when the tab is hidden. Show a stale state explicitly: dim the page and show "last update Xs ago" when the stream drops.
- **Show change, not just state:** add ↑↓ arrows and deltas vs the 7-day average, briefly highlight values that changed, and use color only as a backup to text and shape.
- **Fleet consoles:** the headline layer has 5 numbers or fewer, then investigation, then detail (trace or log). Use one status card per agent, sorted by severity, and a selected card swaps a side panel, not the page. Count pending approvals in the header. A failed lane is one card, not an outage.
- **Transport:** SSE is enough for one-way server→browser updates (it reconnects automatically and works through proxies). WebSocket is only needed for two-way control.

## Live architecture (chosen; thin and swappable)
- **Node agent** (`live/agent.py`, Python stdlib, one per machine, config-driven): it samples cheap OS counters every 2 s and the expensive ones (git numstat, df, screen -ls, file reads) every 15–60 s, then POSTs a `node/v1` JSON snapshot to the aggregator. Target: under 1% CPU on the Mini.
- **Aggregator** (`live/aggregator.py`, stdlib, on the NucBox): holds the latest snapshot per node, polls `gh` every 60 s for the pipeline and throughput, computes the speed index, and serves `/` (the page), `/api/state` (JSON) and `/api/stream` (SSE, every 2 s). It binds to 127.0.0.1 and is reached only through the tailnet (the NucBox runs userspace tailscaled), not the public internet.
- **Contract:** `node/v1` (POST /api/node) and `state/v1` (GET/SSE). Any later orchestrator can replace either side by speaking the same JSON.
- **Speed index (0–100):** 30% LOC/min (log-scaled, where 500 LOC/min = full) + 20% test runs/min (6/min = full) + 20% agent actions/min from lane logs (60/min = full) + 15% CPU utilization + 15% active lanes (12 = full). The page explains it on tap.
