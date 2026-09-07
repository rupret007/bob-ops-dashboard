# Bob Ops Dashboard

Public, mobile-friendly status board for projects Bob is working on (Jeff Story / `rupret007`).

**Live URL:** https://rupret007.github.io/bob-ops-dashboard/

## What's here

- `index.html` -- human dashboard (Claude orange `#d97757` on near-black `#0a0a0a`)
- Phone-first board: compact **pulse** (live/stale only) + one **next action** + first-screen tabs for **live types plus Parked**. Leftover-only types (today Cisco and Media) sit under Parked and must not look like active agents or a Jeff yes; they reappear as a tab only while that leftover panel is open. Existing type panels stay in the document. The glance names the highest-risk real decision or red/yellow project on one line, then an allowlisted place line: **Decide**, **Owner hold**, **Red · type**, **Review · type**, or **CI wait · type**. Review and CI wait never share leftover "needs a look" copy. One tap opens the existing panel, focuses the exact matching row, and briefly highlights it -- no second menu or category hunt. Decisions is not a first-screen tab. Never a yes-count, and owner-only lane gates / parked leftover drafts cannot hide actionable work. Unsafe or missing target keys fail closed to the selected panel without guessing a row. Lane color is live GitHub evidence; standing notes and missing CI never invent yellow or **CI pending**. First screen is not the project wall. Unknown Mac probes stay honest in the document and never invent Running; that line is not first-screen chrome. Cloud work links stay as names on that first screen. Live still holds the music stack (Vault, StoryBoard, Show Night, WebJam). Timestamp, repo footer, **Abilities**, and the fetched-repo line stay off the first screen until a type is opened; engineer notes stay behind collapsed **How this board works**.
- Tap-to-open: Cloud Agent pills with a real `cursor.com/agents/bc-…` URL (never invented) → Open agent / Open PR. Lanes prefer the open PR, plus Open repo / Open CI when those URLs are known. Turdanoid also exposes one exact-allowlisted **Play game** link; neighboring or foreign Pages URLs fail closed. A complete same-repository PR chain shows safe base-to-tip order (for example, **Stack #10 -> #11 -> #12**) and taps the repository pull list; ambiguous, forked, branching, or partial chains fall back to the honest open-PR count. iOS-safe: real `<a target=_blank>` plus `openBlank` fallback. Never invent a bc-id, stack, or green status.
- `status.json` -- machine-readable snapshot (client polls every ~30s)
- Browser Back/Forward returns through the views you open: glance, types, leftover types, and owner holds. Closing a selected type is a returnable home visit. Reloads and automatic refreshes do not add visits; opening the same glance destination twice does not add a duplicate. History stores only the existing section URL, never a decision review or approval.
- Keyboard navigation: the project-type tabs use one Tab stop; Left/Right wrap, Home/End reach the ends, and Enter/Space open or close the focused type. Selection keeps focus on that tab. A refreshed project keeps focus on the same action only when its URL and label still match; changed or removed actions return focus to the project row, and a missing or ambiguous project returns it to the visible panel.
- `.github/workflows/refresh-dashboard.yml` -- Actions cron every 15 minutes
- No secrets, tokens, CSOne customer paths, Keeper material, or private handoff text
- AdoptIQ appears only as a high-level private/offline summary with `ready_for_live_cisco=false`

Next-action priority is explicit: a non-standing pending decision first, then a real red project, then Jeff-actionable yellow (open PRs or an incomplete public listing), then CI running/pending yellow, then a standing owner hold when no such work remains. In-flight CI still beats a standing hold; it cannot hide review yellow. Only the existing `jeff-gate` and `owner-live-gate` decision kinds count as standing holds; titles and IDs do not decide authority. **Parked → Review owner holds** keeps the exact highest-risk valid hold reachable even while a project occupies the first screen. Opening that row does not review, approve, or execute it; the existing current-snapshot review guard still applies.

## Portfolio coverage

The board follows every inherited product lane. GitHub-backed rows use live repository, default-branch CI, and open-PR state for WebJam, Turdanoid, Show Night, Story Shelf, StoryBoard, Andrea NanoBot, StoryLiner, Bob Ops Dashboard, RadDadSite, Rad Dad Merch, and Cursor-OpenClaw Integration. StoryDesk and OpenClaw Runtime are explicitly **Local-only** because no authoritative remote can be claimed; private GitHub lanes stay **high-level only** and are not public live-repo, CI, or PR tap rows; Bob the Bot is a distinct private application lane shown **high-level only** and is not a public live-repo, CI, or PR tap row; private-media work appears only as a high-level **Owner-only** boundary because upload and publishing stay outside this board.

Turdanoid is an independent public gameplay-improvement lane. Its row links the repository, current-tip CI, and the curated [live game hub](https://rupret007.github.io/Turdanoid/hub.html). Green CI proves the current tip's automated checks; green Pages proves the hub deployed. Neither claims the fun/replayability pass is complete.

Private repositories expose only high-level status. Private PR bodies can never contribute Cursor agent links to the public page, and local probe work links are published only when a live refresh proves their PR repository is public. AI Music Vault remains private-content-boundary only: the catalog spine for StoryBoard and Show Night, not a public live-repo tap.

Vault, StoryBoard, Show Night, and WebJam work together as one music stack. StoryBoard is the band-business engine and consumes Vault; it is not a second catalog. Show Night is the live run sheet -- GitHub is source, and live Latest is Sites. WebJam is the making room. WebJam **Latest** is the published test candidate and is not unpublished source.

Bob the Bot is a distinct private application lane, not an alias for Andrea NanoBot, the local OpenClaw Runtime, or this Bob Ops Dashboard. Its public row is deliberately limited to a coarse bootstrap state and operating boundaries; repository links, branches, pull requests, commit identifiers, paths, and implementation content stay off the public board.

## Public agent continuity

- [Retry keyboard focus — 2026-09-07](docs/handoffs/2026-09-07-retry-focus.md) records focus retention during snapshot checks and recovery.

- [Phone browser history handoff — 2026-09-07](docs/handoffs/2026-09-07-phone-history.md) records the navigation behavior, offline checks, and review boundary.
- [Batch A portfolio handoff — 2026-08-26](docs/handoffs/2026-08-26-batch-a.md) is an immutable completion snapshot (2026-08-26). Later leftover rounds (#31–#33) landed after it; add a new dated handoff instead of rewriting Batch A.
- [Bob application registration handoff — 2026-08-26](docs/handoffs/2026-08-26-bob-application-registration.md) records the private application boundary and remaining owner gates.
- [Full-context decision review — 2026-09-05](docs/handoffs/2026-09-05-decision-review.md) records the phone review flow, current-snapshot guard, offline QA, and remaining owner boundary.
- [Phone tabs leftover honesty — 2026-09-05](docs/handoffs/2026-09-05-phone-tabs-leftover.md) records first-screen live-type tabs and leftover-only presentment under Parked.
- [Phone next-action priority — 2026-09-05](docs/handoffs/2026-09-05-phone-priority.md) records actionable-work priority and the explicit Parked route to standing owner holds.
- [Phone glance CI wait — 2026-09-05](docs/handoffs/2026-09-05-glance-ci-wait.md) records review yellow ahead of in-flight CI on the same first-screen next action.
- [Keyboard place — 2026-09-05](docs/handoffs/2026-09-05-keyboard-place.md) records tab navigation and project-action focus recovery across soft refreshes.
- [Readable saved snapshot — 2026-09-06](docs/handoffs/2026-09-06-saved-snapshot.md) records the read-only fallback when JavaScript is off or navigation cannot finish starting.
- [Phone glance type clarity — 2026-09-06](docs/handoffs/2026-09-06-glance-type.md) records the first-screen name plus allowlisted place line for the next action.
- [Agent handoff runbook](docs/AGENT_HANDOFF_RUNBOOK.md) is the reusable checklist and template for Grok Bot, Codex, and future agents.

These are sanitized operational-continuity records, not private handoff text. This repository is public: add a new dated handoff for each completed round instead of rewriting an old snapshot, and keep private lanes high-level. Never copy private repository metadata, creative details, customer data, credentials, or local paths into a handoff.

## Public board (no Unlock / OTP)

Possession of the public URL is enough. There is no Jeff verify card, no 6-digit code, and no `localStorage` gate.

Open the pending item and read its full public detail, risk, and boundary; these stay visible on phones. **Review choices** then exposes **Open approval draft / Open hold draft / Open denial draft**. These open a GitHub issue composer titled `BOB-APPROVE: <id>` (or `BOB-HOLD` / `BOB-DENY`), with the exact reviewed public context and snapshot time. Opening a draft does not submit an issue or perform an operation. Submit that issue while logged in as `rupret007`. **Real authority is that GitHub issue**, not anything on this page; high-risk work still requires the exact draft shown in chat and an owner recheck.

Decision links are read-only until the snapshot is valid, current, and explicitly reviewed. A failed poll, an incomplete/invalid/future snapshot, more than 45 minutes of refresh silence, or changed/removed decision context blocks opening a draft and requires review again. A timestamp-only refresh with identical decision content preserves review. **Retry now** checks the existing snapshot; it does not regenerate or publish it. No JavaScript means read-only. Oversized or malformed context is rejected, never truncated into an apparently complete receipt.

Before JavaScript finishes starting, the page is an explicitly labeled **saved snapshot**, not a live check. Its timestamp, project sections, full pending-decision context, and native section links remain readable. Inert tabs and action controls stay hidden. If startup fails partway through, this fallback remains available; reload to try again. Successful startup restores the existing compact home and keyboard tabs. Navigation readiness alone grants neither freshness nor review: the existing current-snapshot checks and explicit **Review choices** step still apply.

The receipt is context, not a signature or an execution lock. Existing decision ingestion is unchanged: it reads owner-issued `BOB-*` titles and does not enforce the new body receipt. Previously copied URLs, already-open GitHub composers, and later repository changes cannot be revoked by this page; the acting owner/agent must re-check current work and exact approvals before acting.

`status.json` must not contain a `verify` block. Refresh drops leftover OTP hashes fail-closed.

## Near-realtime refresh cadence

| Layer | Cadence | What it does |
|-------|---------|--------------|
| GitHub Actions | every **15 minutes** (+ manual `workflow_dispatch`) | runs `./refresh.sh`, commits `index.html` + `status.json` to `main` |
| Browser client | every **30 seconds** (pauses when tab hidden) | fetches `./status.json`; hide / iOS-return abort is not a failed poll; stale cached JSON cannot rewind freshness or the board; soft-paints only when board content changes (not on every 15m timestamp) and preserve the selected type tab; freshness says `Live` only inside the ~15m Actions window. A failed poll or >45m refresh silence changes the page to an explicit **last verified snapshot** state with one **Retry now** action. |
| Manual | on demand | `./refresh.sh` or `./refresh.sh --push` from a box with `gh`; decision issues are read-only by default |

Optional: a Bob / Grok routine can also call `./refresh.sh --push` on meaningful events (merge, release, CI red). That is additive -- Actions remains the baseline; do not block shipping on the routine.

Refreshes read decision issues but do not comment on or close them. That remote mutation is guarded behind the explicit `BOB_DASHBOARD_APPLY_DECISIONS=1` operator opt-in, and the scheduled workflow does not enable it. Builds and QA must leave the flag unset.

Before replacing the published snapshot, refresh requires a complete metadata, default-branch, open-PR, Actions, and release read for every public portfolio repository. A partial GitHub/API collection exits before writing `index.html` or `status.json`, so the last truthful board remains available instead of transiently repainting products as missing. Private high-level lanes may still be inaccessible to the Actions token and do not weaken this public-row integrity gate.

### Actions token limits

Workflow uses default `GITHUB_TOKEN` (`permissions: contents: write`) plus `gh auth setup-git`. It can read **public** `rupret007/*` repos and push this dashboard. **Private** repos (e.g. AdoptIQ / TACTrack) may show as inaccessible from Actions -- that is OK; keep high-level notes in the board.

## Publish notes

- Repo is **public** so GitHub Pages works on the free plan.
- Pages served from `main` / root.
- Theme + public Controls/pending + client poll live in `refresh.sh` (source of truth) so they survive rebuilds.
- Board HTML is escaped (`html.escape` / JS `esc` + `safeHref`). Do not render raw notes/URLs.
- Soft-paint keeps `pollSeq` / `decideBusy` race guards and a content fingerprint so timestamp-only refreshes do not flash the board. Pending items now share the main accepted-snapshot path: there is no independent startup pending fetch that can resurrect an old decision. Every poll validates required structure, real explicit-zone timestamps, bounded public decision fields, and case-insensitive unique decision IDs before advancing freshness. It restores the selected tab after a real content paint. First-screen focus keys are deterministic, length-bounded, and never used as CSS selectors; exact attribute comparison prevents malformed input from selecting a neighboring row. Tab-hide / bfcache abort invalidates the in-flight seq (not a fail). A stale cached `status.json` cannot rewind freshness or rewrite lanes. Work taps keep a real href and use `openBlank` when native `_blank` is not available.
- Tip CI is the current default-branch SHA. Pages / docs deploys, a skipped helper, and this board's scheduled refresh publisher cannot hide a failing test workflow. A skipped or cancelled helper cannot beat a success on the same SHA or become **Open CI**. A new tip with no matching run is missing CI -- not invented **CI pending** and not last-SHA green. A live queued or unstarted Actions run still paints **CI pending** and taps that run. CI failure/running/pending stays first; then an active coordination lease; then actionable review work; only then Latest vs source. Ready PRs still drive the normal count/stack. A coordination-named ready PR appears as review work only when the complete live GitHub listing proves that exact same-repo PR is open. Coordination still records a named leftover draft for the soft-paint fingerprint, but that draft never becomes **Draft #**, a lane tap, a yellow "needs a look", or an active Jeff yes. Unrelated and parked drafts remain hidden, stale claims fail closed, and private lanes expose no PR detail. When Latest SHA and tip SHA are proven different, the signal is **Latest != source** and taps `/releases/latest` -- not dead text of the tag. A tag with no comparable SHA still shows the tag. Review signals are taps (the verified ready coordination PR, one ready PR, or the repository pull list for multiple ready PRs), never invented links.
- Agents strip is fail-closed: stale or untimestamped Codex/Cursor/Claude probes paint **Unknown**. Never invent Running. A work link appears only when a currently open, same-repository PR in an allowlisted public repository advertises that exact real `cursor.com/agents/bc-` URL; probe-only and fork-PR links are dropped.
- Do not merge unrelated PRs as part of a refresh.

## refresh.sh

```bash
./refresh.sh          # rebuild index.html + status.json from live gh
./refresh.sh --push   # rebuild and push to Pages (main / root)
```

Default source-only QA (also used by the pull-request workflow): `python3 qa-offline.py`. It builds disposable artifacts with a rejecting synthetic GitHub CLI, synthetic previous state, and synthetic agent probes; it strips inherited credentials/probes and forces decision mutation off. It runs the full fail-closed claim smoke, including privacy, outage preservation, browser-state review guards, and Python/JavaScript receipt parity. Unsupported CLI commands fail; they never fall through to real GitHub. Scheduler-owned checkout artifacts remain byte-stable. Passing proves the source and offline behavior, not live repository state or publication.

For visual inspection, optionally use `python3 qa-offline.py --output-dir /tmp/bob-dashboard-review-fixture` with an absent or empty temporary directory outside a repository. It retains only visibly marked synthetic HTML/JSON and an offline notice. Do not publish these fixtures. There is no `--push` option.

`./qa-source-only.sh` remains available for a separately authorized **live repository read**: it queries the portfolio through real `gh` while building disposable artifacts. Do not use it during offline-only or excluded-lane work. `./qa-claim-smoke.sh` is the lower-level command for an already generated page.

The stale-state smoke covers the exact trust boundary: one failed live poll immediately labels the board historical, an overdue refresh does the same after the 45-minute silence window, and a successful current snapshot clears that warning. **Retry now** reuses the same bounded, no-store poll path; it does not dispatch Actions, refresh GitHub, or publish Pages.
