# StoryPool Control Plane Design

A mostly-deterministic scheduler that replaces the Grok-based chat bots (Bob conductor, lane dispatcher, Karen ship gate, scheduled routines) with LLM calls only where judgment is needed, routed by cost tier.

## 1. Current State: Grok-Based Control Plane

| Role | Current Implementation | Cost Problem |
|------|------------------------|--------------|
| **Bob (Conductor)** | Grok chat bot deciding what work runs next | Always-on LLM for scheduling decisions |
| **Lane Dispatcher** | Grok-driven refill of coding lanes | LLM for capacity-based dispatch |
| **Karen (Ship Gate)** | Grok QA/security bot that must PASS a PR at exact head SHA before merge | LLM for every gate check |
| **Scheduled Routines** | Grok hourly conductor, self-assessment, check-ins | LLM for cron-triggered tasks |

**Problem:** Grok usage is too expensive. All these roles can be mostly deterministic with LLM calls only for judgment.

## 2. Target Architecture

### 2.1 Service Budget Allocation

| Service | Budget | Role | Usage Pattern |
|---------|--------|------|---------------|
| **Gemini API** | ~$121 prepaid | Web Bob conductor brain | Judgment calls for work prioritization |
| **Codex** | $200 plan | Coding lanes (NucBox-only via Linux CLI in WSL) | Stop new lanes at 75% weekly |
| **Cursor** | $200 plan | Coding lanes | Primary coding agent |
| **Claude** | $20 plan | Karen-equivalent ship gate | PASS/FAIL verdicts on PRs |
| **MiniMax** | BYOK | Ken (cheap second judge) | Borderline cases, second opinion |
| **Grok** | ~$0 target | Emergency fallback only | Track usage, alert if > $1/week |

### 2.2 Infrastructure

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              NucBox (Hub)                                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ NATS Server │  │ Scheduler   │  │ Dispatcher  │  │ Gate Runner         │ │
│  │ JetStream   │  │ (cron/NATS) │  │ (Python)    │  │ (Claude/MiniMax)    │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────────┘ │
│         │                │                │                    │             │
│         └────────────────┼────────────────┼────────────────────┘             │
│                          │                │                                   │
│                    ┌─────▼────────────────▼─────┐                            │
│                    │      NATS Streams          │                            │
│                    │  JOBS, LANES, RESULTS, KV  │                            │
│                    └─────┬────────────────┬─────┘                            │
└──────────────────────────┼────────────────┼──────────────────────────────────┘
                           │                │
              ┌────────────┘                └────────────┐
              │                                          │
       ┌──────▼──────┐                           ┌───────▼───────┐
       │ NucBox      │                           │ Mac mini      │
       │ Worker      │                           │ Worker        │
       │ (Codex WSL) │                           │ (Cursor/      │
       │             │                           │  Claude)      │
       └─────────────┘                           └───────────────┘
```

### 2.3 Component Responsibilities

| Component | Deterministic | LLM Calls |
|-----------|---------------|-----------|
| **Scheduler** | 100% — cron/systemd/launchd + NATS schedules | None |
| **Dispatcher** | 95% — capacity checks, queue depths, health | 5% Gemini for priority ambiguity |
| **Gate Runner** | 70% — SHA check, CI status, review state | 30% Claude for diff judgment |
| **Ken Judge** | 0% | 100% MiniMax for second opinion |
| **Budget Tracker** | 100% — usage aggregation, cap enforcement | None |

## 3. Detailed Design

### 3.1 Scheduler (replaces Grok hourly conductor)

**Implementation:** Python script + cron/systemd timers OR NATS 2.14 recurring schedules.

```
# /etc/systemd/system/storypool-hourly.timer
[Unit]
Description=StoryPool hourly conductor

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

**Responsibilities:**
1. Trigger dispatcher fill check every hour
2. Trigger self-assessment daily at 6 AM CT
3. Trigger check-in routine at configured intervals
4. Publish schedule events to NATS `schedules.>` subjects

**No LLM calls.** The scheduler only decides *when* things run, not *what* runs.

### 3.2 Web Bob Conductor (replaces Grok "Bob")

**Implementation:** Python state machine triggered by schedule or webhook.

**Flow:**
```
┌─────────────────┐
│ Schedule/Event  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     No      ┌─────────────────┐
│ Capacity Check  │────────────►│ Skip (log why)  │
│ (deterministic) │             └─────────────────┘
└────────┬────────┘
         │ Yes
         ▼
┌─────────────────┐     Clear   ┌─────────────────┐
│ Priority Check  │────────────►│ Dispatch top N  │
│ (deterministic) │             │ (deterministic) │
└────────┬────────┘             └─────────────────┘
         │ Ambiguous
         ▼
┌─────────────────┐
│ Gemini Judgment │  ← Only LLM call in the conductor path
│ (priority tie)  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Dispatch work   │
└─────────────────┘
```

**Deterministic checks (no LLM):**
- Queue depths per lane type
- Machine health (load, swap, disk)
- Pending gate reviews
- Budget remaining per service
- Time since last lane fill

**LLM only for:**
- Ambiguous priority between equivalent candidates
- Novel situations not covered by rules

**Cost routing:**
```python
def conductor_judgment(context: dict) -> str:
    """Route conductor judgment to Gemini (prepaid, cheapest)."""
    return gemini_api.complete(
        model="gemini-2.0-flash",
        prompt=CONDUCTOR_PROMPT.format(**context),
        max_tokens=200
    )
```

### 3.3 Lane Dispatcher (replaces Grok lane fill)

**Implementation:** `cluster/dispatcher.py` — pure Python, no LLM.

**Algorithm:**
```python
def fill_lanes():
    """Deterministic lane fill — no LLM needed."""
    
    # 1. Check budget caps
    for provider in ['codex', 'cursor', 'claude']:
        if budget_tracker.weekly_pct(provider) >= 75:
            log(f"Skipping {provider}: at 75% weekly cap")
            continue
    
    # 2. Check machine health (Mini guards)
    if mini_load() >= 10:
        log("Mini load too high, skipping")
        return
    if mini_swap_pct() >= 80:
        log("Mini swap too high, skipping")
        return
    if mini_free_disk_gb() < 15:
        log("Mini disk too low, skipping")
        return
    
    # 3. Check NucBox capacity
    if nucbox_load() >= 12 or nucbox_lanes() >= 7:
        log("NucBox at capacity")
        # Can still fill Mini lanes
    
    # 4. Get pending work from GitHub
    candidates = get_pending_prs()
    
    # 5. Sort by deterministic priority
    #    - Blocked PRs first (Karen FAIL awaiting fix)
    #    - Oldest PRs second
    #    - Smallest diff third
    candidates.sort(key=priority_key)
    
    # 6. Dispatch to available lanes
    for candidate in candidates:
        lane = find_free_lane(candidate)
        if lane:
            enqueue_lane(lane, candidate)
```

**No LLM calls.** Dispatch is pure capacity + priority math.

### 3.4 Karen Ship Gate (replaces Grok QA/security gate)

**Implementation:** `cluster/gate_runner.py`

**Invariants:**
1. Gate must PASS at the **exact head SHA** being merged
2. PR must be **undrafted** before gate check
3. No **force-push** between gate PASS and merge
4. Gate never creates tags or releases
5. Gate never uses sudo
6. Gate never loosens or deletes tests
7. Gate never sends messages without Jeff's approval
8. Gate never commits or prints secrets

**Flow:**
```
┌─────────────────┐
│ PR ready for    │
│ merge (webhook) │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     No      ┌─────────────────┐
│ Draft check     │────────────►│ BLOCK: undraft  │
│ (deterministic) │             │ first           │
└────────┬────────┘             └─────────────────┘
         │ Undrafted
         ▼
┌─────────────────┐     Fail    ┌─────────────────┐
│ CI status check │────────────►│ BLOCK: CI must  │
│ (deterministic) │             │ pass            │
└────────┬────────┘             └─────────────────┘
         │ Pass
         ▼
┌─────────────────┐     Old     ┌─────────────────┐
│ Head SHA check  │────────────►│ BLOCK: gate     │
│ (deterministic) │             │ stale           │
└────────┬────────┘             └─────────────────┘
         │ Current
         ▼
┌─────────────────┐
│ Claude Review   │  ← LLM call for diff judgment
│ (PASS/FAIL)     │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌───────┐  ┌───────┐     Maybe   ┌─────────────────┐
│ PASS  │  │ FAIL  │────────────►│ Ken second      │
└───┬───┘  └───┬───┘             │ judge (MiniMax) │
    │          │                 └────────┬────────┘
    │          │                          │
    ▼          ▼                          ▼
┌───────┐  ┌────────────┐        ┌─────────────────┐
│ Allow │  │ Block      │        │ PASS or FAIL    │
│ merge │  │ with note  │        │ (tiebreaker)    │
└───────┘  └────────────┘        └─────────────────┘
```

**Claude prompt:**
```python
GATE_PROMPT = """You are Karen, a QA and security gate for {repo}.

PR #{pr_number}: {title}
Head SHA: {sha}
Author: {author}

## Diff
{diff}

## Existing tests
{test_files}

## CI status
{ci_status}

Review this PR for:
1. Security vulnerabilities (injection, auth bypass, secrets)
2. Test coverage (are new paths tested?)
3. Code quality (obvious bugs, edge cases)

Respond with exactly one of:
- PASS: ready to merge
- FAIL: {reason}

If borderline, lean toward FAIL with a clear reason.
"""
```

**Ken (MiniMax) prompt:**
```python
KEN_PROMPT = """Karen gave a borderline verdict on this PR.

Karen's verdict: {karen_verdict}
Karen's reason: {karen_reason}

PR diff:
{diff}

As a second judge, do you agree with Karen?
Respond with: AGREE or DISAGREE with one sentence reason.
"""
```

**Merge validation:** `cluster/control_plane/gate_runner.py` requires an explicit
PASS result for the same PR and exact head SHA, a non-draft PR, nonempty passing
CI results, and an independent approval at that SHA. Each reviewer's latest
decision applies; outstanding changes requested block validation. Callers must
fetch fresh GitHub state and supply the PR author before invoking validation.

The primary review must see the complete diff. Missing or oversized diffs and
ambiguous model responses require human review. Ken is advisory and cannot turn
an incomplete or uncertain primary review into PASS.

### 3.5 Budget Tracker

**Implementation:** `cluster/budget_tracker.py`

**Data model:**
```python
@dataclass
class ProviderUsage:
    provider: str  # codex, cursor, claude, minimax, gemini, grok
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    window_start: datetime = field(default_factory=datetime.utcnow)

@dataclass 
class BudgetCaps:
    codex_weekly_pct: float = 75.0  # Stop at 75% of $200 weekly
    cursor_weekly_pct: float = 75.0
    claude_weekly_usd: float = 20.0  # Hard cap
    minimax_weekly_usd: float = 10.0  # BYOK, set low
    gemini_prepaid_usd: float = 121.0  # Prepaid balance
    grok_weekly_usd: float = 1.0  # Near-zero target
```

**Enforcement:**
```python
def can_use_provider(provider: str) -> tuple[bool, str]:
    """Check if provider is within budget caps."""
    usage = get_weekly_usage(provider)
    caps = get_budget_caps()
    
    if provider == 'codex':
        pct = usage.cost_usd / (200.0 / 4) * 100  # Weekly portion
        if pct >= caps.codex_weekly_pct:
            return False, f"Codex at {pct:.1f}% of weekly cap"
    
    if provider == 'grok':
        if usage.cost_usd >= caps.grok_weekly_usd:
            alert(f"Grok usage ${usage.cost_usd:.2f} exceeds target")
            return False, "Grok budget exhausted"
    
    # ... similar for other providers
    return True, "ok"
```

### 3.6 Machine Guards

**Mini guards (from ARCHITECTURE.md):**
```python
def mini_guards_pass() -> tuple[bool, str]:
    """Check Mini guards before accepting work."""
    load1, _, _ = os.getloadavg()
    if load1 >= 10:
        return False, f"load {load1:.1f} >= 10"
    
    swap = get_swap_usage()
    if swap.pct >= 80:
        return False, f"swap {swap.pct:.1f}% >= 80%"
    
    disk = shutil.disk_usage('/')
    free_gb = disk.free / (1024**3)
    if free_gb < 15:
        return False, f"disk {free_gb:.1f}GB < 15GB"
    
    return True, "ok"
```

**NucBox guards:**
```python
def nucbox_guards_pass() -> tuple[bool, str]:
    """Check NucBox guards — up to 7 lanes at load < 12."""
    load1, _, _ = os.getloadavg()
    if load1 >= 12:
        return False, f"load {load1:.1f} >= 12"
    
    active_lanes = count_active_lanes('nucbox')
    if active_lanes >= 7:
        return False, f"lanes {active_lanes} >= 7"
    
    return True, "ok"
```

## 4. Phased Migration Plan

### Phase 0: Preparation (Jeff-only)

The operator provisions provider access outside the published checkout through
the approved private configuration workflow. Shadow mode uses existing read-only
GitHub access and does not need provider credentials. Installers check access
without changing authentication. Credential values must stay out of command
arguments, URLs, reports, and tracked files.

### Phase 1: Shadow Mode (1-2 days)

**What changes:**
- New scheduler runs alongside Grok bots
- Shadow runner logs what it *would* dispatch/gate to JSONL
- Gate runner logs verdicts without blocking merges
- Shadow reads usage observations; missing observations are logged as skips

**Grok usage:** Unchanged (still authoritative)

**Validation:**
- Compare deterministic decisions with Grok decisions via daily compare script
- Tune priority weights based on shadow logs
- Identify edge cases before cutover

Shadow mode is limited to the authorized dashboard repository. Runtime state
and JSONL reports must be outside the checkout; Pages excludes operational code
and the design/research documents. Shadow reads budget state without creating or
updating it. Weekly budgets reset on Monday UTC; Gemini prepaid spend persists
across weeks. Missing usage observations block shadow dispatch and LLM review
instead of implying zero spend. Capacity uses live lane counts and limits each evaluation to the
remaining free slots. Skipped gates appear in comparison reports.

**Installation:**
```bash
# Mac mini (launchd, runs every 15 min)
bash cluster/control_plane/install/install-shadow-mini.sh

# NucBox WSL (systemd --user, runs every 15 min)
bash cluster/control_plane/install/install-shadow-nucbox.sh
```

**Daily comparison:**
```bash
python3 cluster/control_plane/shadow_compare.py --days 1
```

### Phase 2: Scheduler Cutover (1-2 days)

**What changes:**
- Disable Grok hourly conductor
- Enable systemd/cron scheduler
- Scheduler triggers dispatcher (still shadow)

**Grok usage:** Reduced ~30%

**Rollback:** Re-enable Grok hourly conductor

### Phase 3: Dispatcher Cutover (1-2 days)

**What changes:**
- Disable Grok lane dispatcher
- Enable Python dispatcher with Gemini for ambiguity
- Lanes fill from NATS queue

**Grok usage:** Reduced ~60%

**Rollback:** Re-enable Grok lane dispatcher

### Phase 4: Gate Cutover (2-3 days)

**What changes:**
- Disable Grok Karen gate
- Enable Claude gate runner
- Ken (MiniMax) for borderline cases
- Server-validated merge checks

**Grok usage:** Reduced ~95%

**Rollback:** Re-enable Grok Karen gate

### Phase 5: Cleanup (ongoing)

**What changes:**
- Remove Grok bot prompts and credentials
- Archive old gate result files
- Consolidate budget tracking
- Document new operational procedures

**Grok usage:** ~0% (emergency fallback only)

## 5. Operational Procedures

### 5.1 Daily Check

```bash
# Run on NucBox
storypool-status

# Output:
# Budget (weekly):
#   Gemini: $12.50 / $121.00 (10.3%)
#   Codex:  $45.00 / $50.00 (90%) ⚠️ approaching cap
#   Cursor: $30.00 / $50.00 (60%)
#   Claude: $8.00 / $20.00 (40%)
#   MiniMax: $2.00 / $10.00 (20%)
#   Grok:   $0.15 / $1.00 (15%) ✓
#
# Machine health:
#   Mini:   load 4.2, swap 45%, disk 120GB ✓
#   NucBox: load 8.5, lanes 5/7 ✓
#
# Gates:
#   Pending: 2
#   Passed (24h): 8
#   Failed (24h): 1
```

### 5.2 Alerts

| Condition | Severity | Action |
|-----------|----------|--------|
| Grok usage > $0.50/day | Warning | Review what triggered Grok |
| Grok usage > $1.00/day | Critical | Disable Grok fallback |
| Codex > 75% weekly | Info | Stop new Codex lanes |
| Mini load > 10 | Warning | Pause Mini lanes |
| Mini swap > 80% | Critical | Kill oldest lane, investigate |
| Mini disk < 15GB | Critical | Clean job directories |

### 5.3 Manual Override

```bash
# Pause all lanes
storypool-pause --all --reason "investigating issue"
```

Manual review must produce fresh evidence for the current head SHA and satisfy
the same validation checks. Budget and capacity checks still apply to dispatch.

## 6. File Layout

```
cluster/
├── control_plane/
│   ├── __init__.py
│   ├── scheduler.py      # Cron/NATS schedule management
│   ├── dispatcher.py     # Lane fill logic
│   ├── gate_runner.py    # Claude gate + Ken second judge
│   ├── budget_tracker.py # Per-provider usage tracking
│   ├── guards.py         # Machine health checks
│   └── llm_router.py     # Cost-tiered LLM routing
├── tests/
│   ├── test_scheduler.py
│   ├── test_dispatcher.py
│   ├── test_gate_runner.py
│   └── test_budget_tracker.py
└── config/
    ├── budget_caps.json
    └── provider_pricing.json
```

## 7. Success Criteria

| Metric | Current | Target |
|--------|---------|--------|
| Grok weekly spend | ~$50 | < $1 |
| Gate latency (P50) | 30s | < 20s |
| Gate accuracy | ~95% | >= 95% |
| Lane fill rate | 6/hour | >= 6/hour |
| False positive gates | 2/week | < 2/week |

## 8. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Claude gate misses security issue | Low | High | Ken second judge on borderline; human review on FAIL |
| Gemini prepaid runs out | Medium | Medium | Track usage, alert at 80% |
| Codex 75% cap hit early | Medium | Low | Prioritize high-value work |
| NATS queue backup | Low | Medium | Monitor queue depth, alert at 100 |
| Machine guard false positive | Low | Low | Log and tune thresholds |

## 9. Non-Goals (Explicit Exclusions)

- **No merge execution by gate:** Gate only PASSes or FAILs; human or existing automation merges.
- **No tag/release creation:** Out of scope for control plane.
- **No sudo operations:** All operations run as unprivileged user.
- **No test weakening:** Gate cannot suggest or approve test removal.
- **No external messaging:** No Slack/email without Jeff's approval.
- **No secret handling:** Gate never sees or logs credentials.
