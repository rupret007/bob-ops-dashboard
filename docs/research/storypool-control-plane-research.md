# StoryPool Control Plane Research

Research on how open-source multi-agent and AI coding orchestrators handle conductor, dispatch, review gates, and scheduling without an always-on chat LLM. This informs the migration of StoryPool's Grok-based bots (Bob conductor, lane dispatcher, Karen ship gate, scheduled routines) to a mostly-deterministic architecture.

## Executive Summary

The surveyed projects consistently favor **deterministic outer orchestration** (state machines, cron/webhook triggers, NATS/Kafka/Temporal queues) with **LLM calls reserved for judgment-heavy steps** (reasoning, review, code generation). None rely on an always-on chat LLM for scheduling or dispatch. Key reusable patterns:

1. **Event-sourced state with deterministic replay** (OpenHands, LangGraph)
2. **Typed work queues with pull consumers** (NATS JetStream, Temporal)
3. **Server-validated merge gates** (ship/ElMundiUA, OpenSWE, PR-Agent)
4. **Per-model role routing** (Aider, Plandex, CrewAI model packs)
5. **Budget/quota tracking as a first-class concern** (CrewAI, Plandex, OpenHands)

---

## 1. OpenHands (formerly OpenDevin)

**Repository:** https://github.com/all-hands-ai/OpenHands  
**License:** MIT  
**Stars:** ~91K

### Architecture

OpenHands V1 uses **event-sourced, stateless execution**:
- `ConversationState` is the only mutable source of truth
- Agents, tools, and LLMs are immutable, serializable components
- Append-only `EventLog` supports deterministic replay, pause/resume, recovery, and context condensation

### Conductor/Dispatch Pattern

- **No always-on LLM for orchestration.** Agents execute step-by-step; each `step()` call processes one reasoning cycle.
- Sub-agent delegation is implemented as a tool: a parent spawns independent conversations and blocks until parallel tasks complete.
- Dynamic scheduling, async delegation, and fault recovery are intended as **user-defined tools**, not core framework changes.

### Scheduling

- Workflows can be triggered from schedules or webhooks via the automation service.
- Scoped access, approval gates, and isolated execution are supported.

### Merge Gate Pattern

- Uses `SecurityAnalyzer` to evaluate action risk before execution.
- Confirmation mode stores actions as pending, waits for user approval for high-risk actions.

### Budget/Quota

- Cost tracking at the conversation level (`total_cost`, `message_cost`).
- No hard enforcement at the framework level; provider limits apply.

### What's Reusable

- Event-sourcing pattern for audit trail and replay.
- Skill/prompt injection without modifying agent logic.
- The "workflow as a tool" pattern for scheduled/triggered runs.

---

## 2. SWE-agent / Open SWE (LangChain)

**Repository:** https://github.com/SWE-agent/SWE-agent (original), https://github.com/langchain-ai/open-swe  
**License:** MIT  
**Stars:** ~21K (SWE-agent)

### Architecture

Open SWE uses a **durable LangGraph runtime** with separate graphs:
- `agent` — main coding agent
- `reviewer` — read-only PR reviewer (cannot commit, push, or create PRs)
- `analyzer` — repository analysis
- `chat` — PR chat without mutation tools
- `scheduler` — fans cron tasks into scheduled agent runs

### Conductor/Dispatch Pattern

- Source-specific webhook routes verify provider input and choose a **deterministic thread identifier**.
- `dispatch_agent_run` creates runs with `durability="sync"` and checkpoint at each step.
- An incoming follow-up interrupts the active run and resumes with its history and new message.

### Scheduling

- Five-field cron expressions validated and registered against the scheduler assistant.
- Scheduled runs create fresh threads and durable agent runs.
- PR CI monitoring uses **immediate signed webhook evaluation** plus a **deterministic 10-minute cron fallback** — no model tokens consumed while state is unchanged.

### Merge Gate Pattern

- Opt-in `/baby-sit` skill monitors PRs.
- New failures resume the originating agent thread for confidence-gated diagnosis.
- Flake reruns are capped and deduplicated.
- **Reviewer is read-only:** findings validated against changed lines before publication; no commit/push/PR-creation path.

### Budget/Quota

- Not explicitly surfaced in architecture docs; likely handled at the LangSmith/provider level.

### What's Reusable

- Deterministic cron fallback pattern that consumes no tokens when state is unchanged.
- Signed webhook → deterministic thread ID pattern.
- Read-only reviewer graph with restricted toolset.

---

## 3. Aider

**Repository:** https://github.com/Aider-AI/aider  
**License:** Apache-2.0  
**Stars:** ~25K+

### Architecture

Aider's `Coder` class is the central orchestrator, coordinating chat, repository context, Git, model calls, and cost tracking. It supports a **three-model setup**:
1. **Main model** — chat and code editing
2. **Weak model** — commit messages and history summarization (cheaper)
3. **Editor model** — architect-mode implementation

### Conductor/Dispatch Pattern

- **No external conductor.** Aider is a local CLI tool; the user is the conductor.
- Architect mode is two-phase: main model proposes changes, then Coder creates an editor Coder with the selected editor model.
- Costs are carried across the transition.

### Budget/Quota

- Per-model token/reasoning settings (`--thinking-tokens`, `--reasoning-effort`).
- Model metadata pricing for cost estimation.
- **No hard enforcement** — Aider reports cost but doesn't enforce provider limits.

### What's Reusable

- **Three-tier model routing:** cheap model for cheap tasks, expensive model for judgment.
- Model metadata file for pricing and context windows.
- Cost tracking per session.

---

## 4. Goose (Block)

**Repository:** https://github.com/block/goose  
**License:** Apache-2.0  
**Stars:** ~53K

### Architecture

Goose uses a **re-entrant, turn-based state machine**:
- The persisted conversation is the state.
- Ordered `Operations` handle steering, compaction, approval, tool execution, recipes, subagents, retries, hooks, and inference.
- Tool dispatch routes frontend tools to the client, schedule/final-output tools to dedicated handlers, and other tools through ExtensionManager/MCP.

### Conductor/Dispatch Pattern

- **No always-on LLM for scheduling.** The `Scheduler` struct manages `ScheduledJob` entities persisted in a local store.
- Jobs trigger via `tokio-cron-scheduler`; each fires a new recipe execution session.
- Subagents are isolated `Agent` instances with configurable providers, extensions, and turn limits.

### Scheduling

- Cron-based jobs persisted and monitored.
- Recipes triggered automatically or interactively from CLI/GUI.

### Approval/Gate Pattern

- `ToolApprovalOperation` can yield to the UI and resume later.
- Approvals are stored; nothing stays running while the user decides.
- `MaxTurnsOperation` enforces a hard cap (default 1000 turns).

### Budget/Quota

- `max_turns` configurable per session.
- Turn limits and retry timeouts are explicit.

### What's Reusable

- **State machine architecture** with ordered operations — easy to insert new behaviors without modifying core.
- Approval as a state transition, not a blocking call.
- Persisted scheduler with cron expressions.

---

## 5. Plandex

**Repository:** https://github.com/plandex-ai/plandex  
**License:** AGPL-3.0  
**Stars:** ~12K

### Architecture

Plandex supports **multi-model orchestration through role-specific model packs**:
- Planner, coder, builder, summarizer, verifier, context-loader can each use different models.
- Custom packs via JSON or `plandex model-packs create`.

### Conductor/Dispatch Pattern

- **No separate task scheduler.** Plandex executes long-running plans automatically.
- Configurable autonomy: full auto to step-by-step review.

### Budget/Quota

- `plandex usage` tracks spend by plan, branch, user, model pack, role, and provider.
- Monthly budgets and alert thresholds supported.
- Optimization levers: cheap/oss model packs, smart-context, tree-sitter file maps, prompt caching.

### What's Reusable

- **Role-based model packs** for multi-model orchestration.
- Usage tracking with allocation by plan/branch/user/role.
- Optimization strategies for token spend.

---

## 6. AutoGen / Microsoft Agent Framework

**Repository:** https://github.com/microsoft/autogen → now maintenance-mode  
**License:** MIT (code), CC-BY-4.0 (docs)  
**Stars:** ~60K

### Architecture

AutoGen supports both **deterministic and LLM-driven orchestration**:
- **Sequential workflows:** publish/subscribe with explicit topic routing.
- **GraphFlow:** define explicit nodes, edges, conditions, parallel branches, loops; execution order is deterministic but messages are broadcast unless filtered.
- **SelectorGroupChat / Swarm:** LLM chooses the next agent (dynamic routing).

### Conductor/Dispatch Pattern

- For fixed pipelines, use sequential publish/subscribe or GraphFlow.
- For dynamic routing, use SelectorGroupChat with max rounds, stall limits, and termination conditions.
- **Recommended hybrid:** deterministic outer workflow with LLM agents inside individual steps.

### Budget/Quota

- `max_tool_iterations`, termination conditions, round limits.
- No built-in cost tracking at the framework level.

### Status

**AutoGen is now maintenance-mode.** Microsoft directs new projects to **Microsoft Agent Framework** (1.0 GA April 2026), which uses a typed, graph-based `Workflow` with data-flow edges and supports request/response pauses and checkpointing.

### What's Reusable

- GraphFlow for deterministic multi-agent orchestration.
- Termination strategies (MaxMessageTermination, TextMentionTermination).
- Hybrid pattern: deterministic rules enforce hard constraints, model chooses among safe candidates.

---

## 7. CrewAI

**Repository:** https://github.com/crewAIInc/crewAI  
**License:** MIT  
**Stars:** ~57K

### Architecture

CrewAI supports **deterministic multi-agent orchestration through Flows**:
- Event-driven steps with `@start`, `@listen`, `@router` decorators.
- Conditional routers, loops, shared state, explicit Crew execution.
- `@persist` for resumable state.

### Conductor/Dispatch Pattern

- **Flows provide determinism where it matters:** steps execute in a defined sequence with explicit branching.
- **Crews provide intelligence:** each step is powered by an agent or an entire crew.
- A Crew node embeds multiple agents collaborating across multiple tasks as a single Flow step.

### Budget/Quota

- Agent limits: `max_iter`, `max_execution_time`, `max_rpm`.
- Task limits: `max_tokens`, `guardrail_max_retries`.
- Crew-level `max_rpm` overrides agent limits.
- **Deterministic steps outside the LLM:** parsing, validation, lookups, and math don't require a language model.

### Scheduling

- Async execution (`akickoff`, `akickoff_for_each`), task-level `async_execution`.
- External schedulers or AMP runtime policies govern execution windows.

### What's Reusable

- **Flows for deterministic + agentic:** determinism where it matters, intelligence where you need it.
- Token spend optimization: hierarchical vs. sequential, tool scoping, model routing.
- State persistence and resumption.

---

## 8. LangGraph

**Repository:** https://github.com/langchain-ai/langgraph  
**License:** MIT  
**Stars:** ~40K

### Architecture

LangGraph is a **low-level orchestration framework** for long-running, stateful agents:
- Mix deterministic hand-coded steps with LLM-driven agentic steps in a single graph.
- Typed `StateGraph` with explicit nodes, conditional Python routing, bounded retry loops.
- Checkpointer persistence, interrupt/resume approval gates.

### Conductor/Dispatch Pattern

- **Routing is plain Python.** Models never decide which node runs next; they write to state, and code reads state to choose the next node.
- Edges define transitions; messages are passed along edges.
- Durable execution with checkpointing.

### Merge Gate Pattern

A LangGraph-based PR reviewer example:
1. Three specialist reviewers read the same diff in parallel.
2. A merge step combines findings.
3. A critic checks the review by executing its claims.
4. Nothing gets posted until a human approves.
5. Loop limit prevents infinite iterations.

**Key design decision:** The models never decide which node runs next.

### Budget/Quota

- Not built into LangGraph; handled at the application or LangSmith level.

### What's Reusable

- **Typed state graphs with Python routing** — testable without calling a model.
- Checkpointer for persistence across restarts.
- Interrupt/resume for human-in-the-loop.

---

## 9. Temporal Workflows

**Documentation:** https://docs.temporal.io  
**License:** MIT (SDK), proprietary (Server)

### Architecture

Temporal provides **durable execution** for long-running workflows:
- Workflows block without consuming compute.
- Durable timers survive worker restarts, deployments, and infrastructure migrations.
- Event history is the complete audit trail.

### Conductor/Dispatch Pattern

- Workflows are the conductor; activities are the executors.
- Child workflows for isolated retry/failure handling.
- Signals for external input (approvals, decisions).

### Merge Gate Pattern

**Approval Pattern:**
1. Workflow blocks with a timeout using `wait_condition()`.
2. If an approver sends a Signal before timeout, workflow processes the decision.
3. If timeout expires, workflow follows the timeout path (rejection or escalation).
4. Approval data (approver, reason, timestamp) is captured in workflow history.

**Server-validated merge gate (example from ship/ElMundiUA):**
- Verify at least one APPROVED review on the PR.
- Verify all completed head-SHA checks are success/skipped/neutral.
- Gate the `code_review → auto_merge` transition.
- Merge execution happens in an activity, never in workflow code.

### Budget/Quota

- Workflow timeouts, activity timeouts, retry policies.
- No built-in cost tracking for LLM calls.

### What's Reusable

- **Approval pattern** with Signal and timeout.
- Server-validated merge gate logic.
- Child workflows for isolated task execution.
- Durable timers for scheduled checks.

---

## 10. PR-Agent (Qodo)

**Repository:** https://github.com/qodo-ai/pr-agent  
**License:** Apache-2.0  
**Stars:** ~10K+

### Architecture

PR-Agent is an **AI PR reviewer** that runs as a GitHub Action or GitHub App:
- Auto-review, describe, and improve on PR events.
- Can handle `synchronize` events when enabled.
- Configurable via `.pr_agent.toml` or environment variables.

### Merge Gate Pattern

- PR-Agent can emit labels such as "possible security issue".
- Use a separate required GitHub Action that fails when those labels are present.
- Configure branch protection to require that check.
- For a stronger gate, make the review job itself a required check: fail it on blocking findings.

### Scheduling

- GitHub Action workflow triggers: `pull_request`, `pull_request_target`, `synchronize`.
- No built-in scheduler; relies on GitHub webhook events.

### Budget/Quota

- Not built-in; depends on the LLM provider.

### What's Reusable

- **Label-based merge gate:** emit labels, fail a check when blocking labels are present.
- Review job as a required check pattern.
- Modular tools: `/describe`, `/review`, `/improve`.

---

## 11. CodeRabbit-style OSS Alternatives

### OpenRabbit
**Repository:** https://github.com/aryanbrite/openrabbit  
**License:** MIT

- Runs entirely in GitHub Actions — no server.
- Pluggable LLM provider (Groq, OpenRouter).
- Posts summary and inline comments.

### Robin Review
**Repository:** https://www.robinreview.dev  
**License:** MIT

- BYOK (bring your own key) — OpenAI-compatible endpoints.
- Severity-tiered review comments (High / Medium / Suggestion).
- No vendor account, no per-seat pricing.

### Kodus
**Repository:** https://github.com/kodustech/kodus-ai  
**License:** AGPL

- Full self-hosted platform (Docker/Helm).
- Supports GitHub, GitLab, Bitbucket, Azure DevOps, Forgejo.
- Unlimited PRs with your own API key.

### Hawk
**Repository:** https://github.com/mturac/hawk  
**License:** MIT

- GitHub Action + Web Dashboard.
- Multi-LLM support including Ollama (local/offline).
- Security, bug, performance, style, and test coverage review.

### What's Reusable

- GitHub Action workflow templates for PR review.
- BYOK pattern — no vendor lock-in.
- Severity-tiered findings.

---

## 12. NATS JetStream Work Queue Patterns

**Documentation:** https://docs.nats.io  
**License:** Apache-2.0

### Architecture

NATS JetStream provides:
- **WorkQueue retention:** message deleted on ack.
- **Pull consumers:** workers fetch messages; server enforces `max_ack_pending`.
- **KV buckets:** for leases with TTL and compare-and-set.
- **Deduplication:** via `Nats-Msg-Id` header.

### Scheduling (NATS 2.14+)

- **Recurring schedules:** publish once with `Nats-Schedule: "@hourly"` or cron expression.
- Server handles repetition — no client-side timer needed.
- Can combine with TTL to stop scheduling after a period.

### Work Queue Pattern

1. Create a WorkQueue stream with subjects `jobs.>`.
2. Create a durable pull consumer `workers` with `ack_policy=explicit`, `max_ack_pending=N`.
3. N workers bind to the same consumer; server hands each job to exactly one worker.
4. Workers ack after processing; unacked jobs redeliver after `ack_wait`.
5. `max_deliver` controls retry limit; exhausted jobs can be moved to a DLQ.

### Lease Pattern (already in StoryPool)

- KV bucket `LANE_LEASES` with TTL.
- `create` succeeds only if key is absent (atomic).
- Revision as fencing token; `renew`/`release` are compare-and-set.
- Lease service runs on the hub; hosts never write `$KV` directly.

### What's Reusable

- **Server-side recurring schedules** — eliminates the need for a conductor to maintain timers.
- **Pull consumer with max_ack_pending** — server enforces concurrency cap.
- **Lease service pattern** — already implemented in StoryPool.

---

## Summary: Reusable Projects with Licenses

| Project | License | What to Reuse |
|---------|---------|---------------|
| **OpenHands** | MIT | Event-sourcing, skill injection, workflow-as-tool |
| **Open SWE / LangGraph** | MIT | Deterministic cron fallback, read-only reviewer, checkpointing |
| **Aider** | Apache-2.0 | Three-tier model routing, cost tracking |
| **Goose** | Apache-2.0 | State machine with ordered operations, persisted scheduler |
| **CrewAI** | MIT | Flows for deterministic + agentic, token spend optimization |
| **PR-Agent** | Apache-2.0 | Label-based merge gate, review as required check |
| **Kodus** | AGPL | Full self-hosted PR review platform |
| **Robin Review / OpenRabbit** | MIT | GitHub Action PR review templates |
| **NATS JetStream** | Apache-2.0 | Server-side recurring schedules, pull consumer pattern |
| **Temporal** | MIT (SDK) | Approval pattern with Signals, server-validated merge gate |

---

## Key Patterns for StoryPool Migration

### 1. Deterministic Scheduler (replaces Grok hourly conductor)

- **NATS 2.14 recurring schedules** or **cron/systemd/launchd** for fixed cadences.
- Scheduler publishes to work queues; workers pull and ack.
- No LLM for scheduling — deterministic cron expressions.

### 2. Web Bob Conductor (replaces Grok "Bob")

- **LangGraph-style state graph** or **CrewAI Flow** with Python routing.
- Gemini API for judgment calls (what work runs next, priority decisions).
- Deterministic checks first (queue depths, machine health, pending reviews), LLM only for ambiguity.

### 3. Lane Dispatcher (replaces Grok lane fill)

- Already mostly deterministic in StoryPool (`cluster/lanes.py`).
- Move fill logic to a Python script triggered by cron/NATS schedule.
- LLM not needed — dispatch is capacity-based.

### 4. Karen Ship Gate (replaces Grok QA/security gate)

- **Claude API** for PASS/FAIL verdict on PR diff.
- **Server-validated merge gate:** verify GitHub review state + CI status before allowing merge.
- **Exact head SHA check:** gate must PASS at the exact SHA being merged.
- Deterministic fallback: if Claude is unavailable, queue for human review.

### 5. Ken Second Judge (new MiniMax role)

- Cheap second opinion on borderline cases.
- Only invoked when Karen returns NEEDS_REVIEW or budget allows.

### 6. Budget/Quota Tracking

- Per-provider token/cost tracking at the application level.
- Hard caps enforced by the scheduler before dispatching work.
- Grok budget tracked and kept at ~0.

---

## References

1. OpenHands SDK Paper: https://arxiv.org/html/2511.03690
2. Open SWE Architecture: https://github.com/langchain-ai/open-swe/blob/main/openwiki/runtime-architecture.md
3. Aider Architecture: https://deepwiki.com/Aider-AI/aider/2-core-architecture
4. Goose State Machine: https://spiralxyz.substack.com/p/the-agentic-state-machine
5. CrewAI Flows: https://docs.crewai.com/edge/en/concepts/flows
6. LangGraph Overview: https://docs.langchain.com/oss/python/langgraph/overview
7. Temporal Approval Pattern: https://docs.temporal.io/design-patterns/approval
8. PR-Agent Docs: https://github.com/qodo-ai/pr-agent/blob/main/docs/docs/installation/github.md
9. NATS JetStream Scheduling ADR: https://github.com/nats-io/nats-architecture-and-design/blob/main/adr/ADR-51.md
10. NATS Worker Pool: https://docs.nats.io/learn/jetstream/worker-pool
