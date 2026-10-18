"""Configuration for Local Karen gate.

Configurable reviewer order, budget caps, and blocked repos.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

NEVER_TOUCH_REPOS = frozenset([
    "rupret007/rad-dad-show-night",
])

DEFAULT_REVIEWER_ORDER = ["codex", "gemini", "minimax"]

CODEX_WEEKLY_BUDGET_PCT = 75.0
CLAUDE_DAILY_CAP = 3
DEFAULT_BUDGET_PER_RUN_USD = 0.50

AUTO_DISCOVER_SENTINEL = "auto"


@dataclass
class KarenConfig:
    """Configuration for Local Karen gate."""

    reviewer_order: list = field(  # List[str]
        default_factory=lambda: list(DEFAULT_REVIEWER_ORDER)
    )
    codex_host: str = "nuc"
    codex_effort_default: str = "low"
    codex_effort_high_risk: str = "high"
    codex_weekly_cap_pct: float = CODEX_WEEKLY_BUDGET_PCT

    gemini_api_key_file: str = "~/.config/storypool/gemini.key"
    gemini_model: str = "gemini-2.5-flash"
    minimax_api_key_file: str = "~/.config/storypool/minimax.key"

    claude_enabled: bool = False
    claude_daily_cap: int = CLAUDE_DAILY_CAP

    budget_per_run_usd: float = DEFAULT_BUDGET_PER_RUN_USD
    
    run_local_tests: bool = False

    never_touch_repos: frozenset = field(  # frozenset[str]
        default_factory=lambda: NEVER_TOUCH_REPOS
    )

    verdict_dir: str = "~/.local/share/karen/verdicts"
    state_dir: str = "~/.local/share/karen"

    poll_interval_minutes: int = 10
    poll_label: str = "needs-karen"

    repos: str = AUTO_DISCOVER_SENTINEL

    def __post_init__(self):
        if isinstance(self.never_touch_repos, list):
            self.never_touch_repos = frozenset(self.never_touch_repos)
        if isinstance(self.repos, list):
            if not self.repos:
                self.repos = AUTO_DISCOVER_SENTINEL
            else:
                self.repos = ",".join(self.repos)

    def should_auto_discover(self):
        # type: () -> bool
        """Return True if repos should be auto-discovered."""
        if self.repos == AUTO_DISCOVER_SENTINEL:
            return True
        if not self.repos or self.repos.strip() == "":
            return True
        return False

    def get_repos_list(self):
        # type: () -> list
        """Get list of repos from config, or empty list if auto-discover."""
        if self.should_auto_discover():
            return []
        return [r.strip() for r in self.repos.split(",") if r.strip()]

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
                if "repos" in data:
                    repos_val = data["repos"]
                    if isinstance(repos_val, list):
                        if not repos_val:
                            data["repos"] = AUTO_DISCOVER_SENTINEL
                        else:
                            data["repos"] = ",".join(repos_val)
                    elif repos_val is None or repos_val == "":
                        data["repos"] = AUTO_DISCOVER_SENTINEL
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except (json.JSONDecodeError, TypeError):
                pass

        return cls()

    @classmethod
    def migrate_config(cls, path: Optional[Path] = None) -> bool:
        """Migrate old config format to new format.
        
        Changes:
        - repos: [] or repos: ["single/repo"] -> repos: "auto"
        - Adds repos: "auto" if missing
        
        Returns True if config was migrated.
        """
        if path is None:
            path = Path(
                os.environ.get(
                    "KAREN_CONFIG",
                    "~/.config/karen/config.json"
                )
            ).expanduser()

        if not path.is_file():
            return False

        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            return False

        migrated = False

        if "repos" not in data:
            data["repos"] = AUTO_DISCOVER_SENTINEL
            migrated = True
        elif isinstance(data["repos"], list):
            if not data["repos"] or len(data["repos"]) <= 1:
                data["repos"] = AUTO_DISCOVER_SENTINEL
                migrated = True
            else:
                data["repos"] = ",".join(data["repos"])
                migrated = True
        elif data["repos"] is None or data["repos"] == "":
            data["repos"] = AUTO_DISCOVER_SENTINEL
            migrated = True

        if migrated:
            path.write_text(json.dumps(data, indent=2))
            logger.info("Migrated config at %s: repos set to '%s'", path, data["repos"])

        return migrated

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
        """Check if a repo is in the never-touch list (case-insensitive)."""
        full_name = f"{owner}/{repo}".lower()
        return full_name in {r.lower() for r in self.never_touch_repos}
