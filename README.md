# Bob Ops Dashboard

Public, mobile-friendly status board for projects Bob is working on (Jeff Story / `rupret007`).

**Live URL:** https://rupret007.github.io/bob-ops-dashboard/

## What's here

- `index.html` -- human dashboard (Claude orange `#d97757` on near-black `#0a0a0a`; Jeff verify gate)
- First-class sections: **Abilities** (what Bob can do), **Controls** (what Jeff can do), **Features** (what the board is)
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

Fixed allowlist: `jeffstory007@gmail.com` only. Bob emails a 6-digit code (issuer TTL **2 hours**). The page SHA-256-checks it against `status.json.verify`. Unlock still fails on the phone when `exp` is past. Missing/wrong email and non-64-hex sha fail closed (no default-to-Jeff).

GitHub Pages / Fastly caches `status.json` at **max-age 600**. If Unlock sees no verify (or the Pages fetch fails), it falls back to `raw.githubusercontent.com/rupret007/bob-ops-dashboard/main/status.json` (`no-store`, same allowlist / sha / exp checks).

Refresh snapshots `refresh_started_ms` at start and must **never** drop a verify that was still live at that clock (`exp > refresh_started_ms`) or still within `issued_at + TTL` (plus 5 minutes grace). The 2026-08-23 iPhone miss was `15m OTP + 15m Actions cron + preserve only if exp > now` wiping the hash during a ~20s rebuild. Helper: `preserve_verify.py`. Gate: `./qa-claim-smoke.sh`.

Unlock also keeps iOS double-submit / stale-poll guards: `unlockBusy`, `unlockTouchAt`, `pollSeq`, `pendingSeq`, `decideBusy`.

## Publish notes

- Repo is **public** so GitHub Pages works on the free plan.
- Pages served from `main` / root.
- Theme + verify UI + client poll live in `refresh.sh` (source of truth) so they survive rebuilds.
- Board HTML is escaped (`html.escape` / JS `esc` + `safeHref`). Do not render raw notes/URLs.
- Do not merge unrelated PRs as part of a refresh.

## refresh.sh

```bash
./refresh.sh          # rebuild index.html + status.json from live gh
./refresh.sh --push   # rebuild and push to Pages (main / root)
```

QA before calling Unlock good: `./qa-claim-smoke.sh` (fail-closed `node --check`, ASCII-safe scripts, XSS helpers, preserve race, raw fallback, first-class sections).

## Jeff verify + control panel

1. In Bob chat: **send dashboard code**
2. Bob emails a 6-digit code only to `jeffstory007@gmail.com`
3. Enter it on the live page to unlock this device
4. Pending items: **Approve / Hold / Deny** opens a GitHub issue titled `BOB-APPROVE: <id>` (must be logged in as `rupret007`)
5. Real authority is that GitHub issue, not localStorage

Helper: `python3 issue-dashboard-code.py` then email the printed code and `./refresh.sh --push`.

Do not weaken the Jeff email allowlist.
