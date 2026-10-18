# StoryPool Control Plane Architecture

## Core Design Principle: Outcome-Verified

**Every component reports real outcomes, not intents.**

The StoryPool control plane is built around the principle that logging an
intent is not the same as executing that intent. A "Dispatched: 3" log
message means nothing if no worker processes were actually spawned. A
"Self-update attempted" log is useless if HEAD never moves.

### What This Means

1. **Self-Update**: Every self-update cycle logs one of three outcomes:
   - `UPDATED: X -> Y` when HEAD actually changed
   - `SKIPPED: <reason>` when no update was needed or possible
   - `FAILED: <error>` when something went wrong

2. **Job Dispatch**: Jobs are tracked in a ledger from dispatch to verified
   completion:
   - Jobs have a **lease** that expires after 45 minutes
   - Dispatcher **must not re-dispatch** a PR that holds an active lease
   - Jobs count as **done only when verified**: a commit was pushed to the
     PR branch, or a Karen verdict was posted
   - Worker processes are **actually spawned** with tracked PIDs

3. **Dashboard & Digest**: Read only from the job ledger and verdict files,
   never from intent logs or dispatch counters.

### The Job Ledger

Location: `~/.local/share/storypool/jobs/ledger.jsonl`

Each job record contains:
- `job_id`: Unique identifier
- `repo`, `pr_number`, `head_sha`: PR identification
- `provider`: codex, cursor, etc.
- `machine`: mini, nucbox
- `state`: queued, running, done, failed, expired
- `lease_acquired`, `lease_expires`: Lease timestamps
- `pid`, `start_time`, `end_time`: Process tracking
- `outcome`, `outcome_verified`, `outcome_sha`: Result verification

### State Transitions

```
queued ─────────────┬─> running ─────────┬─> done (verified)
                    │                    │
                    │                    └─> failed (error)
                    │
                    └─> expired (lease timeout or process died)
```

### Outcome Verification

A job's outcome is verified when:
1. **Commit pushed**: The PR's HEAD SHA changed from the job's `head_sha`
2. **Verdict posted**: A Karen verdict file exists for this PR at the job's SHA

### Self-Update Log

Location: `~/.local/share/storypool/self-update.log`

Each update attempt produces a JSONL entry with:
- `timestamp`: When the attempt occurred
- `outcome`: UPDATED, SKIPPED, or FAILED
- `old_head`, `new_head`: SHA transitions (for UPDATED)
- `reason`: Why it was skipped or failed

### Watchdog HEAD Lag Detection

The watchdog alerts when:
- HEAD lags behind origin by more than 30 minutes
- The last self-update outcome was not UPDATED

This catches silent self-update failures that would otherwise go unnoticed.

## Component Overview

### Mini (macOS)
- **Karen Poller**: Reviews PRs, posts verdicts (launchd)
- **Dashboard**: Web UI on port 8790 (launchd)
- **Watchdog**: Health checks every 10 min (launchd)

### NucBox (Linux WSL)
- **Dispatcher**: Allocates work to lanes (systemd)
- **Conductor**: Orchestrates LLM-assisted fixes (systemd)
- **Dashboard**: Web UI on port 8790 (systemd)
- **Watchdog**: Health checks every 10 min (systemd)

### Data Flow

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   GitHub    │────>│  Dispatcher  │────>│  Job Ledger │
│    PRs      │     │  (NucBox)    │     │  (verified) │
└─────────────┘     └──────────────┘     └─────────────┘
                          │                     │
                          v                     v
                    ┌──────────────┐     ┌─────────────┐
                    │   Codex/     │────>│  Verdicts   │
                    │   Cursor     │     │  (Karen)    │
                    └──────────────┘     └─────────────┘
                                                │
                                                v
                                         ┌─────────────┐
                                         │  Dashboard  │
                                         │  Digest     │
                                         └─────────────┘
```

## Provider Allocation

| Provider | Machine  | Lanes | Weekly Cap |
|----------|----------|-------|------------|
| Cursor   | any      | ∞     | none       |
| Codex    | NucBox   | 2     | 75%        |
| Claude   | any      | 1     | none       |
| MiniMax  | any      | 1-2   | none       |
| Gemini   | any      | ∞     | judgment   |
| Grok     | never    | 0     | ~$0        |

## Safety Invariants

- No sudo
- No force-push
- No tags/releases
- Never touch ~/.cursor, auth files, Keychain, or svc-* screens
- Never auto-merge rad-dad-show-night or Hang Out
