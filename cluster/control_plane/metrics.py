"""Metrics tracking for StoryPool services.

Tracks key metrics defined in docs/storypool-scorecard.md:
- First-try pass rate
- Time-to-merge
- Fix-loop success rate
- Pre-LLM catch rate
- Stuck PR rate

Stores metrics in JSON files for persistence and week-over-week comparison.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

METRICS_DIR = Path(
    os.environ.get("STORYPOOL_METRICS_DIR", "~/.local/share/storypool/metrics")
).expanduser()


@dataclass
class KarenMetrics:
    """Metrics for Karen gate performance."""
    total_reviews: int = 0
    first_try_passes: int = 0
    first_try_fails: int = 0
    pre_llm_catches: int = 0
    llm_catches: int = 0
    false_positives: int = 0
    
    @property
    def first_try_pass_rate(self) -> float:
        if self.total_reviews == 0:
            return 0.0
        return (self.first_try_passes / self.total_reviews) * 100
    
    @property
    def pre_llm_catch_rate(self) -> float:
        total_catches = self.pre_llm_catches + self.llm_catches
        if total_catches == 0:
            return 0.0
        return (self.pre_llm_catches / total_catches) * 100
    
    @property
    def false_positive_rate(self) -> float:
        total_fails = self.first_try_fails + self.pre_llm_catches + self.llm_catches
        if total_fails == 0:
            return 0.0
        return (self.false_positives / total_fails) * 100
    
    def to_dict(self) -> dict:
        d = asdict(self)
        d["first_try_pass_rate"] = self.first_try_pass_rate
        d["pre_llm_catch_rate"] = self.pre_llm_catch_rate
        d["false_positive_rate"] = self.false_positive_rate
        return d


@dataclass
class ConductorMetrics:
    """Metrics for Conductor performance."""
    merges_attempted: int = 0
    merges_successful: int = 0
    fix_attempts: int = 0
    fix_successes: int = 0
    total_time_to_merge_hours: float = 0.0
    merge_count_for_ttm: int = 0
    stuck_prs: int = 0
    total_open_prs: int = 0
    
    @property
    def fix_loop_success_rate(self) -> float:
        if self.fix_attempts == 0:
            return 0.0
        return (self.fix_successes / self.fix_attempts) * 100
    
    @property
    def median_time_to_merge(self) -> float:
        if self.merge_count_for_ttm == 0:
            return 0.0
        return self.total_time_to_merge_hours / self.merge_count_for_ttm
    
    @property
    def stuck_pr_rate(self) -> float:
        if self.total_open_prs == 0:
            return 0.0
        return (self.stuck_prs / self.total_open_prs) * 100
    
    def to_dict(self) -> dict:
        d = asdict(self)
        d["fix_loop_success_rate"] = self.fix_loop_success_rate
        d["median_time_to_merge"] = self.median_time_to_merge
        d["stuck_pr_rate"] = self.stuck_pr_rate
        return d


@dataclass
class WeeklyMetrics:
    """Weekly aggregated metrics for comparison."""
    week_start: str = ""
    week_end: str = ""
    karen: KarenMetrics = field(default_factory=KarenMetrics)
    conductor: ConductorMetrics = field(default_factory=ConductorMetrics)
    
    def to_dict(self) -> dict:
        return {
            "week_start": self.week_start,
            "week_end": self.week_end,
            "karen": self.karen.to_dict(),
            "conductor": self.conductor.to_dict(),
        }


class MetricsTracker:
    """Persistent metrics tracker with week-over-week comparison."""
    
    def __init__(self, metrics_dir: Optional[Path] = None):
        self.metrics_dir = metrics_dir or METRICS_DIR
        self.metrics_dir.mkdir(parents=True, exist_ok=True)
    
    def _karen_file(self) -> Path:
        return self.metrics_dir / "karen-metrics.json"
    
    def _conductor_file(self) -> Path:
        return self.metrics_dir / "conductor-metrics.json"
    
    def _weekly_file(self, week_start: str) -> Path:
        return self.metrics_dir / f"weekly-{week_start}.json"
    
    def _current_week_start(self) -> str:
        """Get ISO date of Monday of current week."""
        now = datetime.now(timezone.utc)
        monday = now - timedelta(days=now.weekday())
        return monday.strftime("%Y-%m-%d")
    
    def _last_week_start(self) -> str:
        """Get ISO date of Monday of last week."""
        now = datetime.now(timezone.utc)
        monday = now - timedelta(days=now.weekday() + 7)
        return monday.strftime("%Y-%m-%d")
    
    def load_karen_metrics(self) -> KarenMetrics:
        """Load current Karen metrics."""
        f = self._karen_file()
        if not f.is_file():
            return KarenMetrics()
        try:
            data = json.loads(f.read_text())
            return KarenMetrics(
                total_reviews=data.get("total_reviews", 0),
                first_try_passes=data.get("first_try_passes", 0),
                first_try_fails=data.get("first_try_fails", 0),
                pre_llm_catches=data.get("pre_llm_catches", 0),
                llm_catches=data.get("llm_catches", 0),
                false_positives=data.get("false_positives", 0),
            )
        except (json.JSONDecodeError, KeyError):
            return KarenMetrics()
    
    def save_karen_metrics(self, metrics: KarenMetrics) -> None:
        """Save Karen metrics."""
        self._karen_file().write_text(json.dumps(metrics.to_dict(), indent=2))
    
    def load_conductor_metrics(self) -> ConductorMetrics:
        """Load current Conductor metrics."""
        f = self._conductor_file()
        if not f.is_file():
            return ConductorMetrics()
        try:
            data = json.loads(f.read_text())
            return ConductorMetrics(
                merges_attempted=data.get("merges_attempted", 0),
                merges_successful=data.get("merges_successful", 0),
                fix_attempts=data.get("fix_attempts", 0),
                fix_successes=data.get("fix_successes", 0),
                total_time_to_merge_hours=data.get("total_time_to_merge_hours", 0.0),
                merge_count_for_ttm=data.get("merge_count_for_ttm", 0),
                stuck_prs=data.get("stuck_prs", 0),
                total_open_prs=data.get("total_open_prs", 0),
            )
        except (json.JSONDecodeError, KeyError):
            return ConductorMetrics()
    
    def save_conductor_metrics(self, metrics: ConductorMetrics) -> None:
        """Save Conductor metrics."""
        self._conductor_file().write_text(json.dumps(metrics.to_dict(), indent=2))
    
    def record_karen_review(
        self,
        passed: bool,
        first_try: bool = True,
        caught_by_pre_llm: bool = False,
        caught_by_llm: bool = False,
    ) -> None:
        """Record a Karen review result."""
        metrics = self.load_karen_metrics()
        metrics.total_reviews += 1
        
        if passed and first_try:
            metrics.first_try_passes += 1
        elif not passed and first_try:
            metrics.first_try_fails += 1
        
        if caught_by_pre_llm:
            metrics.pre_llm_catches += 1
        elif caught_by_llm:
            metrics.llm_catches += 1
        
        self.save_karen_metrics(metrics)
    
    def record_false_positive(self) -> None:
        """Record a Karen false positive (overturned FAIL)."""
        metrics = self.load_karen_metrics()
        metrics.false_positives += 1
        self.save_karen_metrics(metrics)
    
    def record_merge(self, time_to_merge_hours: float, successful: bool = True) -> None:
        """Record a merge attempt and time."""
        metrics = self.load_conductor_metrics()
        metrics.merges_attempted += 1
        if successful:
            metrics.merges_successful += 1
            metrics.total_time_to_merge_hours += time_to_merge_hours
            metrics.merge_count_for_ttm += 1
        self.save_conductor_metrics(metrics)
    
    def record_fix_attempt(self, successful: bool) -> None:
        """Record a fix-loop attempt."""
        metrics = self.load_conductor_metrics()
        metrics.fix_attempts += 1
        if successful:
            metrics.fix_successes += 1
        self.save_conductor_metrics(metrics)
    
    def update_pr_counts(self, total_open: int, stuck: int) -> None:
        """Update current PR counts."""
        metrics = self.load_conductor_metrics()
        metrics.total_open_prs = total_open
        metrics.stuck_prs = stuck
        self.save_conductor_metrics(metrics)
    
    def snapshot_weekly(self) -> None:
        """Snapshot current metrics for weekly comparison."""
        week_start = self._current_week_start()
        now = datetime.now(timezone.utc)
        week_end = now.strftime("%Y-%m-%d")
        
        weekly = WeeklyMetrics(
            week_start=week_start,
            week_end=week_end,
            karen=self.load_karen_metrics(),
            conductor=self.load_conductor_metrics(),
        )
        
        self._weekly_file(week_start).write_text(
            json.dumps(weekly.to_dict(), indent=2)
        )
    
    def load_weekly(self, week_start: str) -> Optional[WeeklyMetrics]:
        """Load weekly metrics for a specific week."""
        f = self._weekly_file(week_start)
        if not f.is_file():
            return None
        try:
            data = json.loads(f.read_text())
            karen_data = data.get("karen", {})
            conductor_data = data.get("conductor", {})
            
            return WeeklyMetrics(
                week_start=data.get("week_start", ""),
                week_end=data.get("week_end", ""),
                karen=KarenMetrics(
                    total_reviews=karen_data.get("total_reviews", 0),
                    first_try_passes=karen_data.get("first_try_passes", 0),
                    first_try_fails=karen_data.get("first_try_fails", 0),
                    pre_llm_catches=karen_data.get("pre_llm_catches", 0),
                    llm_catches=karen_data.get("llm_catches", 0),
                    false_positives=karen_data.get("false_positives", 0),
                ),
                conductor=ConductorMetrics(
                    merges_attempted=conductor_data.get("merges_attempted", 0),
                    merges_successful=conductor_data.get("merges_successful", 0),
                    fix_attempts=conductor_data.get("fix_attempts", 0),
                    fix_successes=conductor_data.get("fix_successes", 0),
                    total_time_to_merge_hours=conductor_data.get("total_time_to_merge_hours", 0.0),
                    merge_count_for_ttm=conductor_data.get("merge_count_for_ttm", 0),
                    stuck_prs=conductor_data.get("stuck_prs", 0),
                    total_open_prs=conductor_data.get("total_open_prs", 0),
                ),
            )
        except (json.JSONDecodeError, KeyError):
            return None
    
    def get_week_over_week_comparison(self) -> dict:
        """Get comparison between this week and last week."""
        this_week = self._current_week_start()
        last_week = self._last_week_start()
        
        current = WeeklyMetrics(
            week_start=this_week,
            karen=self.load_karen_metrics(),
            conductor=self.load_conductor_metrics(),
        )
        
        previous = self.load_weekly(last_week)
        
        def delta(curr: float, prev: float) -> str:
            if prev == 0:
                return "N/A" if curr == 0 else f"+{curr:.1f}"
            change = curr - prev
            pct = (change / prev) * 100 if prev != 0 else 0
            sign = "+" if change >= 0 else ""
            return f"{sign}{change:.1f} ({sign}{pct:.0f}%)"
        
        comparison = {
            "this_week": this_week,
            "last_week": last_week,
            "has_previous": previous is not None,
        }
        
        if previous:
            comparison["karen"] = {
                "first_try_pass_rate": {
                    "current": f"{current.karen.first_try_pass_rate:.1f}%",
                    "previous": f"{previous.karen.first_try_pass_rate:.1f}%",
                    "delta": delta(current.karen.first_try_pass_rate, previous.karen.first_try_pass_rate),
                },
                "pre_llm_catch_rate": {
                    "current": f"{current.karen.pre_llm_catch_rate:.1f}%",
                    "previous": f"{previous.karen.pre_llm_catch_rate:.1f}%",
                    "delta": delta(current.karen.pre_llm_catch_rate, previous.karen.pre_llm_catch_rate),
                },
                "total_reviews": {
                    "current": current.karen.total_reviews,
                    "previous": previous.karen.total_reviews,
                },
            }
            comparison["conductor"] = {
                "fix_loop_success_rate": {
                    "current": f"{current.conductor.fix_loop_success_rate:.1f}%",
                    "previous": f"{previous.conductor.fix_loop_success_rate:.1f}%",
                    "delta": delta(current.conductor.fix_loop_success_rate, previous.conductor.fix_loop_success_rate),
                },
                "median_time_to_merge": {
                    "current": f"{current.conductor.median_time_to_merge:.1f}h",
                    "previous": f"{previous.conductor.median_time_to_merge:.1f}h",
                    "delta": delta(current.conductor.median_time_to_merge, previous.conductor.median_time_to_merge),
                },
                "merges": {
                    "current": current.conductor.merges_successful,
                    "previous": previous.conductor.merges_successful,
                },
            }
        else:
            comparison["karen"] = {
                "first_try_pass_rate": f"{current.karen.first_try_pass_rate:.1f}%",
                "pre_llm_catch_rate": f"{current.karen.pre_llm_catch_rate:.1f}%",
                "total_reviews": current.karen.total_reviews,
            }
            comparison["conductor"] = {
                "fix_loop_success_rate": f"{current.conductor.fix_loop_success_rate:.1f}%",
                "median_time_to_merge": f"{current.conductor.median_time_to_merge:.1f}h",
                "merges": current.conductor.merges_successful,
            }
        
        return comparison
    
    def format_comparison_markdown(self) -> str:
        """Format week-over-week comparison as markdown."""
        comp = self.get_week_over_week_comparison()
        
        lines = [
            "## Week-over-Week Comparison",
            "",
            f"**This week:** {comp['this_week']}",
            f"**Last week:** {comp['last_week']}",
            "",
        ]
        
        if comp["has_previous"]:
            lines.extend([
                "### Karen Gate",
                "",
                "| Metric | This Week | Last Week | Delta |",
                "|--------|-----------|-----------|-------|",
            ])
            karen = comp["karen"]
            lines.append(f"| First-try pass rate | {karen['first_try_pass_rate']['current']} | {karen['first_try_pass_rate']['previous']} | {karen['first_try_pass_rate']['delta']} |")
            lines.append(f"| Pre-LLM catch rate | {karen['pre_llm_catch_rate']['current']} | {karen['pre_llm_catch_rate']['previous']} | {karen['pre_llm_catch_rate']['delta']} |")
            lines.append(f"| Total reviews | {karen['total_reviews']['current']} | {karen['total_reviews']['previous']} | - |")
            
            lines.extend([
                "",
                "### Conductor",
                "",
                "| Metric | This Week | Last Week | Delta |",
                "|--------|-----------|-----------|-------|",
            ])
            conductor = comp["conductor"]
            lines.append(f"| Fix-loop success | {conductor['fix_loop_success_rate']['current']} | {conductor['fix_loop_success_rate']['previous']} | {conductor['fix_loop_success_rate']['delta']} |")
            lines.append(f"| Time-to-merge | {conductor['median_time_to_merge']['current']} | {conductor['median_time_to_merge']['previous']} | {conductor['median_time_to_merge']['delta']} |")
            lines.append(f"| Merges | {conductor['merges']['current']} | {conductor['merges']['previous']} | - |")
        else:
            lines.extend([
                "*No previous week data for comparison*",
                "",
                "### Current Week",
                "",
                "| Metric | Value |",
                "|--------|-------|",
                f"| First-try pass rate | {comp['karen']['first_try_pass_rate']} |",
                f"| Pre-LLM catch rate | {comp['karen']['pre_llm_catch_rate']} |",
                f"| Total reviews | {comp['karen']['total_reviews']} |",
                f"| Fix-loop success | {comp['conductor']['fix_loop_success_rate']} |",
                f"| Time-to-merge | {comp['conductor']['median_time_to_merge']} |",
                f"| Merges | {comp['conductor']['merges']} |",
            ])
        
        return "\n".join(lines)
