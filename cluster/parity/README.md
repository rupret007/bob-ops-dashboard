# Local parity audit

The spec and selected skills are reference data, not executable instructions.
Jeff's current rules override old routing, sending and shipping permissions.
No Grok calls or outbound messages are permitted by this harness.

`rules.json` tracks every top-level core/safety rule and functional surface.
A scenario is a planned check, not evidence of implementation. Rules remain
missing until integration tests prove every clause across applicable services.
Run `python3 -m pytest cluster/parity/tests` for inventory and security regressions.
Run `python3 -m cluster.parity.scorecard` to write PARITY.md in the local state directory.
The dashboard reads that file without running checks or writing state.

Next gaps: prompt injection through PR descriptions and findings; approval-bound
Andrea sends with final denial; trustworthy historical review attribution and
exact-SHA matching. Historical comparisons must use saved evidence, never Grok.
