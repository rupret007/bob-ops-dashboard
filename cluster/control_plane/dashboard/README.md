The top engine panel polls `/api/engine` every three seconds, using a two-second
in-process cache. It reads bounded ledger tails and verdict files; it never calls
GitHub, a shell, an LLM, or another machine. The existing `/api/data` route remains
available. SVG needles animate locally and respect reduced-motion preferences.

Verified jobs/hour and fix success use terminal, verified ledger outcomes for
this machine; running lanes require a current lease. PASS and review rates use
verdict timestamps in the last hour, ignoring LATEST symlinks. Backlog uses
`backlog.json` or the conductor's last `total_open_prs` metric. Unknown inputs
show an em dash. Merges today means UTC today; the gate's cap is rolling 24 hours.

Health badges use watchdog checks less than 30 minutes old. Remote evidence can
be synced read-only into `machines/mini/watchdog.log` or
`machines/nucbox/watchdog.log` under the StoryPool data directory. Missing or
stale evidence is yellow, failed fresh checks red. The endpoint never infers
remote health from the current machine's load. `MONITOR.md` and activity strings
are rendered as text, never HTML. Dashboard assets ship with self-update's
existing package copy on both machines.

The engine turns green only with a completed, full-SHA Karen verdict and a
verified successful job in the last 60 minutes, and no stale active NEEDS_JEFF
entries. Errors, unknown heads, unverified jobs and future dates cannot prove
flow. Dated active entries in the root and conductor NEEDS_JEFF files become
stale after 60 minutes; resolved archives and explicitly resolved lines are
excluded. Fresh peer-local evidence is synced without echoing cluster totals;
missing peer flow evidence is yellow. The reason is rendered as plain text.
Existing red service failures remain red and machine badges stay independent.

A failed latest peer sync or a snapshot older than 15 minutes turns an otherwise
green remote badge yellow. Its detail preserves the last reported green health,
so a sync outage is not described as a dead machine. A newer snapshot supersedes
an older sync failure; fresh reported machine failures remain red. The JSON
exposes `reported_state` and `sync_issue` separately for that distinction.

Node maintenance controls use `/api/control/node` (POST only): JSON contains
`machine` (`mini` or `nucbox`), boolean `maintenance`, and literal `confirm: true`.
A same-origin browser confirmation is required. Requests must originate from
loopback or the tailnet. The state persists until an explicit return to service.
Running work finishes; new work is deferred. HANDOFF.md supplies the audit panel.
Peer controls require the peer dashboard to have received this deployment.
