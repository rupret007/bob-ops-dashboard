"""Synthetic fixtures for dashboard QA; never delegates to the real GitHub CLI."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path


# Reuses the metadata/commit/empty-list vectors from the outage integration test.
# Names here are static test identifiers, not permission to query these repos.
FAKE_GH_SOURCE = textwrap.dedent(r'''\
#!/usr/bin/env python3
import json
import os
import sys
from urllib.parse import parse_qs

args = sys.argv[1:]
log_path = os.environ.get("BOB_DASHBOARD_FIXTURE_LOG")
if os.environ.get("BOB_DASHBOARD_FIXTURE_MODE") != "offline" or not log_path:
    raise SystemExit("Fake gh requires the offline fixture environment")

def record(outcome):
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(json.dumps({"args": args, "outcome": outcome}) + "\n")

def refuse():
    record("refused")
    raise SystemExit(2)

repos = {
    "rupret007/" + name for name in (
        "webjam", "StoryLiner", "StoryBoard", "Rad-Dad-Merch", "RadDadSite",
        "Turdanoid", "AdoptIQ", "TACTrack", "AI-Music-Vault", "rad-dad-show-night",
        "Andrea_NanoBot", "Bob-the-Bot", "story-corner-shelf", "StoryOps-AI",
        "ballbeacon", "CSS_Conductor", "bob-ops-dashboard",
        "Cursor-OpenClaw-Integration", "Sliding-Glass-Door-PETG-Screw",
    )
} | {"0xc0re/barker"}
private = {"AdoptIQ", "TACTrack", "AI-Music-Vault", "CSS_Conductor", "Bob-the-Bot"}
scenario = os.environ.get("BOB_DASHBOARD_FIXTURE_SCENARIO")
if scenario not in {"complete", "metadata-outage"}:
    refuse()

coord_list = ["issue", "list", "-R", "rupret007/Bob-the-Bot", "--label", "coord",
              "--state", "open", "--limit", "50", "--json", "number,title,body,url,updatedAt"]
decision_list = ["issue", "list", "-R", "rupret007/bob-ops-dashboard", "--state", "open",
                 "--limit", "40", "--json", "number,title,author,createdAt,url"]
if args in (coord_list, decision_list):
    value = []
elif len(args) == 2 and args[0] == "api":
    path, _, query = args[1].partition("?")
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "repos" or "/".join(parts[1:3]) not in repos:
        refuse()
    owner, repo = parts[1:3]
    params = parse_qs(query, keep_blank_values=True)
    if len(parts) == 3 and not query:
        if scenario == "metadata-outage" and owner == "rupret007" and repo == "webjam":
            record("fixture-outage")
            raise SystemExit(1)
        value = {
            "name": repo, "full_name": f"{owner}/{repo}", "private": repo in private,
            "default_branch": "main", "html_url": f"https://github.com/{owner}/{repo}",
        }
    elif parts[3:] == ["commits", "main"] and not query:
        value = {
            "sha": "0123456789abcdef0123456789abcdef01234567",
            "commit": {"committer": {"date": "2026-09-03T20:00:00Z"},
                       "message": "offline fixture tip"},
        }
    elif parts[3:] == ["pulls"] and params == {"state": ["open"], "per_page": ["100"], "page": ["1"]}:
        value = []
    elif parts[1:] == ["rupret007", "bob-ops-dashboard", "pulls"] and params == {"state": ["open"], "per_page": ["20"]}:
        value = []
    elif parts[3:] == ["actions", "runs"] and params == {"per_page": ["20"], "branch": ["main"]}:
        value = {"workflow_runs": []}
    elif parts[3:] == ["releases"] and params == {"per_page": ["1"]}:
        value = []
    else:
        refuse()
else:
    refuse()
record("fixture")
print(json.dumps(value))
''').lstrip("\\\n")


def require_temporary_child(root: Path) -> Path:
    resolved = root.resolve()
    temp_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    if not any(resolved != base and resolved.is_relative_to(base) for base in temp_roots):
        raise ValueError("Fixture files must stay in a child of a temporary directory")
    if any((parent / ".git").exists() for parent in (resolved, *resolved.parents)):
        raise ValueError("Fixture files must not overwrite a repository")
    return resolved


def seed_agent_fixture(root: Path) -> None:
    """First candidate exists, so refresh never reads the owner fallback file."""
    root = require_temporary_child(root)
    payload = json.dumps({
        "agents": [
            {"id": name, "name": label, "state": "unknown", "detail": "Offline fixture"}
            for name, label in (("codex", "Codex"), ("cursor", "Cursor"), ("claude", "Claude"))
        ],
    }) + "\n"
    with (root / "agents-status.json").open("x", encoding="utf-8") as destination:
        destination.write(payload)


def prepare_generator(source: Path, destination: Path) -> None:
    destination = require_temporary_child(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Fixture generator destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("README.md", "board_meta.py", "refresh.sh"):
        shutil.copyfile(source / name, destination / name)
    (destination / "status.json").write_text('{"offline_fixture": true}\n', encoding="utf-8")
    (destination / "index.html").write_text("<!-- offline fixture sentinel -->\n", encoding="utf-8")
    seed_agent_fixture(destination)


def fixture_environment(scratch: Path, *, scenario: str = "complete") -> dict[str, str]:
    """Allowlist process environment: no inherited credentials or probe payloads."""
    if scenario not in {"complete", "metadata-outage"}:
        raise ValueError("unknown offline fixture scenario")
    scratch = require_temporary_child(scratch)
    fake_bin = scratch / "fixture-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(FAKE_GH_SOURCE, encoding="utf-8")
    fake_gh.chmod(0o755)
    temp = scratch / "tmp"
    temp.mkdir(exist_ok=True)
    # Keep tool lookup working on macOS and hosted Linux without inheriting
    # Python/Node preload settings, real GH config, tokens, or local probes.
    return {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
        "OWNER": "rupret007",
        "BOB_DASHBOARD_APPLY_DECISIONS": "0",
        "BOB_DASHBOARD_FIXTURE_MODE": "offline",
        "BOB_DASHBOARD_FIXTURE_SCENARIO": scenario,
        "BOB_DASHBOARD_FIXTURE_LOG": str(scratch / "fixture-gh.jsonl"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(temp),
        "LANG": "C.UTF-8",
    }


def assert_fixture_calls(log_path: Path, *, require_calls: bool = True) -> list[dict]:
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()] if log_path.exists() else []
    if require_calls and not rows:
        raise RuntimeError("Offline QA made no fixture calls")
    if any(row.get("outcome") == "refused" for row in rows):
        raise RuntimeError("Offline QA attempted an unsupported command; real gh was never called")
    return rows


if __name__ == "__main__":
    # This helper only seeds a specified disposable directory, never a probe.
    if len(sys.argv) != 3 or sys.argv[1] != "seed-agents":
        raise SystemExit("usage: offline_qa_fixtures.py seed-agents TEMP_DIRECTORY")
    seed_agent_fixture(Path(sys.argv[2]))
