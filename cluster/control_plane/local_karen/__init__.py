"""Local Karen: ship gate that runs on Jeff's Mac mini (or NucBox WSL).

Primary reviewer: Codex CLI (via CODEX_HOST=nuc, $200 plan)
Fallback reviewer: Gemini API (prepaid)
Second judge: MiniMax (borderline/high-risk tiers)
Last resort: Claude (off by default, capped)

INVARIANTS:
- NEVER merge, push, force-push, tag, release, or use sudo
- Verdict for a stale SHA is invalid
- Fail closed on any error
"""

__version__ = "0.1.0"
