# MiniMax review response contract

Official documentation checked on 2026-10-19. No provider request, credential
inspection, authentication change or live review was performed for this change.

MiniMax marks the legacy `/v1/text/chatcompletion_v2` endpoint deprecated. Its
`response_format` option is restricted to MiniMax-Text-01, so adding JSON/schema
mode for the configured MiniMax-M2.7 model has no documented basis. The legacy
request specification also omits `reasoning_split`. [Legacy endpoint reference](https://platform.minimax.io/docs/api-reference/text-post),
[official legacy OpenAPI specification](https://platform.minimax.io/docs/api-reference/text/api/openapi.json).

The supported `/v1/chat/completions` endpoint explicitly lists MiniMax-M2.7 and
documents `reasoning_split: true` for returning reasoning separately from the
final `content`. Its request specification exposes no `response_format`.
Karen therefore uses this endpoint with reasoning separation and continues to
parse only the complete final content. The model, existing credentials,
16384-token limit, temperature, request timeout, pinned diff and one permitted
format-error retry remain unchanged. [Chat Completions reference](https://platform.minimax.io/docs/api-reference/text-chat-openai),
[official Chat Completions OpenAPI specification](https://platform.minimax.io/docs/api-reference/text/api/openapi-chat-openai.json).

This change can prevent reasoning from contaminating a structured answer; it
does not constrain M2.7 to JSON or establish the cause of observed prose replies.
Natural-language final answers, reasoning containing JSON, truncated responses
and malformed schemas still fail closed. FAIL/BORDERLINE remain terminal votes.
Offline fixtures cover these boundaries without accepting guessed JSON.

The documented `system` message role carries a static independent-review and
JSON response contract. Every PR value, diff and requested context remains in
the `user` message; none is interpolated into the system message. This promotes
the existing contract without adding an undocumented JSON mode or treating
another reviewer's PASS as evidence. It is a prompt-compliance measure, not a
guarantee that the model emits JSON. [Supported message roles](https://platform.minimax.io/docs/api-reference/text-chat-openai).

Malformed-response diagnostics retain only character count, controlled leading
and prefix categories, a leading-BOM boolean, and a controlled failure stage
(`envelope`, `content`, `json_decode`, `schema`, or `context`). These distinguish
prose, markup, reasoning tags, BOM/format prefixes and non-object JSON without
logging raw text, tag values, paths or arbitrary characters. Classification
does not normalize the parser input or rescue a malformed verdict. The one
identical format-error retry and all approval gates remain unchanged.

The shared prompt requires security/functional issue arrays to contain only
concrete unresolved defects with changed-file `file:line` evidence. With no such
defects those arrays must be empty. Positive assurances and verified protections
belong in `risk_notes`; placeholders and no-issue strings do not belong in issue
arrays. Existing strict parsing is unchanged: any populated blocking array still
blocks a PASS, even when its text sounds positive. Fixture tests demonstrate both
that rejection and acceptance of verified checks in `risk_notes`.

Production request compatibility and response-format reliability remain rollout
observations. No fixture result is a Karen verdict or evidence of live PASSes.
