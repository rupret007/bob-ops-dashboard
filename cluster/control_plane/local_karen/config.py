"""Configuration for Local Karen gate.

Configurable reviewer order, budget caps, and blocked repos.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


NEVER_TOUCH_REPOS = frozenset([
    "rupret007/rad-dad-show-night",
])

DEFAULT_REVIEWER_ORDER = ["codex", "gemini", "minimax"]

CODEX_WEEKLY_BUDGET_PCT = 75.0
CLAUDE_DAILY_CAP = 3
DEFAULT_BUDGET_PER_RUN_USD = 0.50


@dataclass
class KarenConfig:
    """Configuration for Local Karen gate."""

    reviewer_order: list[str] = field(
        default_factory=lambda: list(DEFAULT_REVIEWER_ORDER)
    )
    codex_host: str = "nuc"
    codex_effort_default: str = "low"
    codex_effort_high_risk: str = "high"
    codex_weekly_cap_pct: float = CODEX_WEEKLY_BUDGET_PCT

    gemini_api_key_file: str = "~/.config/storypool/gemini.key"
    minimax_api_key_file: str = "~/.config/storypool/minimax.key"

    claude_enabled: bool = False
    claude_daily_cap: int = CLAUDE_DAILY_CAP

    budget_per_run_usd: float = DEFAULT_BUDGET_PER_RUN_USD

    never_touch_repos: frozenset[str] = field(
        default_factory=lambda: NEVER_TOUCH_REPOS
    )

    verdict_dir: str = "~/.local/share/karen/verdicts"
    state_dir: str = "~/.local/share/karen"

    poll_interval_minutes: int = 10
    poll_label: str = "needs-karen"

    def __post_init__(self):
        if isinstance(self.never_touch_repos, list):
            self.never_touch_repos = frozenset(self.never_touch_repos)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "KarenConfig":
        """Load config from JSON file or environment."""
        if path is None:
            path = Path(
                os.environ.get(
                    "KAREN_CONFIG",
                    "~/.config/karen/config.json"
                )
            ).expanduser()

        if path.is_file():
            try:
                data = json.loads(path.read_text())
                if "never_touch_repos" in data:
                    data["never_touch_repos"] = frozenset(data["never_touch_repos"])
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except (json.JSONDecodeError, TypeError):
                pass

        return cls()

    def save(self, path: Optional[Path] = None) -> None:
        """Save config to JSON file."""
        if path is None:
            path = Path("~/.config/karen/config.json").expanduser()

        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["never_touch_repos"] = list(self.never_touch_repos)
        path.write_text(json.dumps(data, indent=2))

    def get_verdict_dir(self) -> Path:
        """Get expanded verdict directory path."""
        return Path(self.verdict_dir).expanduser()

    def get_state_dir(self) -> Path:
        """Get expanded state directory path."""
        return Path(self.state_dir).expanduser()

    def is_blocked_repo(self, owner: str, repo: str) -> bool:
        """Check if a repo is in the never-touch list."""
        full_name = f"{owner}/{repo}"
        return full_name in self.never_touch_repos
