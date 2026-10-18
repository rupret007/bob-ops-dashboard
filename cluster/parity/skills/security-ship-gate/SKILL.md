---
name: Security ship gate
description: >-
  use this when Bob is about to clear auth, secrets, Pages, send fences,
  connectors, or spend-adjacent work — Karen security fail-closed before Jeff
  sees good
---
# Security ship gate

Use when Bob is about to call anything security-sensitive good, or when a change touches auth, secrets, public Pages, send fences, connectors, payments, or control planes. Fail closed. Bob builds; Karen (or Security mode) breaks trust boundaries before Jeff sees “good.”

## Separation of duties
1. **Bob** — builds, conducts, drafts. Never self-clears security.
2. **Karen (QA + Security)** — functional QA *and* security ship-gate. Fail closed. Never authorizes Andrea sends.
3. **Andrea** — texts engine only. Send only after separate yes in Bob front-door chat.
4. **Jeff** — money, feel/set-list/catalog, sends, logins, destructive. Final authority.

## When this gate is mandatory
- Public GitHub Pages / status.json / client auth (Unlock, OTP, localStorage)
- Secrets, tokens, emails, allowlists, OTP hashes
- Andrea/BlueBubbles send path, Telegram ingest, control API bind
- Dashboard Approve/Hold/Deny → GitHub issue authority
- Connectors (Gmail, Drive, Calendar, etc.) scope and outbound mail
- Spend paths (Domino’s, Amazon, bills) — never auto-pay
- Any claim “unlocked / verified / safe to merge / safe to send”

## Checks (fail closed)
1. **No secrets in git or public Pages** — tokens, customer rows, CSOne paths, private handoff text.
2. **Auth cannot silently fail open** — missing verify ≠ Jeff; refresh must not wipe live OTP mid-window; CDN stale handled.
3. **Send fences intact** — no Bob/Karen authorize Andrea; stage-before-send; approval surface is Bob chat only.
4. **Control plane** — dashboard actions only open drafts; real yes is `rupret007` GitHub issue prefixes.
5. **Allowlists tight** — dashboard verify email only `jeffstory007@gmail.com` unless Jeff widens.
6. **ASCII-safe / parse-safe** public JS where applicable (`node --check`, smoke).
7. **Report** `PASS` or `FAIL` + exact repro. FAIL blocks Bob saying “good.”

## Pair with
- [QA ship gate](sand-workflow:qa-ship-gate) for UX/functional claims
- [Release gate](sand-workflow:release-gate) for repo CI green before merge/release

## Never
- Auto-send, auto-post, auto-pay
- Widen allowlists without Jeff
- Call Chromium-only Unlock a full iPhone ship PASS when the claim is mobile
