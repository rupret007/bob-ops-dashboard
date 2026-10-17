# Quality and spend scorecards (draft)

**Not enabled; Karen gate before live.** No collection schedule, service, broker
configuration or Pages refresh was enabled. Pages remains on its existing 15-minute
refresh. No generated index/status, history snapshots or per-sample commits are
included. Both briefs share the new branch and one draft PR.

`scorecards.py` computes rolling `(now-window, now]` UTC windows of 24 hours and
7 days, with separate provider and repository groups plus machine idle slots.
The static Usage & machines renderer includes the scorecards. `scorecard_sources.py`
is a manually invoked read-only collector; it prints aggregate JSON or `--html`.
Its required arguments are `--sessions <session-root>` and `--logs <lane-log-root>`;
optional `--notes <existing-conductor-notes>`, repeatable `--repo owner/repo`, and
repeatable `--karen-login <verified-reviewer-login>` scope the evidence reads.
No default reviewer identity is guessed. Only explicitly trusted authors' comments
and review submissions are parsed. All gh calls use `~/.local/bin/gh` and GET.
The existing publication loader can read an optional `scorecards.json` aggregate;
this PR does not create that file or connect the collector to a scheduler.

## Evidence and unavailable data

Only approved existing sources are read: session JSONL, `gemini-spend.txt`,
`provider-usage.tsv`, configured conductor notes, and GitHub PR/CI data. No auth
files, chat databases, credential discovery, NATS writes or new logging are needed.
Outputs are numeric aggregates and allowlisted labels, never raw notes, log lines,
job identifiers, session content, machine addresses or credentials.

- Codex tokens use explicit per-event `last_token_usage.total_tokens` and deduplicate
  repeated cumulative/per-event observations within a session. Cumulative totals
  alone cannot establish a rolling-window spend and remain unknown. Dollar cost
  remains no data. Weekly % requires a real `rate_limits` 10080-minute window,
  a valid observation timestamp and no elapsed reported reset.
- Gemini spend accepts existing timestamp/run/model/input/output/$cost lines or
  explicit `in=`, `out=`, `tokens=` fields. Missing tokens or dollars are null.
  No price tables, token-price conversion or estimate is used.
- Provider TSV baseline columns are timestamp, job, provider, model, optional policy.
  Such rows establish run observations only. Existing optional `key=value` columns
  (`tokens`, `usd`, `repo`, `usable_output`, `refused`, `output_bytes`) provide direct
  evidence when present. Cursor/Claude/MiniMax stay no data otherwise. Codex/Gemini
  TSV costs are not added to their primary logs, avoiding double-counting.
- Provider attribution requires an explicit `provider:<name>` PR label or a provider
  field in existing notes. Repository attribution requires an explicit repo field.
  Unattributed spend is included only in its provider total, never spread across
  repos. No provider is inferred from a model, author or PR title.
- Existing Karen `- #N SHA PASS/FAIL` lines and `KAREN pr=N sha=SHA PASS/FAIL` are
  recognized; note section timestamps or comment creation timestamps establish
  time. `repo=`, `provider=`, `at=`, `round=` are used only when present. Unknown
  historical rounds are not treated as first-try passes.
- Optional existing `SCORECARD {JSON}` evidence lines can contain a `review`, `pr`,
  `comparison` or `idle` record. This is a parser, not a request to write new notes.
  Review evidence has repo/pr/provider/sha/verdict/at and an explicit round or
  `history_complete:true`. PR evidence may include `first_push_green`,
  `head_changed_at`, `merged_at`, `reverted_at`, `rollback_observed:true` only when
  actually observed. Comparison evidence requires `adjudicated:true` and matching
  canonical blocker-ID lists `karen_blockers` and `gemini_blockers`; prose similarity
  and reviewer approvals alone cannot prove agreement. Idle evidence requires host,
  start/end timestamps and observed idle_slots. Without these facts, those metrics
  show no data. Fixtures illustrate the format; no fixture is published as real use.
- GitHub event history is finite. First-push CI requires the opening event's exact
  head SHA; earliest check run per app/name wins over reruns. All observed checks
  must conclude success. Pending/no check data stays unknown. This covers observed
  checks, not an assertion about branch-protection configuration. A missing event
  is not proof of a green first push. Head movement requires a push/open/synchronize
  event matching the current head SHA, never updatedAt or authored commit time.
- Rollbacks and idle slots cannot be inferred from a missing log or PR title. A
  directly observed negative rollback result is required to count a non-revert.
  GitHub read failures leave no-data fields rather than reporting zero activity.
- **Grok Bot (conserve-only)** appears in the collector, live view, usage panel and
  both scorecard windows. No direct source exists: it is explicitly **unknown**.
  Grok never participates in lanes, costs or provider totals; no numbers invented.

## Metric definitions

Karen first-try PASS % uses first review outcomes in the window where complete
history or round=1 is evidenced. Average rounds uses PRs reaching their first PASS
in the window. Distinct verdict observations are rounds; exact duplicate observations
are deduplicated. An explicit round is preferred. Incomplete history is excluded and
reported in observed/eligible coverage, never silently treated as round one.

First-push CI % uses PRs opened in the window with known first-head check results.
Revert/rollback % uses merges whose **48-hour observation period ends in the window**
(merge+48h); this allows a meaningful 24-hour mature cohort. A revert at exactly 48h
counts; later reverts do not. Immature merges are not negative results.

Gemini agreement is caught/(caught+missed+false alarms) over explicitly adjudicated
blocker sets. Empty matched sets count as agreement. Missed and false-alarm counts
are shown separately. Stuck PRs is the current open backlog with no measured head
movement for >=6h (same as-of backlog in both windows, including older PRs). Any
unknown open-head age makes the total unknown/partial rather than understating it.

Spend sums require all observed rows in the group/window to have the field; any
missing field makes that sum and its ratios unknown/partial. No observations is
no data, not zero. Cost/tokens per merge or Karen PASS divide window totals by the
same group's merges/first passes in the window. These are window ratios, not causal
attribution of a job's cost to a PR; zero denominators are no data. Grok is excluded.

Wasted runs count explicit usable_output=false; empty Gemini outputs require
output_bytes=0; refusals require refused=true. Counts show known/eligible coverage.
Idle slot hours integrate idle_slots × overlapping interval hours per machine,
clipping to the window. Duplicate intervals are deduplicated; overlapping distinct
intervals return no data rather than double-counting. Missing sampling intervals
are not filled. Idle is observed time, not a claim of full-window coverage.

## Status thresholds

These fixed review/triage thresholds are **not budgets, targets or estimates**.
Both windows use the same bounds so sustained high volume is visible. Bounds are
inclusive; beyond the watch boundary is bad. No-data and partial coverage are
always displayed separately; unknown never receives a green status.

| Metric | Good | Watch | Bad |
| --- | --- | --- | --- |
| Karen first-try PASS % | >=90 | >=70 | <70 |
| Average rounds to PASS | <=1 | <=2 | >2 |
| CI green on first push % | >=95 | >=80 | <80 |
| Reverts within 48h % | 0 | <=5 | >5 |
| Gemini blocker agreement % | >=90 | >=70 | <70 |
| Gemini missed / false alarms (each count) | 0 | <=1 | >1 |
| Stuck PR count | 0 | <=2 | >2 |
| Tokens in window | <=10,000,000 | <=100,000,000 | >100,000,000 |
| Dollars in window | <=25 | <=100 | >100 |
| Codex weekly usage % | <=50 | <=80 | >80 |
| Tokens per merge / PASS | <=1,000,000 | <=5,000,000 | >5,000,000 |
| Dollars per merge / PASS | <=5 | <=20 | >20 |
| Wasted runs | 0 | <=2 | >2 |
| Empty Gemini outputs / refusals (each) | 0 | <=1 | >1 |
| Idle slot hours per machine | <=1 | <=6 | >6 |

`test_scorecards.py` covers every computation, no-data paths, every threshold
boundary, rolling boundaries, group isolation, mature revert cohorts, incomplete
rounds, stale head evidence, idle overlap, reset expiry, trusted comments, earliest
CI attempts, source parsing and safe rendering. CI runs these plus usage regression
fixtures. Real NATS tests remain in the strict cluster suite.
