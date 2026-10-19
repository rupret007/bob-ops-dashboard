---
name: Conductor self-improvement loop
description: >-
  use this when running the standing highest-bar harden loop: deep architecture
  self-assessment, research, implement, then 5-minute multi-lane stress — and
  when reviewing multi-agent work so misses become skill updates
---
# Conductor self-improvement loop

Pair with [Multi-LLM stack mastery](sand-workflow:multi-llm-stack-mastery), [No Cursor waste routing](sand-workflow:no-cursor-waste-routing), [Local Cursor Mini agent](sand-workflow:local-cursor-mini-agent), [Codex Goal mastery](sand-workflow:codex-goal-mastery), [Best LLM for the job](sand-workflow:best-llm-for-the-job), [Board work-now publish freshness](sand-workflow:board-work-now-publish-freshness), [Finish-out watchdog](sand-workflow:finish-out-watchdog), [Anti-rubber-stamp research gate](sand-workflow:anti-rubber-stamp-research-gate), [Bob Codex queue inject](sand-workflow:bob-codex-queue-inject).

## Highest-bar harden loop (locked 2026-09-25 ~1:49 AM CT)
Jeff’s bar is **as high as possible**. Repeat until solid:
1. **Deep self-assessment** of architecture/design (not vibes).
2. **Research** improvements (OpenAI agents-as-tools vs handoffs; supervisor+budgets; HITL).
3. **Implement** concrete skill/playbook/routine patches same turn.
4. **5-minute stress** on heavy lanes only.
5. **Round card to Jeff**, then loop.

### Bob’s role
- Bob **conducts only** — never a stress/heavy reasoning lane.
- **Grok worker ban (Jeff 2026-09-26 ~11:55 AM CT):** do **not** use Grok (Bob chat or Cloud `grok-*`) as a coding/worker lane until the ~6-day Grok usage reset (~2026-10-02). Bob stays thin conductor only. Prefer Codex/Claude/MiniMax/Gemini API/`local_cursor`.
- Heavy: Mac Mini Codex + Claude pastes, MiniMax BYOK, Gemini API direct (`gemini-3.1-pro-preview` / `gemini-3.8-flash`+thinkingLevel=low), local Cursor Composer (`composer-2.5` with `-f --trust`) as an **active** coding lane (Jeff remade ~11:56 AM CT).
- Cursor Cloud Background Agents: **0** under spend wall / empty Other Models.
- Ruthless PASS file: `/home/box/conductor/harden-loop-pass-criteria.md`
- Research brief seed: `/home/box/conductor/llm-work-now/harden-deep-research-20260925-0149.md`

### Round budgets (fail closed)
- ≤5 heavy lanes in a 5-minute stress window.
- Minimal structured briefs to workers (≤~20 lines) with **negative scope** (not Barker when parked; not second WebJam sole).
- Empty local Cursor output = FAIL → one retry with `--workspace`; second empty = STALL.
- Gemini/MiniMax errors must be recorded as lane FAIL (never silent skip).
- Cloud BA launch attempt = round FAIL.

### Round card template
```
Round N — <time CT>
Assess/research: <1 line>
Implemented: <bullets>
Stress: Codex _ | Claude _ | Gemini _ | MiniMax _ | LocalCursor _ | CloudBA 0
Eff:    Codex E_ | Claude E_ | Gemini E_ | MiniMax E_ | LocalCursor E_
CodeQ:  <repo/PR or n/a> · CI:_ · tests:_ · scope:_ · Karen:_ · honesty:_
Verdict: Stress PASS/FAIL · Eff PASS/FAIL · CQ PASS/FAIL/n/a
Next: <one implement focus>
```

## Standing keep-pressing order (locked 2026-09-24)
Jeff wants every open project driven without him nudging. Default is act, not wait.
1. Keep pressing within hard gates: Jeff owns Latest, tags, sends, spend, feel, and logins. Tagged Bob never equals approval. One WebJam coding lane. NanoBot never auto-replies to Instinct.
2. Surface Jeff only for blockers, needs-yes, and genuine advances. Quiet on no-change.
3. When Mini shell is down, switch to GitHub API, **BYOK direct (MiniMax/Gemini API)**, file reads/CopyToBox after one Shell retry. Remind reconnect at most once per day. Cursor Cloud = last resort only when spend headroom exists.
4. **Anti-waste / no double-dip:** prefer MiniMax BYOK + Gemini API (`GEMINI_API_KEY`) + Mac Mini Codex/Claude goal paste **before** Cursor Cloud. Never default-spray Cloud `gemini-*`. When Cloud spend wall is up, use Mini paste / Actions CI / BYOK / local Cursor only.
5. Use cheap BYOK models for review notes; save Codex Extra High / Claude Sonnet (Mini paste preferred) for hard coding.
6. Every long job gets a watch routine that reports milestones/stalls and deletes when done.
7. Continuously self-improve: after each miss or Jeff correction, update this lessons log and the matching craft skill in the same turn.

## Keeping work moving
- Mini down → one Cloud Agent may hold the single WebJam coding lease when spend headroom exists; Grok Bot conducts. Under spend wall, use Actions CI + BYOK + Mini paste only.
- Expired coord leases: clear via GitHub MCP even when Mini probes are paused.
- Dual-SoT board PASS only when live Pages `status.json` and `llm-work-now.json` stamps match (≤60s) after publish — in-process rewrite alone ≠ PASS. See [Board work-now publish freshness](sand-workflow:board-work-now-publish-freshness).
- Actions workflow embeds: prefer `python3 -c` one-liners (or checked-in script files); never an unindented heredoc inside `run: |` (it terminates the YAML block).
- Post-harden finish-out (Jeff ~7:14 CT, until he reorders): WebJam Latest path continues; then **Rad Dad / StoryBoard** (same lane as Travis booking) and **WashOps** into something useful; **Wildflower behind**; Barker parked until Jeff↔Chris; camper buy parked. See [Finish-out watchdog](sand-workflow:finish-out-watchdog).
- **Mini / local Cursor closeout:** every brief under `~/Documents/bob-overnight-inject/<job>/` must leave `RESULT.txt` or `BLOCKER.txt` in that workdir before the job is called done. BRIEF alone = orphan (evening 2026-09-25 Logic MCP miss).

## Before any consequential action
1. Read live state first.
2. One driver per checkout or branch.
3. Match the owner's latest words, not the plan in memory.
4. Verify app + folder + model before claiming a lane is live.

## Use the apps right
1. Canonical Mini folders first — WebJam `/Users/jeffstory/Documents/webjam`.
2. Bind Goals to the absolute checkout path.
3. Codex: Extra High / Goal for product coding.
4. Claude: confirm signed-in before `/goal`.
5. Cloud Agents: budget-gated last resort only.
6. **Grok Bot:** conduct / unstick / report only — **no Grok worker** until ~6-day usage reset (Jeff 2026-09-26).
7. Gemini / MiniMax BYOK first; tip critique = MiniMax BYOK.
8. **Local Cursor:** always `-f --trust`; active lane alongside Codex/Claude (not optional spare). See [Local Cursor Mini agent](sand-workflow:local-cursor-mini-agent).
9. **Codex stream error:** UI “Error in message stream” → **one** Retry click → if still dead after ~90s, write `BLOCKER.txt`, **do not** STEER/Retry-spam; failover coding sole to `local_cursor` or Claude on the same lease (2026-09-26 ~11:54–12:02 CT WebJam polish).

## Delegation quality (agents-as-tools)
- Bob owns Jeff-facing synthesis (**agents-as-tools** / manager pattern). Silent peers (Lane, MiniMax) never Jeff-DM and never take ownership of the user-facing turn (**handoffs** reserved for Andrea/Karen privilege peers when Jeff-facing ownership truly moves).
- Start with one conductor; add specialists only when the contract changes (different tools, policy, or output owner) — OpenAI orchestration guidance.
- Every brief: outcome, verify, constraints, boundaries, stop condition, negative scope, and **closeout artifact** (`RESULT.txt` / `BLOCKER.txt` / PR URL).
- Owner gates never move to an agent.

## Friday research sources (2026-09-25 evening)
Adopted only what beats current practice; links kept for the next retro:
- OpenAI orchestration (agents-as-tools vs handoffs; manager owns reply): https://developers.openai.com/api/docs/guides/agents/orchestration
- OpenAI Agents SDK multi-agent: https://openai.github.io/openai-agents-python/multi_agent/
- Anthropic Claude Code subagents (summary-only return; tool limits; isolation): https://code.claude.com/docs/en/sub-agents
- Cursor Cloud Agent best practices (env/secrets first; skills + AGENTS.md): https://cursor.com/docs/cloud-agent/best-practices
- Cursor agent best practices (plan before code; verifiable goals; worktrees): https://cursor.com/blog/agent-best-practices

**Adopted this Friday:** (1) keep Bob on agents-as-tools for Jeff-facing ownership; (2) Mini/local briefs require RESULT/BLOCKER closeout; (3) board repo `private` chip must match live GitHub visibility. Not adopted: Cloud BA spray, handoff of Jeff chat to silent peers, unbudgeted parallel agent fleets.

## Lessons log (newest first; fold older into rules above)
- 2026-10-18: Production outcomes determine success. Record baseline, observation window and evidence coverage before judging an iteration. Passing tests permits rollout but does not prove improved throughput. Include failed/expired/unverified jobs, stale sessions and unfinished backlog; do not count activity as success. Use the normal conductor's measured-feedback report and existing exact-head gates. Current no-Grok, no-sends and machine/quota limits supersede older lane instructions above.
- 2026-09-26 ~1:09 PM CT (retro midday): **WIN** bob-ops #57 Mini Codex salvage **03:33 CT** — RESULT SUCCESS tip `82d78b5` MERGEABLE/CLEAN DRAFT, CloudBA 0 (closeout rule held). **ORDER** Jeff **11:55–11:56 AM CT**: zero Grok worker until ~6-day reset; `local_cursor` active lane (folded #157→#158 tip `0e154e8` ~12:00). **MISS** Codex “Error in message stream” **11:54–12:02 CT** — one Retry then BLOCKER; failover to local Cursor/Claude, no STEER on dead stream. K7 Vault `private:true` still wrong on live board **13:03 CT** (GitHub public). Logic MCP BRIEF still orphan.
- 2026-09-25 ~10:09 PM CT (retro evening + Friday research): Dual-SoT held 22:02; Vault private chip miss; Logic MCP orphan BRIEF; FINISH_OUT_SLA 4h; RESULT/BLOCKER closeout; OpenAI/Claude/Cursor links adopted.
- 2026-09-25 midday→morning: Dual-SoT K2/K3; Actions `python3 -c`; finish-out RadDad/StoryBoard→WashOps→Wildflower; highest-bar harden (Bob out of stress; scorecard; Gemini pins; MiniMax VERDICT; local Cursor empty→retry).
