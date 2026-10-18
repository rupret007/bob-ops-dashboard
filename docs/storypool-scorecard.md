# StoryPool Scorecard: Local vs Grok-Hosted

This document defines concrete metrics that prove local services are **better**
than the Grok-hosted versions.

## Summary Table

| Service | Metric | Grok Baseline | Local Target | Measurement |
|---------|--------|---------------|--------------|-------------|
| Karen Gate | First-try pass rate | ~60% | ≥75% | PRs passing on first Karen review |
| Karen Gate | Pre-LLM catch rate | 0% | ≥20% | Issues caught by static scan before LLM |
| Karen Gate | False positive rate | ~15% | ≤10% | FAILs overturned by manual review |
| Conductor | Time-to-merge (median) | ~4h | ≤2h | Ready-to-merge → merged |
| Conductor | Fix-loop success rate | N/A | ≥50% | Codex fixes that pass Karen |
| Dispatcher | Stuck PR rate | ~10% | ≤5% | PRs >24h without progress |
| Digest | Week-over-week trend | N/A | Visible | Metrics vs previous week |

## Detailed Metrics

### Karen Gate

#### 1. Pre-LLM Static Scan (NEW - Grok doesn't have this)

Local Karen runs these checks BEFORE any LLM review:
- **Repo test execution**: Clone, run tests at head SHA in temp worktree
- **Static security scan**: secrets, unsafe shell, XSS, injection patterns
- **Test integrity**: deleted test files, weakened assertions

This catches issues without spending LLM tokens.

**Metric**: `pre_llm_catch_rate` = (issues caught by static scan) / (total issues)
**Target**: ≥20% of rejections from static scan alone

#### 2. First-Try Pass Rate

PRs that pass Karen on first review (no fix loop needed).

**Metric**: `first_try_pass_rate` = (PRs passing first review) / (total PRs reviewed)
**Target**: ≥75% (Grok: ~60%)

#### 3. False Positive Rate

Karen FAILs that get manually overridden or were incorrect.

**Metric**: `false_positive_rate` = (overturned FAILs) / (total FAILs)
**Target**: ≤10% (Grok: ~15%)

### Conductor

#### 1. Time-to-Merge

Time from PR becoming merge-ready to actually merged.

**Metric**: `median_time_to_merge` in hours
**Target**: ≤2h median (Grok: ~4h)

#### 2. Fix-Loop Success Rate

Codex fix attempts that result in Karen PASS.

**Metric**: `fix_loop_success_rate` = (successful fixes) / (fix attempts)
**Target**: ≥50% (Grok: N/A - no fix loop)

#### 3. Throughput

PRs processed per day.

**Metric**: `daily_throughput` = PRs merged + PRs reviewed
**Target**: Track trend, no regression from Grok

### Dispatcher

#### 1. Stuck PR Rate

PRs with no progress for >24 hours.

**Metric**: `stuck_pr_rate` = (stuck PRs) / (total open PRs)
**Target**: ≤5% (Grok: ~10%)

#### 2. Lane Utilization

How efficiently lanes are used.

**Metric**: `lane_utilization` = (active jobs) / (available lanes)
**Target**: ≥60% during work hours

### Backlog

#### 1. Backlog Age

Median age of items in backlog.

**Metric**: `median_backlog_age` in hours
**Target**: ≤8h (Grok: ~12h)

### Digest

#### 1. Week-over-Week Comparison

Every digest includes comparison to previous week.

**Metrics shown**:
- First-try pass rate: this week vs last week
- Time-to-merge: this week vs last week
- Fix-loop success: this week vs last week
- Throughput: this week vs last week

**Target**: Visible trend, not just snapshot

## Metrics Storage

All metrics stored in JSON files:
- `~/.local/share/storypool/metrics/conductor-metrics.json`
- `~/.local/share/storypool/metrics/karen-metrics.json`
- `~/.local/share/storypool/metrics/weekly-summary.json`

## Reporting

1. **Real-time**: `conductor-status.json` includes current metrics
2. **Daily**: Digest includes 24h metrics
3. **Weekly**: Sunday digest includes week-over-week comparison
4. **On-demand**: `karen-gate --metrics` shows accumulated stats

## Definition of "Better"

A local service is **better** than Grok-hosted when:

1. **Faster**: Lower time-to-merge, faster reviews
2. **Smarter**: Pre-LLM static scan catches issues without tokens
3. **More reliable**: Higher first-try pass rate, lower false positives
4. **More capable**: Fix loop (Grok doesn't have), metrics tracking
5. **More transparent**: Week-over-week trends, not just snapshots

## Parity Baseline

Before claiming "better", must achieve ≥90% parity with Grok-Karen:
- Same verdicts on same PRs
- Run `parity/karen_parity.py` to verify

Current parity: (run test to fill in)
