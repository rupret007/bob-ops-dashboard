# Usage & machines collector

Public aggregate history for the **Usage** tab on the Bob Ops Dashboard. The collector runs **per host** (`mini` or `nucbox`) and never reads `auth.json`, chat databases, or credential files.

## Sources (read-only)

| Metric | Source |
|--------|--------|
| Codex tokens | `~/.codex/sessions/YYYY/MM/DD/*.jsonl` — last `token_count` / `total_token_usage` per session file |
| Gemini tokens & USD | `~/lane-wt/logs/gemini-spend.txt` |
| Lane counts | `~/lane-wt/logs/provider-usage.tsv` (timestamp, task, lane, model, tier) |
| Cursor / Claude / MiniMax | `~/lane-wt/logs/{provider}-spend.txt` when present; otherwise **no data** (never estimated) |
| Grok Bot (conserve-only) | No direct usage source on these hosts; **unknown**, never zero or estimated |
| Machine load / swap / disk | Local host metrics at collect time |
| Merged PRs | `gh api search/issues` with `org:rupret007 is:pr is:merged merged:YYYY-MM-DD` |

Grok Bot usage is a **CONSERVE-ONLY tracked budget**; lanes never consume it.
Each collected and merged daily row emits `grok_bot: {"status":"unknown","source":null}`.
The panel shows **Grok Bot (conserve-only)** with **unknown** and the note
"no usage source; conserve-only", including when there is no usage history.

Host label: `USAGE_MACHINE_LABEL=mini|nucbox`, else Darwin → `mini`, Linux/WSL → `nucbox`.

## Commands

```bash
# On each host (writes usage-host-<label>.json)
python3 usage_collect.py --host mini --out usage-host-mini.json
python3 usage_collect.py --host nucbox --out usage-host-nucbox.json

# Merge partials for refresh / Pages
python3 usage_collect.py --merge-dir .
```

`refresh.sh` loads `usage-host-*.json` when present and normalizes into `status.json` → `usage_machines`.

## Baseline fixture (Oct 12–16, 2026)

Pinned in `tests/fixtures/usage_machines_baseline/` and `usage-machines.json`:

| Day | Codex mini total | Notes |
|-----|------------------|-------|
| Oct 12 | 182M | fixture session totals |
| Oct 13 | 312M | |
| Oct 14 | 203M | |
| Oct 15 | 249M | |
| Oct 16 | 697M (3.48M output) | |
| Oct 16 Gemini | 11.6M tok, $42.38 | |
| Merged PRs | 130 / 110 / 72 / 23 / 75 | mocked in unit tests |

### NucBox WSL read-only run (2026-10-17)

Collector on this clone’s host found Codex sessions only for **2026-10-16** and **2026-10-17** (no Oct 12–15 tree). Oct 16 nucbox Codex total ≈ **1.20M** tokens (7 session files) vs mini baseline **697M** (mini session tree not on this box). `gemini-spend.txt` and `provider-usage.tsv` were absent here; merged PR counts from live `gh` differ from the Jeff baseline (GitHub search `total_count` capped/paginated; Oct 16 live ≈ 54 vs baseline 75). The dashboard publishes fixture/box-merged aggregates only — no paths, tailnet names, or secrets.

## Tests

```bash
python3 test_usage_collect.py -v
python3 test_board_meta.py -v
```
