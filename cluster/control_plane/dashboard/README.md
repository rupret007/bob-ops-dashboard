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
