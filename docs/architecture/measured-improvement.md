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
sessions and the five most frequent classified failure causes. Unclassified
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
