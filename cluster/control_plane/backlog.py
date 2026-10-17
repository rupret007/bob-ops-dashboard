#!/usr/bin/env python3
"""Backlog discovery and reporting for StoryPool.

Discovers all rupret007 repos with open PRs and generates a backlog report.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)

EXCLUDED_REPOS = frozenset([
    "rupret007/rad-dad-show-night",
    "rupret007/hang-out",
    "rupret007/Hang-Out",
])

BACKLOG_PATH = Path("~/.local/share/storypool/backlog.md").expanduser()
DOCS_BACKLOG_PATH = Path("docs/storypool-backlog.md")


def _run_gh(args, default=None):
    # type: (List[str], Any) -> Any
    """Run gh CLI and return parsed JSON."""
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning("gh %s failed: %s", " ".join(args[:3]), result.stderr[:100])
            return default
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError) as e:
        logger.warning("gh command error: %s", e)
        return default


def discover_repos(owner="rupret007"):
    # type: (str) -> List[str]
    """Discover all repos for owner with open PRs, excluding blocked ones."""
    repos = []  # type: List[str]
    
    repo_list = _run_gh([
        "repo", "list", owner,
        "--json", "nameWithOwner",
        "--limit", "100",
    ], default=[])
    
    if not repo_list:
        logger.warning("No repos found for %s", owner)
        return repos
    
    for r in repo_list:
        name = r.get("nameWithOwner", "")
        if not name:
            continue
        if name.lower() in {e.lower() for e in EXCLUDED_REPOS}:
            logger.debug("Excluding %s (blocked)", name)
            continue
        repos.append(name)
    
    logger.info("Discovered %d repos for %s", len(repos), owner)
    return repos


def get_open_prs(repo):
    # type: (str) -> List[Dict]
    """Get open PRs for a repo."""
    prs = _run_gh([
        "pr", "list",
        "--repo", repo,
        "--state", "open",
        "--json", "number,title,author,headRefOid,isDraft,url,labels,reviewDecision,statusCheckRollup",
        "--limit", "50",
    ], default=[])
    
    return prs if isinstance(prs, list) else []


def get_pr_state(pr, verdicts_dir=None):
    # type: (Dict, Optional[Path]) -> str
    """Determine PR state: queued, under_review, passed, failed, blocked."""
    if pr.get("isDraft"):
        return "draft"
    
    labels = [l.get("name", "").lower() for l in pr.get("labels", [])]
    
    if "karen-pass" in labels or "karenpass" in labels:
        return "passed"
    if "karen-fail" in labels or "karenfail" in labels:
        return "failed"
    if "needs-karen" in labels:
        return "under_review"
    
    ci_checks = pr.get("statusCheckRollup", []) or []
    ci_passing = True
    ci_pending = False
    
    for check in ci_checks:
        state = (check.get("conclusion") or check.get("state") or "").lower()
        if state in ("failure", "error", "cancelled"):
            ci_passing = False
            break
        if state in ("pending", "queued", "in_progress"):
            ci_pending = True
    
    if not ci_passing:
        return "ci_failing"
    if ci_pending:
        return "ci_pending"
    
    return "queued"


def collect_backlog(repos=None):
    # type: (Optional[List[str]]) -> Dict[str, Any]
    """Collect backlog across all repos.
    
    Returns dict with repos, prs, and summary.
    """
    if repos is None:
        repos = discover_repos()
    
    backlog = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repos": [],
        "summary": {
            "total_repos": 0,
            "total_prs": 0,
            "queued": 0,
            "under_review": 0,
            "passed": 0,
            "failed": 0,
            "draft": 0,
            "ci_failing": 0,
            "ci_pending": 0,
        },
    }  # type: Dict[str, Any]
    
    for repo in repos:
        prs = get_open_prs(repo)
        if not prs:
            continue
        
        repo_data = {
            "repo": repo,
            "prs": [],
        }
        
        for pr in prs:
            state = get_pr_state(pr)
            pr_data = {
                "number": pr.get("number"),
                "title": pr.get("title", "")[:60],
                "author": (pr.get("author") or {}).get("login", "unknown"),
                "sha": pr.get("headRefOid", "")[:12],
                "state": state,
                "url": pr.get("url", ""),
            }
            repo_data["prs"].append(pr_data)
            backlog["summary"]["total_prs"] += 1
            backlog["summary"][state] = backlog["summary"].get(state, 0) + 1
        
        if repo_data["prs"]:
            backlog["repos"].append(repo_data)
            backlog["summary"]["total_repos"] += 1
    
    return backlog


def format_backlog_md(backlog):
    # type: (Dict[str, Any]) -> str
    """Format backlog as markdown."""
    lines = [
        "# StoryPool Backlog Report",
        "",
        "Generated: {}".format(backlog["timestamp"]),
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|--------|-------|",
        "| Total Repos | {} |".format(backlog["summary"]["total_repos"]),
        "| Total PRs | {} |".format(backlog["summary"]["total_prs"]),
        "| Queued | {} |".format(backlog["summary"].get("queued", 0)),
        "| Under Review | {} |".format(backlog["summary"].get("under_review", 0)),
        "| Passed | {} |".format(backlog["summary"].get("passed", 0)),
        "| Failed | {} |".format(backlog["summary"].get("failed", 0)),
        "| Draft | {} |".format(backlog["summary"].get("draft", 0)),
        "| CI Failing | {} |".format(backlog["summary"].get("ci_failing", 0)),
        "| CI Pending | {} |".format(backlog["summary"].get("ci_pending", 0)),
        "",
        "## PRs by Repository",
        "",
    ]
    
    state_emoji = {
        "queued": "🔵",
        "under_review": "🟡",
        "passed": "✅",
        "failed": "❌",
        "draft": "📝",
        "ci_failing": "🔴",
        "ci_pending": "⏳",
    }
    
    for repo_data in backlog["repos"]:
        lines.append("### {}".format(repo_data["repo"]))
        lines.append("")
        lines.append("| PR | Title | Author | State | SHA |")
        lines.append("|----|-------|--------|-------|-----|")
        
        for pr in repo_data["prs"]:
            emoji = state_emoji.get(pr["state"], "❓")
            lines.append("| [#{}]({}) | {} | {} | {} {} | {} |".format(
                pr["number"],
                pr["url"],
                pr["title"][:40],
                pr["author"],
                emoji,
                pr["state"],
                pr["sha"],
            ))
        
        lines.append("")
    
    lines.extend([
        "---",
        "",
        "*This report is auto-generated by StoryPool each tick.*",
    ])
    
    return "\n".join(lines)


def write_backlog_report(backlog, repo_root=None):
    # type: (Dict[str, Any], Optional[Path]) -> None
    """Write backlog report to both local and repo docs."""
    md = format_backlog_md(backlog)
    
    BACKLOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    BACKLOG_PATH.write_text(md)
    logger.info("Wrote backlog to %s", BACKLOG_PATH)
    
    if repo_root:
        docs_path = repo_root / DOCS_BACKLOG_PATH
        docs_path.parent.mkdir(parents=True, exist_ok=True)
        docs_path.write_text(md)
        logger.info("Wrote backlog to %s", docs_path)


def label_queued_prs(repos=None, dry_run=True):
    # type: (Optional[List[str]], bool) -> List[Dict]
    """Label all queued PRs with needs-karen.
    
    Returns list of PRs that were labeled.
    """
    if repos is None:
        repos = discover_repos()
    
    labeled = []  # type: List[Dict]
    
    for repo in repos:
        prs = get_open_prs(repo)
        
        for pr in prs:
            state = get_pr_state(pr)
            if state != "queued":
                continue
            
            pr_num = pr.get("number")
            if not pr_num:
                continue
            
            if dry_run:
                logger.info("[DRY-RUN] Would label %s#%d with needs-karen", repo, pr_num)
            else:
                try:
                    result = subprocess.run(
                        ["gh", "pr", "edit", str(pr_num), "--repo", repo, "--add-label", "needs-karen"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if result.returncode == 0:
                        logger.info("Labeled %s#%d with needs-karen", repo, pr_num)
                    else:
                        logger.warning("Failed to label %s#%d: %s", repo, pr_num, result.stderr[:100])
                        continue
                except subprocess.SubprocessError as e:
                    logger.warning("Failed to label %s#%d: %s", repo, pr_num, e)
                    continue
            
            labeled.append({
                "repo": repo,
                "pr": pr_num,
                "sha": pr.get("headRefOid", "")[:12],
                "title": pr.get("title", "")[:50],
            })
    
    return labeled


def main():
    # type: () -> int
    """CLI entry point."""
    import argparse
    
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    
    parser = argparse.ArgumentParser(description="StoryPool backlog management")
    parser.add_argument("--discover", action="store_true", help="Discover repos")
    parser.add_argument("--report", action="store_true", help="Generate backlog report")
    parser.add_argument("--label", action="store_true", help="Label queued PRs with needs-karen")
    parser.add_argument("--live", action="store_true", help="Actually apply labels (default is dry-run)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    
    args = parser.parse_args()
    
    if args.discover:
        repos = discover_repos()
        if args.json:
            print(json.dumps(repos, indent=2))
        else:
            for r in repos:
                print(r)
        return 0
    
    if args.report:
        backlog = collect_backlog()
        if args.json:
            print(json.dumps(backlog, indent=2))
        else:
            print(format_backlog_md(backlog))
        return 0
    
    if args.label:
        labeled = label_queued_prs(dry_run=not args.live)
        if args.json:
            print(json.dumps(labeled, indent=2))
        else:
            for pr in labeled:
                print("{repo}#{pr} ({sha}): {title}".format(**pr))
            print("\nTotal: {} PRs".format(len(labeled)))
        return 0
    
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
