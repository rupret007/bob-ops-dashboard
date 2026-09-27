#!/usr/bin/env python3
"""Validate the public snapshot contract and its generated HTML, offline."""
import json
from pathlib import Path
import re
import sys

PROJECT_IDS = ["web-bob", "webjam", "adoptiq", "storyboard", "storyland-fantasy"]
LLM_IDS = ["codex", "claude", "cursor", "gemini", "minimax"]


def validate(html_path, status_path):
    page = Path(html_path).read_text()
    data = json.loads(Path(status_path).read_text())
    assert data.get("schema_version") == 2, "Expected the operations board schema"
    assert [p["id"] for p in data["projects"]] == PROJECT_IDS, "Five ordered project cards required"
    assert [r["id"] for r in data["llm_work"]] == LLM_IDS, "Five ordered LLM rows required"
    assert "verify" not in data, "Authentication data does not belong on the public board"

    def check_retired(value):
        if isinstance(value, dict):
            for key, child in value.items():
                assert not re.search(r"(^|[_-])songs?($|[_-])", key, re.I), f"Retired field: {key}"
                if key in ("id", "type"):
                    assert str(child).lower() not in {"song", "songs"}, "Retired section"
                check_retired(child)
        elif isinstance(value, list):
            for child in value:
                check_retired(child)
    check_retired(data)
    for p in data["projects"]:
        if p.get("private"):
            assert not any(p.get(key) for key in ("repo", "repo_url", "url", "tip_sha", "tip_date", "draft_prs", "open_pr_url", "open_pr_number", "agent_url", "coord")), "Private project metadata leaked"
            assert not set(p.get("ci") or {}) - {"conclusion"}, "Private CI detail leaked"
        if p["id"] == "adoptiq":
            assert p.get("ready_for_live_cisco") is False, "AdoptIQ must stay offline"
    for ident in PROJECT_IDS:
        assert f'data-project-id="{ident}"' in page, f"Missing project {ident}"
    for ident in LLM_IDS:
        assert f'data-lane-id="{ident}"' in page, f"Missing LLM {ident}"
    head = page.split('<script type="application/json"', 1)[0]
    for name in ("Tip SHA", "Draft PR", "Next action", "Work now"):
        assert name in head, f"Missing required card content: {name}"
    for marker in ('id="auth-panel"', 'id="songs"', 'class="song', 'data-lane-id="grok"', 'localStorage', 'doUnlock'):
        assert marker not in page, f"Retired UI or gate: {marker}"
    assert '<details data-detail="decisions">' in head, "Decisions must start collapsed"
    assert '<details data-detail="activity">' in head, "Activity details must start collapsed"
    assert 'target="_blank"' not in head or 'rel="noopener noreferrer"' in head
    print("GENERATED QA PASSED: five LLMs, five projects, private boundaries, no songs or auth gate")


if __name__ == "__main__":
    validate(sys.argv[1] if len(sys.argv) > 1 else "index.html", sys.argv[2] if len(sys.argv) > 2 else "status.json")
