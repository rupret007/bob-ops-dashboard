# Finish-out card (K6)

Fill every cell via `scripts/finish-out-card.sh` (or by hand with `gh`).
**Blank Release URL after green CI = hole** — not “still working.”

| Field | Value |
|-------|-------|
| Repo | `REPO` |
| Tip SHA | `TIP_SHA` |
| Tip short | `TIP_SHORT` |
| CI conclusion | `CI_CONCLUSION` |
| CI run URL | `CI_RUN_URL` |
| Tag | `TAG` |
| Release URL | `RELEASE_URL` |
| Latest flag | `IS_LATEST` |
| Board stamp (`status.generated_at` / Pages) | `BOARD_STAMP` |
| Board tip (Pages commit) | `BOARD_TIP` |
| Filled at (CT) | `FILLED_AT_CT` |

## Rules
1. Tag exists → check CI on that ref.
2. CI green + Release URL blank → **hole** (run publish path or escalate once).
3. Release exists → confirm Latest (`gh api .../releases/latest` tag matches).
4. After Latest proved → nudge board: `./scripts/dispatch-board-refresh.sh release-published` (K5).
5. Do not burn tight poll loops; backoff ≤3 checks (finish-out-watchdog).
