"""Budget tracking for LLM providers.

Tracks per-provider usage (tokens, cost, calls) and enforces budget caps.
Designed to keep Grok usage near zero while routing to prepaid/cheaper services.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .codex_quota import read_codex_quota

PROVIDERS = ("codex", "cursor", "claude", "minimax", "gemini", "grok")

DEFAULT_CAPS = {
    "codex_weekly_pct": 75.0,
    "cursor_weekly_pct": 75.0,
    "claude_weekly_usd": 20.0,
    "minimax_weekly_usd": 10.0,
    "gemini_prepaid_usd": 121.0,
    "grok_weekly_usd": 1.0,
}

WEEKLY_BUDGETS = {
    "cursor": 50.0,
}


@dataclass
class ProviderUsage:
    """Usage record for one provider in one time window."""

    provider: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    window_start_ts: float = field(default_factory=time.time)

    @property
    def window_start(self) -> datetime:
        return datetime.fromtimestamp(self.window_start_ts, tz=timezone.utc)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProviderUsage":
        return cls(**d)


@dataclass
class BudgetCaps:
    """Configurable budget caps per provider."""

    codex_weekly_pct: float = 75.0
    cursor_weekly_pct: float = 75.0
    claude_weekly_usd: float = 20.0
    minimax_weekly_usd: float = 10.0
    gemini_prepaid_usd: float = 121.0
    grok_weekly_usd: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BudgetCaps":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class BudgetTracker:
    """Track LLM provider usage and enforce budget caps.

    Usage is stored in a JSON file and reset weekly. The tracker is designed
    to be used from the dispatcher and gate runner to check before LLM calls.
    """

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        caps: Optional[BudgetCaps] = None,
    ):
        self.data_dir = data_dir or Path(
            os.environ.get("STORYPOOL_DATA_DIR", "~/.config/storypool")
        ).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.caps = caps or BudgetCaps()
        self._usage: dict[str, ProviderUsage] = {}
        self._load()

    def _usage_file(self) -> Path:
        return self.data_dir / "budget_usage.json"

    def _caps_file(self) -> Path:
        return self.data_dir / "budget_caps.json"

    def _load(self) -> None:
        """Load usage from disk."""
        path = self._usage_file()
        if path.is_file():
            try:
                data = json.loads(path.read_text())
                for provider, u in data.get("usage", {}).items():
                    self._usage[provider] = ProviderUsage.from_dict(u)
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
        caps_path = self._caps_file()
        if caps_path.is_file():
            try:
                self.caps = BudgetCaps.from_dict(json.loads(caps_path.read_text()))
            except (json.JSONDecodeError, TypeError, KeyError):
                pass

    def _save(self) -> None:
        """Persist usage to disk."""
        data = {"usage": {p: u.to_dict() for p, u in self._usage.items()}}
        self._usage_file().write_text(json.dumps(data, indent=2))

    def _week_start(self) -> float:
        """Start of the current week (Monday 00:00 UTC)."""
        now = datetime.now(timezone.utc)
        days_since_monday = now.weekday()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = start.replace(day=start.day - days_since_monday)
        return start.timestamp()

    def _get_or_create_usage(self, provider: str) -> ProviderUsage:
        """Get usage for provider, resetting if week changed."""
        if provider not in PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}")
        week_start = self._week_start()
        usage = self._usage.get(provider)
        if usage is None or usage.window_start_ts < week_start:
            usage = ProviderUsage(provider=provider, window_start_ts=week_start)
            self._usage[provider] = usage
        return usage

    def record(
        self,
        provider: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Record a usage event for a provider."""
        usage = self._get_or_create_usage(provider)
        usage.tokens_in += tokens_in
        usage.tokens_out += tokens_out
        usage.cost_usd += cost_usd
        usage.calls += 1
        self._save()

    def get_usage(self, provider: str) -> ProviderUsage:
        """Get current weekly usage for a provider."""
        return self._get_or_create_usage(provider)

    def weekly_pct(self, provider: str) -> Optional[float]:
        """Get weekly usage as a percentage of the weekly budget."""
        if provider == "codex":
            return read_codex_quota()["weekly_pct"]
        usage = self.get_usage(provider)
        if provider in WEEKLY_BUDGETS:
            budget = WEEKLY_BUDGETS[provider]
            return (usage.cost_usd / budget) * 100 if budget > 0 else 0.0
        return 0.0

    def can_use(self, provider: str) -> tuple[bool, str]:
        """Check if a provider is within budget caps.

        Returns (allowed, reason). If not allowed, reason explains why.
        """
        usage = self.get_usage(provider)

        if provider == "codex":
            pct = self.weekly_pct(provider)
            if pct is None:
                return False, "Codex weekly plan quota unknown; fresh rate-limit observation required"
            if pct >= min(75.0, self.caps.codex_weekly_pct):
                return False, f"Codex at {pct:.1f}% of weekly cap"

        elif provider == "cursor":
            pct = self.weekly_pct(provider)
            if pct >= self.caps.cursor_weekly_pct:
                return False, f"Cursor at {pct:.1f}% of weekly cap"

        elif provider == "claude":
            if usage.cost_usd >= self.caps.claude_weekly_usd:
                return False, f"Claude at ${usage.cost_usd:.2f} of ${self.caps.claude_weekly_usd:.2f} cap"

        elif provider == "minimax":
            if usage.cost_usd >= self.caps.minimax_weekly_usd:
                return False, f"MiniMax at ${usage.cost_usd:.2f} of ${self.caps.minimax_weekly_usd:.2f} cap"

        elif provider == "gemini":
            if usage.cost_usd >= self.caps.gemini_prepaid_usd:
                return False, f"Gemini prepaid exhausted (${usage.cost_usd:.2f})"

        elif provider == "grok":
            if usage.cost_usd >= self.caps.grok_weekly_usd:
                return False, f"Grok budget exceeded (${usage.cost_usd:.2f} >= ${self.caps.grok_weekly_usd:.2f})"

        return True, "ok"

    def summary(self) -> dict[str, dict]:
        """Return a summary of all provider usage."""
        result = {}
        for provider in PROVIDERS:
            usage = self.get_usage(provider)
            allowed, reason = self.can_use(provider)
            result[provider] = {
                "cost_usd": None if provider == "codex" else usage.cost_usd,
                "calls": usage.calls,
                "tokens_in": usage.tokens_in,
                "tokens_out": usage.tokens_out,
                "allowed": allowed,
                "reason": reason,
            }
            if provider == "codex":
                quota = read_codex_quota()
                result[provider]["weekly_pct"] = quota["weekly_pct"]
                result[provider]["quota"] = quota
            elif provider in WEEKLY_BUDGETS:
                result[provider]["weekly_pct"] = self.weekly_pct(provider)
        return result
