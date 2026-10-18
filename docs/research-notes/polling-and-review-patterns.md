# Polling and Review Patterns Research

Research conducted October 2026 on PR-Agent, OpenHands, and Goose for StoryPool Karen gate improvements.

## Summary

Analyzed three production-grade systems for proven patterns in:
- launchd/systemd PATH handling
- Polling mechanisms
- Auto-review gating
- Retry and backoff strategies

## Patterns Adopted

### 1. From PR-Agent: Finding State Reconciliation

**Source**: `pr_agent/algo/review_finding_state.py`

PR-Agent maintains persistent finding state across reviews with a reconciliation mechanism that:
- Normalizes findings to stable fingerprints (path + body hash)
- Tracks ACTIVE vs RESOLVED states
- Preserves findings across review runs
- Limits resolved history to prevent unbounded growth (`DEFAULT_MAX_RESOLVED_FINDINGS = 20`)

**Adopted for Karen**: The gate now tracks verdict history per PR with a state file that persists across poll cycles. This prevents the "flapping" bug where a PR kept re-entering the queue after a gate crash.

Key pattern:
```python
@dataclass(frozen=True)
class ReconciliationResult:
    state: dict[str, Any]
    changed: bool
    resolved_ids: tuple[str, ...]
    reopened_ids: tuple[str, ...]
```

### 2. From PR-Agent: Notification-Based Polling with Deduplication

**Source**: `pr_agent/servers/github_polling.py`

PR-Agent polls GitHub notifications with:
- `handled_ids` set for deduplication
- Multiprocessing for parallel task dispatch
- Capacity checks before spawning workers
- Comment pagination drift detection (retries when comments change mid-scan)

**Adopted for Karen**:
- Poll state tracks `prs_checked` to avoid re-running gate on same SHA
- Exception handling marks PRs as `karen-fail` and removes label (fail-closed)
- Backlog report tracks all PR states including `draft_green` for auto-undraft

Key pattern from PR-Agent:
```python
if comment['id'] in handled_ids:
    return False, handled_ids
else:
    handled_ids.add(comment['id'])
```

### 3. From Goose: Exponential Backoff with Jitter

**Source**: `goose-provider-types/src/retry.rs`

Goose implements production-grade retry configuration:
```rust
pub const DEFAULT_MAX_RETRIES: usize = 3;
pub const DEFAULT_INITIAL_RETRY_INTERVAL_MS: u64 = 1000;
pub const DEFAULT_BACKOFF_MULTIPLIER: f64 = 2.0;
pub const DEFAULT_MAX_RETRY_INTERVAL_MS: u64 = 30_000;
```

Critical innovation: **jitter to avoid thundering herd**:
```rust
let jitter_factor = 0.8 + (rand::random::<f64>() * 0.4);
let jitter_delay_ms = (capped_delay_ms as f64 * jitter_factor) as u64;
```

Also: **transient-only retry** mode that skips 4xx client errors but retries 5xx/network errors.

**Adopted for StoryPool**:
- Self-update uses exponential backoff for git fetch (4s, 8s, 16s, 32s)
- Conductor LLM fallback: Codex primary → Gemini fallback with clear error reasons
- Gate errors result in fail-closed (label removed, karen-fail applied)

### 4. From OpenHands: React Query Polling with Status Checks

**Source**: `src/hooks/query/use-task-polling.ts`

OpenHands uses React Query with dynamic refetch intervals:
```typescript
refetchInterval: (query) => {
  const task = query.state.data;
  if (task?.status === "READY" || task?.status === "ERROR") {
    return false;  // Stop polling
  }
  return 3000;  // Poll every 3 seconds
},
retry: false,
```

**Adopted for Karen**:
- Poll cycle stops processing a PR once verdict is reached
- Label removal happens atomically with verdict labeling
- Backlog report includes state machine for PR lifecycle: `queued → under_review → passed/failed`

### 5. From PR-Agent: Error Classification and Safe Failure Messages

**Source**: `pr_agent/tools/pr_reviewer.py`

PR-Agent classifies errors into actionable categories:
```python
_REVIEW_FAILURE_REASONS = (
    (("credit balance is too low", ...), "insufficient credits..."),
    (("ratelimiterror", ...), "rate-limited..."),
    (("context_length_exceeded", ...), "exceeded context limit..."),
)
```

**Adopted for Karen**:
- Gate errors now produce actionable `karen-fail` labels
- Conductor logs explicit reasons when Codex/Gemini unavailable
- Error messages don't expose internal details

## PATH Handling

None of the three projects use launchd/systemd directly; they rely on:
- **PR-Agent**: Webhook server (no scheduled jobs)
- **OpenHands**: Web app (no scheduled jobs)
- **Goose**: Desktop app with bundled environment

**StoryPool Solution**: Dynamic PATH detection in install scripts:
```bash
detect_gh_path() {
    local gh_bin
    gh_bin=$(command -v gh 2>/dev/null || true)
    if [[ -n "$gh_bin" ]]; then
        dirname "$gh_bin"
    elif [[ -x "/opt/homebrew/bin/gh" ]]; then
        echo "/opt/homebrew/bin"
    fi
}
```

This is then injected into launchd plist templates and systemd environment files.

## Patterns NOT Adopted

1. **PR-Agent's multiprocessing**: StoryPool's poll cycle is serial by design to limit merge rate
2. **OpenHands' React Query**: StoryPool uses Python, not TypeScript
3. **Goose's async polling for OAuth**: StoryPool doesn't need OAuth device flow

## Implementation Status

| Pattern | Source | StoryPool Component | Status |
|---------|--------|---------------------|--------|
| Finding reconciliation | PR-Agent | `poller.py` exception handler | ✅ Implemented |
| Handled IDs dedup | PR-Agent | `poll_state.prs_checked` | ✅ Implemented |
| Exponential backoff | Goose | `self_update.py` git retry | ✅ Implemented |
| Status-based polling | OpenHands | PR state machine | ✅ Implemented |
| Error classification | PR-Agent | `karen-fail` labeling | ✅ Implemented |
| Dynamic PATH | Custom | Install scripts | ✅ Implemented |

## References

- PR-Agent: https://github.com/Codium-ai/pr-agent
- OpenHands: https://github.com/All-Hands-AI/OpenHands
- Goose: https://github.com/block/goose
