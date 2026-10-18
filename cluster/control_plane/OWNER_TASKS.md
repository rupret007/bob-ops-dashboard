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

A generic execution boundary is now implemented in `owner_task_runtime.py`.
It creates a new bounded draft branch at the request's full pinned SHA; it never
maps owner descriptions to existing repair PRs. The runtime uses shared task and
branch leases, an atomic lane reservation, fresh controls/quota, machine guards,
and a durable running fence. A crash or uncertain push/PR response remains
`running`/`uncertain` and requires operator reconciliation rather than replay.
Every supervised command and every running process poll repeats the guards;
lease loss, pause, stale quota, or a guard breach stops the process group.
Tests and deterministic secret/dangerous-command/test-weakening checks precede
commit and verified branch push. New PRs are always drafts. This provides no
permission to merge, publish, send messages, or deploy.

This is an opt-in implementation, not a live runtime. No scheduler is enabled.
`OwnerTaskRuntime` deliberately has no production authority defaults. Before
activation, an operator-owned adapter must implement fresh cluster
`read_controls`, `read_codex_quota`, `check_lane`, atomic `reserve_lane`,
`isolated_codex_command`, and `run_isolated_tests`. Authority records are read
from that private adapter, never from the owner request. Controls and quota
records require `scope=cluster` and `observed_at` within 30 seconds. Codex usage
must be strictly below 75%. Lane reservations must enforce the shared guards,
NucBox's seven-lane ceiling and two Codex reservations, and fail closed on an
unreachable shared authority.

The isolated command/tests adapters must enforce OS isolation, network denial,
no host credentials or secret environment, and read-only Git metadata. They must
preserve the default newest Codex model and xhigh effort. CLI prompt wording or
workspace-write flags alone do not satisfy this contract. No fallback command
exists. The test adapter demonstrates progression and race boundaries; it is
not proof of production sandbox/authority availability. Live ACL provisioning,
real authority integration, sandbox verification, scheduling and Karen review
remain blockers. Keep Web Bob task receipts pending until those are reviewed.

Git metadata is created outside the sandbox worktree with `--separate-git-dir`.
Both isolated adapters receive `trusted_git_dir` and `denied_paths`; they must
hide the metadata directory, make the worktree `.git` pointer read-only, and
terminate all sandbox descendants before returning. Trusted follow-up Git
commands pin `--git-dir`/`--work-tree`, disable hooks/fsmonitor, and use the
immutable original repository HTTPS URL for push/remote verification. Metadata,
the pointer and Git attribute files are hashed before isolation and checked
after both model and tests; any mutation aborts before privileged Git resumes.
Symlinks fail closed, and diffs disable external drivers/text conversion.
