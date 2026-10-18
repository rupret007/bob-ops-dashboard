---
name: MiniMax tip VERDICT contract
description: >-
  use this when filing or scoring MiniMax tip critiques — final channel must be
  VERDICT-only; never put think-tags in prompts; CoT-only is Eff E1 FAIL
---
# MiniMax tip VERDICT-only contract

Locked 2026-09-25 ~2:36 CT after R4 CoT flood. Updated ~2:47 CT after R5 redo pain.

## When
Any MiniMax tip / PR critique / BYOK critique that Bob or MiniMax Runner will score or act on.

## Rule
Final channel must be usable VERDICT text:
1. First line: `VERDICT: PASS` or `VERDICT: FAIL` or `VERDICT: PASS-with-LEFTOVER`
2. Then short Findings / Must-fix / CodeQ notes
3. No chain-of-thought, no think-blocks, no prompt restatement as the body

## Prompt hygiene (R5 hard lesson)
- **Never** put literal think open/close tags (or `</` + `think>`) in the user/system prompt — R5 r1 returned parseable `VERDICT: FAIL` claiming “no input.”
- Keep tip prompts **compact**; put the diff/evidence in clear fenced code without think markup.
- Prefer: “After private reasoning, emit VERDICT line + short bullets in plain text outside any think block.”
- Tip-once after a **usable** VERDICT (PASS or grounded FAIL). CoT-only / empty-input does not count as usable — one compact retry max, then FAIL the lane.

## Scoring
- Missing VERDICT after strip → Eff **E1 FAIL** (HTTP 200 alone is not enough)
- CoT-only → unparseable; do not merge or board-promote
- Usable grounded FAIL counts as contract PASS for the pipe (Eff ≥ E2 if findings are concrete)

## Pair with
Full note: `/home/box/conductor/minimax-tip-verdict-contract.md`
Architecture: `/home/box/conductor/llm-work-now/architecture-delta-20260925-0236.md`
Exit bar: `/home/box/conductor/llm-work-now/harden-loop-exit-bar-20260925.md`


## Prompt hygiene (R5)
- No think-close / think-open tags in user prompts; compact prompts; tip-once after usable VERDICT.
- Pair: `/home/box/conductor/minimax-tip-verdict-contract.md`
