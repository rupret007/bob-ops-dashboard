# Handoff: External writers must call validate_llm_work_write (2026-09-25)

- Audience: Bob / conductor / any agent that writes `llm-work-now.json`
- Snapshot: 2026-09-25 ~02:56 CT
- Research gate: `/home/box/conductor/llm-work-now/harden-deep-research-20260925-0255.md` (R5)

## Rule
Exporting `validate_llm_work_write` from `board_meta.py` is **not** adoption.
**Any writer** of Work-now rows (conductor scripts, Mini peers, refresh ingest helpers,
manual JSON edits intended to persist) **must call** `validate_llm_work_write(row)`
(or the refresh path `finalize_llm_work_after_live_poll`) **before** commit/push of
`llm-work-now.json` / status artifacts that paint Running.

## Why
Write-time honesty only protects callers that invoke it. Skipping validate leaves
idle-class / missing-proof / future-skew Running lies on the board until the next
refresh demote — too late for Jeff's glance.

## Refresh order (R1)
Live Cloud poll stamps receiver `heartbeat_at=now` for non-stale-source live lanes
**first**, then validate + demote. Do not reintroduce validate-before-stamp in
`_normalize_work_row`.
