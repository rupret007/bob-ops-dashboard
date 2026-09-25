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
  - exact model id on row + in detail sheet (`Unknown` fail-closed when absent)
  - full task + full goal text
  - repo + branch + PR field (number/URL when present)
  - cloud agent id and Open agent link when available
  - status plus last update (cloud checked_at when present, snapshot timestamp otherwise)
  - tip SHA + CI signal from matched public repo lane when available
  - lease state / next action notes from coordination fields when present
  - recent public-safe snippets from llm work + cloud/detail context (fail-closed filters)
- Added phone-safe sheet controls (44px dismiss target) and link chips with existing safe URL plumbing.

### Spend visibility (polished in PR #57)
- Added **spend_session** and **spend_day** fields to work rows for resource consumption tracking
- Work-now belly now displays spend when available (yellow warning color for visibility)
- Detail sheet includes "Session spend" and "Today spend" fact rows
- Spend values are sanitized and formatted with `_safe_spend()` (fail-closed: invalid values become empty)
- Supports numeric values (formatted as `$X.XX`) and pre-formatted strings (`$X.XX` or `X.XX`)
- Both first-paint (Python) and soft-paint (JavaScript) paths handle spend consistently

### Model ID handling (polished in PR #57)
- Work-now belly shows exact model IDs directly under task title (e.g., `Model: claude-opus-4-20250514`)
- Model IDs sourced from multiple fields in priority order:
  1. `model` field
  2. `model_id` field
  3. `resource_model` field
  4. `runner_model` field
- Fail-closed fallback to `Unknown` when no valid model ID is present
- Model validation via `_safe_model_id()` enforces pattern `[A-Za-z0-9][A-Za-z0-9._:-]{1,79}`
- Detail sheet includes model as first fact row

### Public-board safety
- Reused/kept existing allowlisted URL validators (`safeAgentUrl`, `safePrUrl`, `safeActionsUrl`, `safeRepoUrl`, `safePullsUrl`, `safeReleaseUrl`, `safeGameUrl`) before any outbound href.
- Recent events are generated from already-public status fields; no private paths, OTP/auth controls, or customer payloads added.
- Spend values are sanitized (only `$X.XX` format allowed, no scripts or injection vectors)
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
