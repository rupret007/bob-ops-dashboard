# Bob Ops Dashboard

Public, mobile-friendly status board for projects Bob is working on (Jeff Story / `rupret007`).

**Live URL:** https://rupret007.github.io/bob-ops-dashboard/

## What's here

- `index.html` -- human dashboard (Claude orange `#d97757` on near-black `#0a0a0a`)
- Phone-first board: one **pulse strip** (freshness + agents) → **Decisions** (high/medium first; lower-risk collapsed) → **Live shipping** as compact status lanes → quieter secondary lanes. **Abilities** are a collapsed footer. Engineer notes stay behind collapsed **How this board works**.
- `status.json` -- machine-readable snapshot (client polls every ~30s)
- `.github/workflows/refresh-dashboard.yml` -- Actions cron every 15 minutes
- No secrets, tokens, CSOne customer paths, Keeper material, or private handoff text
- AdoptIQ appears only as a high-level "Cisco CS desktop -- Build 115" summary

## Public board (no Unlock / OTP)

Possession of the public URL is enough. There is no Jeff verify card, no 6-digit code, and no `localStorage` gate.

Pending **Approve / Hold / Deny** opens a GitHub issue titled `BOB-APPROVE: <id>` (or `BOB-HOLD` / `BOB-DENY`). Submit that issue while logged in as `rupret007`. **Real authority is that GitHub issue**, not anything on this page.

`status.json` must not contain a `verify` block. Refresh drops leftover OTP hashes fail-closed.

## Near-realtime refresh cadence

| Layer | Cadence | What it does |
|-------|---------|--------------|
| GitHub Actions | every **15 minutes** (+ manual `workflow_dispatch`) | runs `./refresh.sh`, commits `index.html` + `status.json` to `main` |
| Browser client | every **30 seconds** (pauses when tab hidden) | fetches `./status.json`; soft-paints only when board content changes (not on every 15m timestamp); freshness says `Live` only inside the ~15m Actions window |
| Manual | on demand | `./refresh.sh` or `./refresh.sh --push` from a box with `gh` |

Optional: a Bob / Grok routine can also call `./refresh.sh --push` on meaningful events (merge, release, CI red). That is additive -- Actions remains the baseline; do not block shipping on the routine.

### Actions token limits

Workflow uses default `GITHUB_TOKEN` (`permissions: contents: write`) plus `gh auth setup-git`. It can read **public** `rupret007/*` repos and push this dashboard. **Private** repos (e.g. AdoptIQ / TACTrack) may show as inaccessible from Actions -- that is OK; keep high-level notes in the board.

## Publish notes

- Repo is **public** so GitHub Pages works on the free plan.
- Pages served from `main` / root.
- Theme + public Controls/pending + client poll live in `refresh.sh` (source of truth) so they survive rebuilds.
- Board HTML is escaped (`html.escape` / JS `esc` + `safeHref`). Do not render raw notes/URLs.
- Soft-paint keeps `pollSeq` / `pendingSeq` / `decideBusy` race guards and a content fingerprint so timestamp-only refreshes do not flash the board.
- Agents strip is fail-closed: stale or untimestamped Codex/Cursor/Claude probes paint **Unknown**. Never invent Running.
- Do not merge unrelated PRs as part of a refresh.

## refresh.sh

```bash
./refresh.sh          # rebuild index.html + status.json from live gh
./refresh.sh --push   # rebuild and push to Pages (main / root)
```

QA: `./qa-claim-smoke.sh` (fail-closed `node --check`, ASCII-safe scripts, XSS helpers, no OTP leftovers, first-class sections, Approve draft URL).
