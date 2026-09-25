## Status detail sheet (source-only draft slice)

### Goal
Implement one cohesive, phone-first in-board detail experience so tapping a lane or Work-now row shows deeper context without navigating away from the board.

### What shipped in source
- Added a **bottom-sheet detail panel** (in-board, dismissible) in `refresh.sh` output:
  - Opens from lane-row tap or Work-now row tap.
  - Preserves explicit link behavior for `Open PR / Open repo / Open CI / Open agent / Play game`.
  - Includes:
    - status
    - full note/goal text (not truncated)
    - snapshot timestamp (`generated_at_display`)
    - safe links (PR/CI/agent/repo/game when available)
    - short **recent events** list derived fail-closed from public snapshot fields only.
- Added row metadata to both first-paint and soft-paint render paths:
  - `data-detail-kind`
  - `data-detail-key`
- Added **Back/dismiss behavior**:
  - Opening the sheet pushes history state.
  - Browser Back closes the sheet first.
  - Close button, scrim tap, and Escape close the sheet.
- Expanded **Work now resource-type details** so Jeff can read assignment state without leaving board:
  - full task + full goal text
  - repo + branch + PR field (number/URL when present)
  - cloud agent id and Open agent link when available
  - status plus last update (cloud checked_at when present, snapshot timestamp otherwise)
  - tip SHA + CI signal from matched public repo lane when available
  - lease state / next action notes from coordination fields when present
  - recent public-safe snippets from llm work + cloud/detail context (fail-closed filters)
- Added phone-safe sheet controls (44px dismiss target) and link chips with existing safe URL plumbing.

### Public-board safety
- Reused/kept existing allowlisted URL validators (`safeAgentUrl`, `safePrUrl`, `safeActionsUrl`, `safeRepoUrl`, `safePullsUrl`, `safeReleaseUrl`, `safeGameUrl`) before any outbound href.
- Recent events are generated from already-public status fields; no private paths, OTP/auth controls, or customer payloads added.
- Change is source-only (`refresh.sh` + tests + docs), with no direct scheduler snapshot edits.

### QA additions
- Updated `test_offline_browser.js` to verify:
  - in-board sheet opens from a lane tap
  - full note text appears in sheet
  - snapshot stamp appears
  - close button dismisses
  - browser Back dismisses sheet

### Out of scope for this slice
- No authority/OTP/auth behavior changes.
- No refresh ownership changes for generated snapshots.
- No WebJam product-lane edits.
