---
name: Finish-out watchdog
description: >-
  use this when a release tag or ship path is in flight — catch
  CI-green-but-unpublished, infinite await thrash, and missing Latest flips
  without burning poll loops
---
# Finish-out watchdog

Use after a product cut reaches tag/CI (e.g. WebJam unsigned Latest) or any “almost shipped” state.

## Fail-closed outcomes (check real side effects)
Not model words — verify with `gh`:
1. Tag exists (`gh api repos/.../git/ref/tags/vX`)
2. Tag/push CI conclusion
3. GitHub Release exists (`gh release view`)
4. Latest flag / publish path as required by that repo

Missing Release after green CI = **hole**, not “still working.”

## Anti-thrash
- Poll with backoff (e.g. 2m → 5m), max ~3 checks then sleep or hand to a watch routine.
- Do **not** burn an executor on tight await loops for 70+ minutes.
- If blocked only on Jeff gates (Final Build / feel / logins / spend / Latest ownership), write status and stop — do not fake progress.

## Actions when CI green + Release missing
1. Run the repo’s documented publish path (draft Release → unsigned Latest as allowed).
2. If publish needs Jeff, wake the parent once with the exact blocker (never auto-send).
3. Record artifact path + SHA/tag in llm-work-now / handoff note.

## After Latest / Release publishes (2026-09-25)
WebJam `v0.28.4` published **07:57 CT** while the ops board still showed **v0.28.3** at **07:54 CT** (15m cadence lag). Dual-SoT can be honest and still lag the release chip.

Until `release.published` → `repository_dispatch` (`release-published`) is wired on bob-ops-dashboard:
1. Immediately `workflow_dispatch` (or equivalent) a board refresh after any Latest/Release publish.
2. Confirm Pages `status` + `llm-work-now` stamps match and the release chip flipped — do not call finish-out done on tag alone.
3. Prefer event-driven refresh over tight CI poll loops (pair with [Board work-now publish freshness](sand-workflow:board-work-now-publish-freshness)).

## Same-day finish-out SLA (2026-09-25 evening)
When the owner finish-out order’s **#1 open item** has no git/coord progress for **≥4 hours** in Jeff’s waking window (and keep-pressing is on):
1. Press that lane (BYOK / Mini paste / local Cursor — CloudBA only with spend headroom), **or**
2. Write an explicit `PARKED` reason on the coord issue / finish-out card (Jeff gate, lease conflict, or owner reorder).

Band-watch wakes (Rad Dad / Stalemate) are **not** finish-out progress. Quiet-correct watches are wins; they do not satisfy this SLA. Measured miss: after WebJam Latest morning, StoryBoard/WashOps idle **13:09–22:09 CT** with no press and no PARKED note.

## Mini / local Cursor job closeout (2026-09-25 evening)
A finish-out or utility Goal that only drops `BRIEF.md` under `~/Documents/bob-overnight-inject/<job>/` is **not done**. Require `RESULT.txt` (commands + outputs) or `BLOCKER.txt` (exact Jeff-facing dialog) in that workdir. Orphan example: Logic MCP 3.17 brief at **13:10 CT** with no closeout by **10:09 PM CT**. Pair with [Bob Codex queue inject](sand-workflow:bob-codex-queue-inject).

## Owner finish-out order (Jeff ~7:14 CT, until he reorders)
After harden floor / WebJam Latest path:
1. **Rad Dad / StoryBoard** (same lane as Travis booking help) into something actually useful.
2. **WashOps** into something actually useful.
3. **Wildflower** behind those.
4. **Barker** parked until Jeff talks to Chris Story.
5. Camper buy / Show Night parked unless Jeff says otherwise.

Do not invent a different product order from memory of the overnight list.

## Pair with
- [Conductor self-improvement loop](sand-workflow:conductor-self-improvement-loop)
- [Board work-now publish freshness](sand-workflow:board-work-now-publish-freshness)
- Board honesty: chip `generated_at` must refresh when finish-out state changes; repo `private` must match GitHub visibility
