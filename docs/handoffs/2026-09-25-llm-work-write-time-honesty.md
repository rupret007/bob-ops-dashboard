# Handoff: LLM Work-now write-time honesty + live TTL (2026-09-25)

- Audience: Bob / Refresh / Mini agents reading the ops dashboard
- Snapshot: 2026-09-25 ~02:49 CT
- Research gate: `/home/box/conductor/llm-work-now/harden-deep-research-20260925-0247.md`

## Rule (single module — post R5)
`board_meta.py` owns Work-now honesty:

- `STALE_RUNNING_SOURCES` never paint **Running** (including when lane ∈ `live_cloud_lane_ids`).
- `LLM_WORK_RUNNING_HEARTBEAT_TTL_SEC = 900` (15m). Proof keys: `heartbeat_at` → `proof_at` → `checked_at` → `updated_at`.
- **Future skew fail-closed:** `LLM_WORK_PROOF_FUTURE_SKEW_SEC = 300` (mirror `agent_is_fresh`). Far-future proof → demote.
- **No live_cloud short-circuit.** Live Cloud poll stamps receiver `heartbeat_at` at refresh; demote still applies TTL + source gates. spend_wall → blocked.
- **Write-time:** `validate_llm_work_write(row)` auto-demotes invalid Running before persistence / ingest. Refresh preserves proof keys in `_normalize_work_row`.
- Callers: `demote_stale_running_llm_work` + `validate_llm_work_write` from Refresh; `llm_work_stale_running_violations` from QA/smoke/tests.

## Product end-state (Jeff)
After harden loop: finish WebJam including #156 Latest/publish (not blocked on Chris). **Barker stays PARKED** until Jeff talks to Chris — do not touch Barker.

## Do not
- Reintroduce live_cloud TTL bypass.
- Dual-write Mini `bob-ops-dashboard-harden-r5`.
- Open Cloud BA / Barker / spend under hard gates this slice.

## Post-R6 (2026-09-25 ~02:56 CT)
- **R1 order:** `finalize_llm_work_after_live_poll` — stamp live heartbeat **before** validate/demote.
- `_normalize_work_row` preserves proof keys only (no early validate).
- External writers: see `2026-09-25-llm-work-external-writer-validate.md`.
