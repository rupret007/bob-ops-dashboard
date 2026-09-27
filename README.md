# Bob Ops

A public, phone-first operations board for Jeff Story.

**Live board:** https://rupret007.github.io/bob-ops-dashboard/

The overview shows Codex, Claude, Cursor, Gemini, and MiniMax with their current state, project, task, and evidence age. Five project cards show **Web Bob**, **WebJam**, **AdoptIQ**, **StoryBoard**, and **Storyland Fantasy**, each with tip SHA, draft PR, current-tip CI, and a next action. Project notes, decisions, activity detail, and refresh diagnostics stay collapsed. There is no authentication gate or songs section.

## Project mapping

| Card | Repository | Public information |
| --- | --- | --- |
| Web Bob | `bob-ops-dashboard` | Tip, drafts, CI, next action |
| WebJam | `webjam` | Tip, drafts, CI, next action |
| AdoptIQ | `AdoptIQ` | High-level offline candidate only |
| StoryBoard | `StoryBoard` | Tip, drafts, CI, next action |
| Storyland Fantasy | `Storyland-Fantasy-Football` | High-level private workspace only |

Private metadata is labeled **Private**, and unavailable public metadata is labeled **Unknown** or **No run**. AdoptIQ retains `ready_for_live_cisco=false`. Private branches, PRs, commit identifiers, customer data, credentials, and implementation detail never belong in this public snapshot.

## Sources and refresh

- `refresh.sh` collects repository metadata, complete open-PR lists, default-branch CI, and releases using `gh`. An incomplete public repository collection leaves the previous snapshot intact. The scheduled refresh remains every 15 minutes.
- `ui/dashboard.js` is the shared server/browser renderer. `render_dashboard.py` embeds it and `ui/dashboard.css` in `index.html`; no production dependencies or external assets are downloaded. The initial page also works without JavaScript.
- `status.json` uses schema version 2: five `projects`, five `llm_work` rows, and operational metadata. Legacy section/song fields are not carried forward.
- `llm-work-now.json` and the snapshot are published together with matching freshness stamps. A page rebuild never supplies a missing activity heartbeat. Running requires an active source and a proof timestamp within 15 minutes; timestamps more than five minutes ahead fail closed. Legacy `cursor-cloud` input maps to Cursor, with explicit Cursor assignments preferred.
- Browser polls use no-store GET requests every 30 seconds while visible, with an eight-second timeout. Timestamp-only refreshes preserve the DOM. Content changes preserve expanded details and focus. Older cached snapshots cannot rewind the board or clear a failure warning. Heartbeat ages and expiration keep advancing without a new snapshot.
- A failed poll or more than 45 minutes without a snapshot shows a **last verified snapshot** notice. **Retry now** only retries the snapshot request.
- Drafts are displayed separately from ready PRs and never imply that an agent is running. CI is matched to the default-branch tip; an old green run cannot become current green. Pages/deployment and skipped helpers cannot hide a failing test run.

The page is accessible by URL. Decision links open GitHub issue drafts; submitting the issue as `rupret007` records the decision. Refresh reads those issues without mutating them. Scheduled builds and QA leave `BOB_DASHBOARD_APPLY_DECISIONS` unset (or `0`). No provider jobs, sends, merges, or deployments are launched by viewing the board.

## Build and QA

Requires Python 3.9+, Node.js 18+, and authenticated `gh` for a live rebuild. Node uses built-in modules; npm packages are only for browser QA.

```bash
./refresh.sh                     # read live GitHub data and rebuild locally
./qa-claim-smoke.sh               # validate generated artifacts and run regression tests
python3 qa-offline.py             # full disposable synthetic integration, no live reads
npm ci --ignore-scripts
npx --no-install playwright install chromium
python3 qa-offline.py --browser   # Chromium at 320, 390, 768, and 1280px
```

Offline QA copies source into a temporary directory, blocks live `gh`/provider calls, visibly marks its synthetic output, and verifies that the checkout's snapshots are unchanged. Browser QA covers readable text, overflow, keyboard disclosures, safe links, soft refresh, stale cache, failure/retry, heartbeat expiry, and hide/resume races. Retain a fixture with `--output-dir /tmp/unique-empty-directory`; never publish it.

Feature PRs may include rebuilt artifacts when the public schema or layout changes. Hosted PR QA validates their public-data contract and independently rebuilds synthetic artifacts; ordinary scheduled refreshes continue to own ongoing data updates. `qa-source-only.sh` remains an optional live-read check in a temporary directory. `./refresh.sh --push` is the existing direct publication command and requires explicit intent to publish; use a draft feature PR for review.

## Visual review

Before/after phone and desktop captures are in [reviews/](reviews/README.md). To reproduce against a generated board without contacting external sites:

```bash
node scripts/capture-review.js /path/to/board before
node scripts/capture-review.js /path/to/board after
```

Historical continuity notes are in `docs/handoffs/`; they describe the interface and boundaries at their recorded dates.
