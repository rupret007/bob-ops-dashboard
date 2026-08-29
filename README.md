# Bob Ops Dashboard

Public, mobile-friendly status board for projects Bob is working on (Jeff Story / `rupret007`).

**Live URL:** https://rupret007.github.io/bob-ops-dashboard/

## What's here

- `index.html` -- human dashboard (Claude orange `#d97757` on near-black `#0a0a0a`)
- Phone-first board: one **pulse strip** (freshness + agents) + one short status + a **tab per existing type** (Live, Apps, Cisco, Bob, Media, Parked; Decisions when something needs a yes). First screen is not the project wall. Unknown Mac probes collapse to one **Agents unknown** line -- never invent Running. Cloud work links stay as names on that first screen. Live still holds the music stack (Vault, StoryBoard, Show Night, WebJam). **Abilities** and the fetched-repo line stay off the first screen until a type is opened; engineer notes stay behind collapsed **How this board works**.
- Tap-to-open: Cloud Agent pills with a real `cursor.com/agents/bc-…` URL (never invented) → Open agent / Open PR. Lanes prefer the open PR, plus Open repo / Open CI when those URLs are known. Turdanoid also exposes one exact-allowlisted **Play game** link; neighboring or foreign Pages URLs fail closed. A complete same-repository PR chain shows safe base-to-tip order (for example, **Stack #10 -> #11 -> #12**) and taps the repository pull list; ambiguous, forked, branching, or partial chains fall back to the honest open-PR count. iOS-safe: real `<a target=_blank>` plus `openBlank` fallback. Never invent a bc-id, stack, or green status.
- `status.json` -- machine-readable snapshot (client polls every ~30s)
- `.github/workflows/refresh-dashboard.yml` -- Actions cron every 15 minutes
- No secrets, tokens, CSOne customer paths, Keeper material, or private handoff text
- AdoptIQ appears only as a high-level private/offline summary with `ready_for_live_cisco=false`

## Portfolio coverage

The board follows every inherited product lane. GitHub-backed rows use live repository, default-branch CI, and open-PR state for WebJam, Turdanoid, Show Night, Story Shelf, StoryBoard, Andrea NanoBot, StoryLiner, Bob Ops Dashboard, RadDadSite, Rad Dad Merch, and Cursor-OpenClaw Integration. StoryDesk and OpenClaw Runtime are explicitly **Local-only** because no authoritative remote can be claimed; private GitHub lanes stay **high-level only** and are not public live-repo, CI, or PR tap rows; Bob the Bot is a distinct private application lane shown **high-level only** and is not a public live-repo, CI, or PR tap row; private-media work appears only as a high-level **Owner-only** boundary because upload and publishing stay outside this board.

Turdanoid is an independent public gameplay-improvement lane. Its row links the repository, current-tip CI, and the curated [live game hub](https://rupret007.github.io/Turdanoid/hub.html). Green CI proves the current tip's automated checks; green Pages proves the hub deployed. Neither claims the fun/replayability pass is complete.

Private repositories expose only high-level status. Private PR bodies can never contribute Cursor agent links to the public page, and local probe work links are published only when a live refresh proves their PR repository is public. AI Music Vault remains private-content-boundary only: the catalog spine for StoryBoard and Show Night, not a public live-repo tap.

Vault, StoryBoard, Show Night, and WebJam work together as one music stack. StoryBoard is the band-business engine and consumes Vault; it is not a second catalog. Show Night is the live run sheet -- GitHub is source, and live Latest is Sites. WebJam is the making room. WebJam **Latest** is the published test candidate and is not unpublished source.

Bob the Bot is a distinct private application lane, not an alias for Andrea NanoBot, the local OpenClaw Runtime, or this Bob Ops Dashboard. Its public row is deliberately limited to a coarse bootstrap state and operating boundaries; repository links, branches, pull requests, commit identifiers, paths, and implementation content stay off the public board.

## Public agent continuity

- [Batch A portfolio handoff — 2026-08-26](docs/handoffs/2026-08-26-batch-a.md) is an immutable completion snapshot (2026-08-26). Later leftover rounds (#31–#33) landed after it; add a new dated handoff instead of rewriting Batch A.
- [Bob application registration handoff — 2026-08-26](docs/handoffs/2026-08-26-bob-application-registration.md) records the private application boundary and remaining owner gates.
- [Agent handoff runbook](docs/AGENT_HANDOFF_RUNBOOK.md) is the reusable checklist and template for Grok Bot, Codex, and future agents.

These are sanitized operational-continuity records, not private handoff text. This repository is public: add a new dated handoff for each completed round instead of rewriting an old snapshot, and keep private lanes high-level. Never copy private repository metadata, creative details, customer data, credentials, or local paths into a handoff.

## Public board (no Unlock / OTP)

Possession of the public URL is enough. There is no Jeff verify card, no 6-digit code, and no `localStorage` gate.

Pending **Approve / Hold / Deny** opens a GitHub issue titled `BOB-APPROVE: <id>` (or `BOB-HOLD` / `BOB-DENY`). Submit that issue while logged in as `rupret007`. **Real authority is that GitHub issue**, not anything on this page.

`status.json` must not contain a `verify` block. Refresh drops leftover OTP hashes fail-closed.

## Near-realtime refresh cadence

| Layer | Cadence | What it does |
|-------|---------|--------------|
| GitHub Actions | every **15 minutes** (+ manual `workflow_dispatch`) | runs `./refresh.sh`, commits `index.html` + `status.json` to `main` |
| Browser client | every **30 seconds** (pauses when tab hidden) | fetches `./status.json`; hide / iOS-return abort is not a failed poll; stale cached JSON cannot rewind freshness or the board; soft-paints only when board content changes (not on every 15m timestamp); freshness says `Live` only inside the ~15m Actions window |
| Manual | on demand | `./refresh.sh` or `./refresh.sh --push` from a box with `gh`; decision issues are read-only by default |

Optional: a Bob / Grok routine can also call `./refresh.sh --push` on meaningful events (merge, release, CI red). That is additive -- Actions remains the baseline; do not block shipping on the routine.

Refreshes read decision issues but do not comment on or close them. That remote mutation is guarded behind the explicit `BOB_DASHBOARD_APPLY_DECISIONS=1` operator opt-in, and the scheduled workflow does not enable it. Builds and QA must leave the flag unset.

### Actions token limits

Workflow uses default `GITHUB_TOKEN` (`permissions: contents: write`) plus `gh auth setup-git`. It can read **public** `rupret007/*` repos and push this dashboard. **Private** repos (e.g. AdoptIQ / TACTrack) may show as inaccessible from Actions -- that is OK; keep high-level notes in the board.

## Publish notes

- Repo is **public** so GitHub Pages works on the free plan.
- Pages served from `main` / root.
- Theme + public Controls/pending + client poll live in `refresh.sh` (source of truth) so they survive rebuilds.
- Board HTML is escaped (`html.escape` / JS `esc` + `safeHref`). Do not render raw notes/URLs.
- Soft-paint keeps `pollSeq` / `pendingSeq` / `decideBusy` race guards and a content fingerprint so timestamp-only refreshes do not flash the board. Tab-hide / bfcache abort invalidates the in-flight seq (not a fail). A stale cached `status.json` cannot rewind freshness or rewrite lanes. Work taps keep a real href and use `openBlank` when native `_blank` is not available.
- Tip CI is the current default-branch SHA. Pages / docs deploys, a skipped helper, and this board's scheduled refresh publisher cannot hide a failing test workflow. A skipped or cancelled helper cannot beat a success on the same SHA or become **Open CI**. A new tip with no matching run yet paints **CI pending**, not last-SHA green + a release tag. CI failure/running/pending stays first; then actionable stack/open-PR review work; only then Latest vs source. When Latest SHA and tip SHA are proven different, the signal is **Latest != source** and taps `/releases/latest` -- not dead text of the tag. A tag with no comparable SHA still shows the tag. Stack and open-PR signals are taps (one PR, or the repository pull list for multiple PRs) -- not dead text next to a title that already opens the PR.
- Agents strip is fail-closed: stale or untimestamped Codex/Cursor/Claude probes paint **Unknown**. Never invent Running. A work link appears only when a currently open, same-repository PR in an allowlisted public repository advertises that exact real `cursor.com/agents/bc-` URL; probe-only and fork-PR links are dropped.
- Do not merge unrelated PRs as part of a refresh.

## refresh.sh

```bash
./refresh.sh          # rebuild index.html + status.json from live gh
./refresh.sh --push   # rebuild and push to Pages (main / root)
```

QA from a source-only branch: `./qa-source-only.sh`. It rebuilds `index.html` and `status.json` in a disposable directory, runs the full fail-closed claim smoke, and proves the scheduler-owned files in the checkout were not touched. `./qa-claim-smoke.sh` is the lower-level command for an already generated page.
