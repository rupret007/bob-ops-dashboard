# Handoff: LLM Work-now Running heartbeat TTL (2026-09-25)

- Audience: Bob / Refresh / Mini agents reading the ops dashboard
- Snapshot: 2026-09-25 ~02:37 CT

## Rule (single module)
`board_meta.py` owns Work-now honesty:

- `STALE_RUNNING_SOURCES` = idle / byok_oneshot / mini_paste / spend_wall / empty — never paint **Running** unless the lane id is in `live_cloud_lane_ids`.
- `LLM_WORK_RUNNING_HEARTBEAT_TTL_SEC = 900` (15m). Non-idle sources keep Running only with a proof timestamp (`heartbeat_at` → `proof_at` → `checked_at` → `updated_at`) younger than TTL.
- Demotion (source-aware): spend_wall→blocked, mini_paste→finished, else idle.
- Callers: `demote_stale_running_llm_work` from Refresh; `llm_work_stale_running_violations` from QA/smoke/tests.

## Do not
- Reintroduce sticky `status=running` in `llm-work-now.json` after one-shots finish.
- Duplicate demotion logic inline in `refresh.sh`.
- Open Cloud BA / Barker / spend / WebJam Latest-as-sole under hard gates.
