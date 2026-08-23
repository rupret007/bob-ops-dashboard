# Bob Ops Dashboard

Public, mobile-friendly status board for projects Bob is working on (Jeff Story / `rupret007`).

**Live URL:** https://rupret007.github.io/bob-ops-dashboard/

## What's here

- `index.html` -- human dashboard (Claude orange `#d97757` on near-black `#0a0a0a`; Jeff verify gate)
- `status.json` -- machine-readable snapshot (client polls every ~30s)
- `.github/workflows/refresh-dashboard.yml` -- Actions cron every 15 minutes
- No secrets, tokens, CSOne customer paths, Keeper material, or private handoff text
- AdoptIQ appears only as a high-level "Cisco CS desktop -- Build 115" summary

## Near-realtime refresh cadence

| Layer | Cadence | What it does |
|-------|---------|--------------|
| GitHub Actions | every **15 minutes** (+ manual `workflow_dispatch`) | runs `./refresh.sh`, commits `index.html` + `status.json` to `main` |
| Browser client | every **30 seconds** (pauses when tab hidden) | fetches `./status.json`; if `generated_at` changes, soft-reloads; shows `Live - updated N ago` |
| Manual | on demand | `./refresh.sh` or `./refresh.sh --push` from a box with `gh` |

Optional: a Bob / Grok routine can also call `./refresh.sh --push` on meaningful events (merge, release, CI red). That is additive -- Actions remains the baseline; do not block shipping on the routine.

### Actions token limits

Workflow uses default `GITHUB_TOKEN` (`permissions: contents: write`) plus `gh auth setup-git`. It can read **public** `rupret007/*` repos and push this dashboard. **Private** repos (e.g. AdoptIQ / TACTrack) may show as inaccessible from Actions -- that is OK; keep high-level notes in the board.

## Jeff verify gate

Fixed allowlist: `jeffstory007@gmail.com` only. Bob emails a 6-digit code; the page SHA-256-checks it against `status.json.verify`. No mailto challenge. `localStorage` unlock is UX-only; real authority is a GitHub issue from `rupret007`.

## Publish notes

- Repo is **public** so GitHub Pages works on the free plan.
- Pages served from `main` / root.
- Theme + verify UI + client poll live in `refresh.sh` (source of truth) so they survive rebuilds.
- Do not merge unrelated PRs as part of a refresh.

## refresh.sh

```bash
./refresh.sh          # rebuild index.html + status.json from live gh
./refresh.sh --push   # rebuild and push to Pages (main / root)
```

QA before calling Unlock good: `./qa-claim-smoke.sh` (fail-closed `node --check`, ASCII-safe scripts, verify allowlist).

## Jeff verify + control panel

1. In Bob chat: **send dashboard code**
2. Bob emails a 6-digit code only to `jeffstory007@gmail.com`
3. Enter it on the live page to unlock this device
4. Pending items: **Approve / Hold / Deny** opens a GitHub issue titled `BOB-APPROVE: <id>` (must be logged in as `rupret007`)
5. Real authority is that GitHub issue, not localStorage

Helper: `python3 issue-dashboard-code.py` then email the printed code and `./refresh.sh --push`.
