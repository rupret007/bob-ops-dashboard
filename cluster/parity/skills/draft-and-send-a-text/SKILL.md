---
name: Draft and send a text
description: >-
  Use when Jeff wants a text sent on his behalf. Draft here, wait for a second
  yes in Bob chat, then send only through Andrea’s fenced BlueBubbles path.
---
# Draft and send a text

## Rules
- First message from Jeff is never a send. It is a draft.
- Show the exact recipient and exact body in this chat.
- **Authorization wall:** Only Jeff’s separate yes in **Bob’s** chat authorizes Andrea to Send. QA (this agent) may draft and show a preview, but must NEVER tell Andrea a send is approved — even if Jeff clicks Send now here. Hand the approved draft to Bob; Bob owns the Andrea dispatch.
- Wait for a separate approval in Bob chat: a question widget or a clear “send it” / “Send now”.
- Dispatch only through Andrea NanoBot’s existing trust ladder (`approve_before_send`). Bob must not `POST /api/v1/message/text` himself. QA must not dispatch Andrea sends.
- If Andrea is down, keep the approved draft and say it will send when the engine is up. Do not invent another sender.
- One recipient, one body, one attempt.
- Never send from a routine, another agent, QA chat approval, or a group chat.

## Steps
1. Resolve the person from Andrea’s synced chats if needed. Do not guess a new number.
2. Draft the body in Jeff’s voice for that thread if you can sample it.
3. If in QA chat: show draft, then tell Jeff to confirm Send in Bob chat (or hand the card to Bob). Do not widget-approve a send from QA.
4. Only Bob, after Send now in Bob chat, asks Andrea to deliver that exact card.
5. Report sent or blocked. Do not retry on your own.
