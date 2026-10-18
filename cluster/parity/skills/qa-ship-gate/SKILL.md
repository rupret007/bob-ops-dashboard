---
name: QA ship gate
description: >-
  use this when Bob is about to call any user-facing work good, or when Jeff
  wants realtime/after-the-fact QA across the stack — fail closed before Jeff
  sees it
---
# QA ship gate (nuts to bolts)

Bob builds. Karen breaks (functional QA). Only PASS may be called good.

For auth/secrets/Pages/send fences/connectors/spend-adjacent claims, also run [Security ship gate](sand-workflow:security-ship-gate). Same Karen; tighter trust-boundary bar.

## Modes
1. **Realtime** — while Bob builds, QA watches claims and fails early (syntax, dead taps, empty emails).
2. **After-the-fact gate** — before Bob tells Jeff something is good, QA must PASS the claim under test.

## Bar
- Fail closed. Code-reading alone is not enough for interactive UI.
- Prefer real browser click, CLI smoke, `node --check`, or the repo’s real tests.
- Hash/status checks alone never clear Unlock, email, or tap UX.
- Mobile-first surfaces need a proven tap (Unlock, Approve), not only desktop click.

## Universal steps
1. Name the one claim under test.
2. Static: syntax/checkers/tests available for that surface.
3. Happy path: do the user action end-to-end.
4. Failure path: wrong/expired input still shows clear feedback.
5. Report to Bob: `PASS` or `FAIL` + exact repro. FAIL blocks the “good” message.

## Dashboard checklist (the link/code/Unlock class of bugs)
- Before any dashboard “good” claim, run `/workspace/bob-ops-dashboard/qa-claim-smoke.sh` (optional `QA_OTP=<code>` for hash/expiry). Static PASS is required; it does **not** replace browser Unlock.
- OTP email/chat body contains the real 6-digit code (never placeholders).
- Live `status.json` verify hash matches the issued code and is unexpired; refresh must not wipe mid-window.
- Page JS passes `node --check` (no smart quotes, no broken strings).
- Unlock PASS = before/after `#auth-status` change + `body.jeff-verified` after click. Ignore page-outside `typeof doUnlock === undefined` (IIFE).
- Hard-refresh still keeps verify localStorage; soft-paint must not wipe auth.
- Pair with Security ship gate for OTP wipe races, CDN stale verify, allowlist fail-open.

## Scope
Everything Bob ships to Jeff: dashboard, Telegram, WebJam door, texts drafts, Codex/Cursor/Claude status claims, emails, Approvals.
