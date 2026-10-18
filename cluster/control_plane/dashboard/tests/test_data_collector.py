"""Tests for dashboard data collector."""

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

import pytest

from cluster.control_plane.dashboard.data_collector import (
    DataCollector,
    DashboardData,
    ServiceHealth,
    GuardValues,
    PRStatus,
    KarenVerdict,
    ProviderBudget,
    _safe_read_json,
    _safe_read_lines,
    _parse_jsonl,
)


class TestSafeReadHelpers:
    """Tests for safe read helper functions."""
    
    def test_safe_read_json_valid(self, tmp_path: Path):
        """Valid JSON file should parse correctly."""
        json_file = tmp_path / "test.json"
        json_file.write_text('{"key": "value"}')
        
        result = _safe_read_json(json_file)
        assert result == {"key": "value"}
    
    def test_safe_read_json_invalid(self, tmp_path: Path):
        """Invalid JSON should return None."""
        json_file = tmp_path / "test.json"
        json_file.write_text('not json')
        
        result = _safe_read_json(json_file)
        assert result is None
    
    def test_safe_read_json_missing(self, tmp_path: Path):
        """Missing file should return None."""
        result = _safe_read_json(tmp_path / "missing.json")
        assert result is None
    
    def test_safe_read_lines(self, tmp_path: Path):
        """Should read last N lines."""
        log_file = tmp_path / "test.log"
        log_file.write_text("\n".join(f"line{i}" for i in range(10)))
        
        result = _safe_read_lines(log_file, 5)
        assert len(result) == 5
        assert result[-1] == "line9"
    
    def test_parse_jsonl(self, tmp_path: Path):
        """Should parse JSONL file."""
        jsonl_file = tmp_path / "test.jsonl"
        jsonl_file.write_text("\n".join([
            '{"n": 1}',
            '{"n": 2}',
            '{"n": 3}',
        ]))
        
        result = _parse_jsonl(jsonl_file, 2)
        assert len(result) == 2
        assert result[0]["n"] == 2
        assert result[1]["n"] == 3


class TestDashboardData:
    """Tests for DashboardData dataclass."""
    
    def test_to_dict(self):
        """to_dict should serialize all fields."""
        data = DashboardData(
            timestamp="2026-10-18T12:00:00Z",
            machine="mini",
            live_mode=True,
        )
        
        d = data.to_dict()
        assert d["timestamp"] == "2026-10-18T12:00:00Z"
        assert d["machine"] == "mini"
        assert d["live_mode"] is True
        assert "services" in d
        assert "providers" in d


class TestDataCollector:
    """Tests for DataCollector."""
    
    def test_collect_returns_dashboard_data(self, tmp_path: Path):
        """collect() should return DashboardData."""
        collector = DataCollector(
            data_dir=tmp_path / "storypool",
            karen_dir=tmp_path / "karen",
            metrics_dir=tmp_path / "metrics",
        )
        
        (tmp_path / "storypool").mkdir()
        (tmp_path / "karen").mkdir()
        (tmp_path / "metrics").mkdir()
        
        data = collector.collect()
        
        assert isinstance(data, DashboardData)
        assert data.timestamp
        assert data.machine in ("mini", "nucbox")
    
    def test_collect_services(self, tmp_path: Path):
        """Should collect service health."""
        data_dir = tmp_path / "storypool"
        shadow_dir = data_dir / "shadow"
        shadow_dir.mkdir(parents=True)
        
        log_file = shadow_dir / "shadow.log"
        log_file.write_text("INFO: test log\n")
        
        collector = DataCollector(
            data_dir=data_dir,
            karen_dir=tmp_path / "karen",
            metrics_dir=tmp_path / "metrics",
        )
        
        services = collector._collect_services()
        
        assert len(services) > 0
        shadow_svc = next((s for s in services if s.name == "shadow"), None)
        assert shadow_svc is not None
        assert shadow_svc.status in ("running", "stopped", "error", "unknown")
    
    def test_collect_guards(self, tmp_path: Path):
        """Should collect guard values."""
        collector = DataCollector(
            data_dir=tmp_path / "storypool",
            karen_dir=tmp_path / "karen",
            metrics_dir=tmp_path / "metrics",
        )
        
        guards = collector._collect_guards()
        
        assert isinstance(guards, GuardValues)
        assert guards.load >= 0
        assert guards.load_limit > 0
    
    def test_collect_verdicts(self, tmp_path: Path):
        """Should collect Karen verdicts."""
        verdict_dir = tmp_path / "karen" / "verdicts" / "rupret007_test"
        verdict_dir.mkdir(parents=True)
        
        verdict_file = verdict_dir / "pr-123-abc12345def.json"
        verdict_file.write_text(json.dumps({
            "owner": "rupret007",
            "repo": "test",
            "pr_number": 123,
            "head_sha": "abc12345def",
            "passed": True,
            "reason": "All checks passed",
            "reviewer": "codex",
            "timestamp": "2026-10-18T12:00:00Z",
        }))
        
        collector = DataCollector(
            data_dir=tmp_path / "storypool",
            karen_dir=tmp_path / "karen",
            metrics_dir=tmp_path / "metrics",
        )
        
        verdicts = collector._collect_verdicts()
        
        assert len(verdicts) == 1
        assert verdicts[0].pr_number == 123
        assert verdicts[0].verdict == "PASS"
    
    def test_collect_merges(self, tmp_path: Path):
        """Should collect today's merges."""
        conductor_dir = tmp_path / "storypool" / "conductor"
        conductor_dir.mkdir(parents=True)
        
        now = datetime.now(timezone.utc)
        merge_state = {
            "merges": [
                {"repo": "rupret007/test", "pr": 1, "sha": "abc", "ts": now.timestamp(), "time": now.isoformat()},
                {"repo": "rupret007/test", "pr": 2, "sha": "def", "ts": (now - timedelta(days=2)).timestamp()},
            ]
        }
        (conductor_dir / "merge-state.json").write_text(json.dumps(merge_state))
        
        collector = DataCollector(
            data_dir=tmp_path / "storypool",
            karen_dir=tmp_path / "karen",
            metrics_dir=tmp_path / "metrics",
        )
        
        merges = collector._collect_merges()
        
        assert len(merges) == 1
        assert merges[0].pr_number == 1
    
    def test_collect_providers(self, tmp_path: Path):
        """Should collect provider budgets."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir(parents=True)
        
        budget_state = {
            "codex": {"weekly_pct": 50.0, "weekly_usd": 10.0},
            "gemini": {"weekly_pct": 10.0, "weekly_usd": 2.0},
        }
        (metrics_dir / "budget-state.json").write_text(json.dumps(budget_state))
        
        collector = DataCollector(
            data_dir=tmp_path / "storypool",
            karen_dir=tmp_path / "karen",
            metrics_dir=metrics_dir,
        )
        
        providers = collector._collect_providers()
        
        assert len(providers) >= 2
        codex = next((p for p in providers if p.name == "codex"), None)
        assert codex is not None
        assert codex.weekly_pct == 50.0
        
        grok = next((p for p in providers if p.name == "grok"), None)
        assert grok is not None
        assert grok.status == "conserve"
    
    def test_collect_needs_jeff(self, tmp_path: Path):
        """Should collect NEEDS_JEFF items."""
        conductor_dir = tmp_path / "storypool" / "conductor"
        conductor_dir.mkdir(parents=True)
        
        needs_jeff = conductor_dir / "NEEDS_JEFF.md"
        needs_jeff.write_text("""# NEEDS_JEFF

Issues requiring human intervention.

---

## 2026-10-18 12:00 UTC

**Reason:** Test issue

**Details:** Some details

---
""")
        
        collector = DataCollector(
            data_dir=tmp_path / "storypool",
            karen_dir=tmp_path / "karen",
            metrics_dir=tmp_path / "metrics",
        )
        
        items = collector._collect_needs_jeff()
        
        assert len(items) == 1
        assert "12:00" in items[0].timestamp
        assert "Reason" in items[0].issue
