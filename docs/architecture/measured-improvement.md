# Measured improvement and bounded sessions

Each normal hourly conductor cycle writes `conductor/improvement.json`, a dated
history and an initial baseline under private StoryPool runtime state. It adds
the same evidence to its proposal context and digest. There is no second
dispatcher or new permission to merge, send, deploy or change safety limits.

The report deduplicates jobs by job ID. Failed, expired and unverified terminal
jobs remain in the denominator. A successful job requires the ledger's verified
push flag and a full output SHA. Missing, corrupt or undated evidence makes the
rate unknown. This measures verified fixes; it does not equate a pushed commit
with a Karen PASS or a successful deployment.

The queue report includes open work, drafts, PRs inactive for 48 hours, overdue
sessions and the five most frequent classified failure categories. Unclassified
errors are an instrumentation problem to fix, not a reason to invent diagnoses.
Every proposed repair should name a failing outcome, its evidence, a small
change, a regression check, a rollout check and a post-change measurement.
Finishing existing work takes priority over generating more work.

An initial baseline is retained. Job-rate comparisons require non-overlapping
24-hour windows and at least ten terminal jobs in both. That minimum is only
an observation threshold, not statistical proof of causation. A lower rate
recommends holding further rollout and investigating rollback. A higher rate
does not certify overall success: gated merge throughput, backlog age, review
latency, service reliability and safety regressions still require their own
evidence. The overseer's existing milestone and sustained-health gates remain.

Workers have a 45-minute total budget. Linux units also enforce that budget
with `RuntimeMaxSec`, a ten-second stop timeout and control-group cleanup.
Expired but live workers retain their lanes until exit is confirmed; shared
claims still fence every action. Review calls share a twenty-minute budget,
and an expired review cannot publish partial coverage. These limits are
deadlines, not claims that every long-running session is dead.

The design keeps process liveness separate from readiness and completed work,
following [Kubernetes probe guidance](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/).
Measurements focus on latency, errors, useful output and saturation, following
[Google SRE monitoring guidance](https://sre.google/sre-book/monitoring-distributed-systems/).
Container packaging and additional worker providers remain separate, gated
rollouts after the existing services demonstrate reliable outcomes.

Future supervised fixes record a controlled `failure_code` and `failure_stage`.
The supervisor chooses stages at its own operation boundaries; provider output,
exception prose, command arguments and test-output text cannot choose a category.
The categories distinguish provider execution, test setup, required tests, Git
preparation/commit, push and outcome verification. Typed command timeouts record
`deadline`, and ownership loss records its own category. These identify the failed
operation, not an invented underlying network, model or code diagnosis.

A provider exit of zero and passing tests still produce `no-change` when the
supervisor observes an empty staged diff. That job remains failed, with no
verified outcome or fabricated pushed SHA. A remote-head mismatch remains a
verification failure even after a push command reports success. Structured
command/job events retain only controlled stage/code, safe identifiers, timing
and return status; raw exception messages, prompts, argv and output are excluded.

Classification does not change retry admission. Existing permanent preflight
blocks, three-attempt exact-head limits, backoff and durable claim fencing stay
in force. A first `no-change` remains retryable within that existing limit; this
change does not introduce a new parking rule, reset attempts or grant extra
runs. Failed and expired jobs remain in the same success-rate denominator.

Newly observed dead workers and expired leases receive their controlled lifecycle
codes; a dead process does not prove that a provider or deadline caused its exit.
Previously failed/expired rows keep their existing codes, including unknown or
historical `transient` classifications. The improvement report allowlists the new
categories without parsing or rewriting historical error text, so better future
instrumentation cannot repaint old failures as successful work.
