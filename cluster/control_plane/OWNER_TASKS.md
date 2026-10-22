Owner-confirmed Web Bob draft requests use `owner_task_scheduler.py`. This is a
shared pending inbox and a consumer, not a second process launcher. The inbox
uses the configured cluster NATS authority and a separate no-TTL
`STORYPOOL_CLAIMS_OWNER_TASKS` KV bucket. Missing authority or bucket fails closed.
There is no local fallback. Task descriptions are stored only in that private
operator-authorized inbox; they are never printed to logs.

An operator can provision the bucket with the existing authorized NATS identity:
`python -m cluster.control_plane.owner_task_scheduler --bootstrap`.
Provisioning is not invoked automatically from Web Bob.

`admit_owner_draft_task(request)` records a bounded, full-SHA, fixed-repository,
Codex-only, draft-only intent and returns `awaiting_admission`. This receipt
confirms durable intent only. It does not assert quota, lane availability,
permission to execute, Karen PASS, or success of any task. Duplicate request IDs
must have identical request content; ambiguous writes are resolved only by
reading the durable shared authority. The Web Bob client must recognize the
pending receipt separately from a fully guarded `queued` receipt.

`consume_pending(runtime, inbox=..., client=..., machine=...)` holds shared task
and branch claims, rereads inside the lease, invokes the single trusted runtime,
validates all guard checks and fresh Codex weekly usage strictly below 75%, then
CAS-transitions the shared row to queued. A crash after runtime admission can
replay the *runtime request*, so the runtime MUST durably deduplicate request_id
cluster-wide. The consumer itself never launches Codex or invokes the PR-only
fix worker. The runtime must repeat pause/drain/quota/guard checks and obtain the
branch lease at execution, preserve full-SHA constraints and draft-only PRs,
and require exact-head Karen gates. A pending description is never authority.

Remaining integration blocker: this StoryPool version has only a PR-fix
scheduler; it has no guarded generic owner-task execution runtime, global
pause/provider control authority, or fresh shared plan-quota authority. Do not
wire this consumer to the PR-only worker or local legacy queue. Until the
runtime supplies those real gates and durable receipts, owner tasks remain
pending and no Codex lane is started. This change requires review and deployment
before Web Bob can see the module; it does not install or enable services.
