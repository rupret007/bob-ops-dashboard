"""StoryPool Control Plane — deterministic scheduler with routed LLM calls.

This module replaces the Grok-based chat bots (Bob conductor, lane dispatcher,
Karen ship gate, scheduled routines) with a mostly-deterministic architecture
that uses LLM calls only where judgment is needed.

Components:
- scheduler: Cron/NATS schedule management (no LLM)
- dispatcher: Lane fill logic (5% LLM for priority ambiguity)
- gate_runner: Claude gate + Ken second judge (30% LLM for diff judgment)
- budget_tracker: Per-provider usage tracking (no LLM)
- guards: Machine health checks (no LLM)
- llm_router: Cost-tiered LLM routing

See docs/design/storypool-control-plane.md for the full design.
"""

__version__ = "0.1.0"
