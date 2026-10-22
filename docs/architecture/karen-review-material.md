# Typed pinned review material

Reviewers receive one shared input format. Static review rules stay outside a
single JSON data object; PR title, author and original description are encoded
inside `pull_request`. A PR body containing forged headings, scope objects or
instructions remains one untrusted string and cannot assign server fields.

`PRInfo.review_scope` is a server-created `ReviewScope` containing the exact-head
chunk plan, complete file manifest and coverage metadata. It is distinct from
the original description. Coverage describes available material and does not
constitute a reviewer vote. The current diff is explicitly named
`chunk_under_review`, rather than misleadingly describing a chunk as Full Diff.
A scoped vote covers that chunk and its required cross-file integration; the server independently reviews every chunk and aggregates every mandatory vote, so a chunk PASS is not whole-PR approval and cannot rely on another vote or context presence.

`PRInfo.review_material` is a typed `ReviewMaterial` containing the validated
cumulative supplemental packet and its phase. The existing exact-head provenance,
complete-diff, source privacy, extension, 16k-per-source, four-entry and total
48k guards run before assignment. A follow-up replaces this one packet instead
of appending another packet. No automatic source fetch is added. Every original
diff byte and chunk ordering remains unchanged, and each reviewer still supplies
an independent complete verdict under the existing strict parser and bounds.

Codex, Gemini, MiniMax and Claude all use `build_review_prompt`; provider-specific
transport and output validation remain unchanged. The legacy display helper
`build_chunk_context` is retained for compatibility, but provider input uses
`build_review_scope` and never embeds its display text into the PR description.

Source-context failures log only `SourceUnavailable.code`, whose constructor
restricts it to fixed codes. Paths, source, model reasons and raw exception text
are omitted. Fail-closed gate behavior, absent artifacts and metrics remain
unchanged; this log makes a failure diagnosable when the gate discards scope.

Offline regressions verify forged-body isolation, original-body round trips,
exact outgoing inputs across all four provider paths, cumulative packet
replacement and controlled diagnostics alongside existing provenance/budget and
independent-vote tests. They do not prove model comprehension. Before another
live gate, separately calibrate each real reviewer on pinned known-good,
known-bad and missing-context fixtures under existing budgets, without creating
gate authority or manufacturing production PASS metrics.

A source request for another changed file can receive its complete pinned diff
when that diff is absent from the current chunk and supplied material. The entry
is explicitly `diff`, never represented as complete `source`; no file is fetched
and the immutable-source extension/privacy/16k limits remain unchanged. Exact PR
identity, pinned chunk evidence, the same cumulative four-entry/48k packet and
one independent follow-up still apply. Original source-request arrays remain in
the attempt trace, with only a controlled routing count added to diagnostics.
Repeating the source request remains unresolved; already-supplied, current-chunk
and unchanged paths retain their normal source rules. Receiving a diff is not
approval and never substitutes for either reviewer's fresh verdict.
