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

MAX_UNDRAFTS_PER_TICK = 5

# Web Bob remains draft-only until Jeff explicitly handles readiness/go-live.
# CI green and a needs-karen label are never substitutes for that decision.
MANUAL_READINESS_REPOS = frozenset({
    "rupret007/bob-brain", "rupret007/web-bob",
})


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


def is_ci_all_green(pr):
    # type: (Dict) -> bool
    """Check if all CI checks are passing (green).
    
    Returns True only if there is at least one check AND all checks pass.
    Returns False if any check is pending, failing, or there are no checks.
    """
    ci_checks = pr.get("statusCheckRollup", []) or []
    
    if not ci_checks:
        return False
    
    for check in ci_checks:
        state = (check.get("conclusion") or check.get("state") or "").lower()
        bucket = check.get("bucket", "").lower()
        
        if bucket == "pass" or state in ("success", "neutral"):
            continue
        return False
    
    return True


def is_never_touch_repo(repo):
    # type: (str) -> bool
    """Check if repo is in the never-touch list."""
    return repo.lower() in {e.lower() for e in EXCLUDED_REPOS}


def get_pr_state(pr, verdicts_dir=None):
    # type: (Dict, Optional[Path]) -> str
    """Determine PR state: queued, under_review, passed, failed, blocked, draft_green."""
    is_draft = pr.get("isDraft", False)
    ci_green = is_ci_all_green(pr)
    
    if is_draft:
        if ci_green:
            return "draft_green"
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
        "undrafted_this_tick": [],
        "summary": {
            "total_repos": 0,
            "total_prs": 0,
            "queued": 0,
            "under_review": 0,
            "passed": 0,
            "failed": 0,
            "draft": 0,
            "draft_green": 0,
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
        "| Draft (CI green) | {} |".format(backlog["summary"].get("draft_green", 0)),
        "| Draft (other) | {} |".format(backlog["summary"].get("draft", 0)),
        "| CI Failing | {} |".format(backlog["summary"].get("ci_failing", 0)),
        "| CI Pending | {} |".format(backlog["summary"].get("ci_pending", 0)),
        "",
    ]
    
    undrafted = backlog.get("undrafted_this_tick", [])
    if undrafted:
        lines.extend([
            "## Undrafted This Tick",
            "",
            "| PR | Title | Author | SHA |",
            "|----|-------|--------|-----|",
        ])
        for pr in undrafted:
            lines.append("| [{}#{}]({}) | {} | {} | {} |".format(
                pr.get("repo", ""),
                pr.get("pr", ""),
                pr.get("url", ""),
                pr.get("title", "")[:40],
                pr.get("author", ""),
                pr.get("sha", ""),
            ))
        lines.append("")
    
    lines.extend([
        "## PRs by Repository",
        "",
    ])
    
    state_emoji = {
        "queued": "🔵",
        "under_review": "🟡",
        "passed": "✅",
        "failed": "❌",
        "draft": "📝",
        "draft_green": "🟢",
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


def ensure_label_exists(repo, label, color="d4c5f9", description=""):
    # type: (str, str, str, str) -> bool
    """Ensure a label exists in the repo, creating it if missing.
    
    Returns True if label was created, False if it already existed or failed.
    """
    try:
        result = subprocess.run(
            ["gh", "label", "list", "--repo", repo, "--json", "name"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            labels = json.loads(result.stdout)
            if any(l.get("name", "").lower() == label.lower() for l in labels):
                return False
    except (subprocess.SubprocessError, json.JSONDecodeError):
        pass
    
    try:
        args = ["gh", "label", "create", label, "--repo", repo, "--color", color]
        if description:
            args.extend(["--description", description])
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            logger.info("Created label '%s' in %s", label, repo)
            return True
        return False
    except subprocess.SubprocessError:
        return False


def undraft_green_prs(repos=None, dry_run=True, max_undrafts=None):
    # type: (Optional[List[str]], bool, Optional[int]) -> List[Dict]
    """Mark draft PRs as ready when all CI checks pass.
    
    Only undrafts PRs that:
    - Are drafts
    - Have all CI checks passing (green)
    - Are NOT in never-touch repos
    
    After undrafting, adds needs-karen label.
    Capped at max_undrafts per tick (default MAX_UNDRAFTS_PER_TICK).
    
    Returns list of PRs that were undrafted.
    """
    if repos is None:
        repos = discover_repos()
    
    if max_undrafts is None:
        max_undrafts = MAX_UNDRAFTS_PER_TICK
    
    undrafted = []  # type: List[Dict]
    labels_ensured = set()  # type: set
    
    for repo in repos:
        if len(undrafted) >= max_undrafts:
            logger.info("Reached undraft cap (%d), stopping", max_undrafts)
            break
        
        if is_never_touch_repo(repo):
            logger.debug("Skipping never-touch repo: %s", repo)
            continue
        
        if repo.lower() in MANUAL_READINESS_REPOS:
            logger.info("Keeping Web Bob draft-only pending Jeff: %s", repo)
            continue

        prs = get_open_prs(repo)
        
        for pr in prs:
            if len(undrafted) >= max_undrafts:
                break
            
            if not pr.get("isDraft"):
                continue
            
            if not is_ci_all_green(pr):
                continue
            
            pr_num = pr.get("number")
            if not pr_num:
                continue
            
            pr_info = {
                "repo": repo,
                "pr": pr_num,
                "sha": pr.get("headRefOid", "")[:12],
                "title": pr.get("title", "")[:50],
                "author": (pr.get("author") or {}).get("login", "unknown"),
                "url": pr.get("url", ""),
            }
            
            if dry_run:
                logger.info("[DRY-RUN] Would undraft %s#%d (CI green)", repo, pr_num)
                undrafted.append(pr_info)
                continue
            
            try:
                result = subprocess.run(
                    ["gh", "pr", "ready", str(pr_num), "--repo", repo],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    logger.warning("Failed to undraft %s#%d: %s", repo, pr_num, result.stderr[:100])
                    continue
                
                logger.info("Undrafted %s#%d (CI green)", repo, pr_num)
                
                if repo not in labels_ensured:
                    ensure_label_exists(
                        repo, "needs-karen",
                        color="d4c5f9",
                        description="Ready for Karen gate review"
                    )
                    labels_ensured.add(repo)
                
                label_result = subprocess.run(
                    ["gh", "pr", "edit", str(pr_num), "--repo", repo, "--add-label", "needs-karen"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if label_result.returncode == 0:
                    logger.info("Labeled %s#%d with needs-karen after undraft", repo, pr_num)
                else:
                    logger.warning("Failed to label %s#%d after undraft: %s", 
                                  repo, pr_num, label_result.stderr[:100])
                
                undrafted.append(pr_info)
                
            except subprocess.SubprocessError as e:
                logger.warning("Failed to undraft %s#%d: %s", repo, pr_num, e)
                continue
    
    return undrafted


def _local_verdict_dir():
    # type: () -> Optional[Path]
    try:
        from .local_karen.config import KarenConfig
        path = KarenConfig.load().get_verdict_dir()
    except Exception:
        return None
    return path if path.is_dir() else None


def _has_verdict_for_head(verdict_dir, repo, pr):
    # type: (Path, str, Dict) -> bool
    sha = pr.get("headRefOid", "") or ""
    if len(sha) < 12 or "/" not in repo:
        return True  # cannot tell; keep the label decision
    owner, name = repo.split("/", 1)
    from .local_karen.verdict import load_verdict
    from .local_karen.config import KarenConfig
    from .local_karen.verdict_push import reviewed
    verdict = load_verdict(owner, name, pr.get("number"), sha,
                           KarenConfig(verdict_dir=str(verdict_dir)))
    return reviewed(verdict)


def label_queued_prs(repos=None, dry_run=True):
    # type: (Optional[List[str]], bool) -> List[Dict]
    """Label all queued PRs with needs-karen.
    
    Creates the needs-karen label if it doesn't exist.
    Returns list of PRs that were labeled.
    """
    if repos is None:
        repos = discover_repos()
    
    labeled = []  # type: List[Dict]
    labels_ensured = set()  # type: set
    verdict_dir = _local_verdict_dir()
    
    for repo in repos:
        prs = get_open_prs(repo)
        
        for pr in prs:
            state = get_pr_state(pr)
            if state in ("passed", "failed") and not pr.get("isDraft") \
                    and (verdict_dir is None or not _has_verdict_for_head(verdict_dir, repo, pr)):
                # A karen-pass/karen-fail label from an older commit must not
                # stand in for a verdict at the current head: re-gate it.
                state = "queued"
            if state != "queued":
                continue
            
            pr_num = pr.get("number")
            if not pr_num:
                continue
            
            if dry_run:
                logger.info("[DRY-RUN] Would label %s#%d with needs-karen", repo, pr_num)
            else:
                if repo not in labels_ensured:
                    ensure_label_exists(
                        repo, "needs-karen", 
                        color="d4c5f9", 
                        description="Ready for Karen gate review"
                    )
                    labels_ensured.add(repo)
                
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
    parser.add_argument("--undraft", action="store_true", help="Undraft green draft PRs and label them")
    parser.add_argument("--live", action="store_true", help="Actually apply changes (default is dry-run)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--max-undrafts", type=int, default=MAX_UNDRAFTS_PER_TICK, 
                       help="Max undrafts per tick (default: {})".format(MAX_UNDRAFTS_PER_TICK))
    
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
    
    if args.undraft:
        undrafted = undraft_green_prs(dry_run=not args.live, max_undrafts=args.max_undrafts)
        if args.json:
            print(json.dumps(undrafted, indent=2))
        else:
            for pr in undrafted:
                print("{repo}#{pr} ({sha}): {title}".format(**pr))
            print("\nTotal: {} PRs undrafted".format(len(undrafted)))
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


KAREN_FAILS_PATH = Path("~/.local/share/storypool/karen-fails.md").expanduser()


def collect_karen_failures(repos=None):
    # type: (Optional[List[str]]) -> Dict[str, Any]
    """Collect Karen gate failures across all repos.
    
    Returns dict with failures grouped by reason category.
    """
    if repos is None:
        repos = discover_repos()
    
    failures = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": 0,
        "by_category": {},  # type: Dict[str, List[Dict]]
        "by_repo": {},  # type: Dict[str, List[Dict]]
    }  # type: Dict[str, Any]
    
    for repo in repos:
        prs = get_open_prs(repo)
        
        for pr in prs:
            labels = [l.get("name", "").lower() for l in pr.get("labels", [])]
            if "karen-fail" not in labels and "karenfail" not in labels:
                continue
            
            pr_num = pr.get("number")
            if not pr_num:
                continue
            
            # Try to get verdict details from verdict dir
            verdict_dir = Path("~/.local/share/storypool/verdicts").expanduser()
            if not verdict_dir.exists():
                verdict_dir = Path("~/.local/share/karen/verdicts").expanduser()
            
            reason = "unknown"
            tier = "unknown"
            verdict_file = None
            
            safe_repo = repo.replace("/", "_")
            pattern = f"{safe_repo}_{pr_num}_*_KARENFAIL.json"
            
            if verdict_dir.is_dir():
                matches = list(verdict_dir.glob(pattern))
                if matches:
                    # Get most recent
                    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    verdict_file = matches[0]
                    try:
                        verdict = json.loads(verdict_file.read_text())
                        reason = verdict.get("reason", "unknown")[:100]
                        tier = verdict.get("tier", "unknown")
                    except (json.JSONDecodeError, OSError):
                        pass
            
            # Categorize by reason pattern
            reason_lower = reason.lower()
            if "ci" in reason_lower or "test" in reason_lower or "build" in reason_lower:
                category = "CI/Test Failures"
            elif "security" in reason_lower or "auth" in reason_lower:
                category = "Security Concerns"
            elif "lint" in reason_lower or "format" in reason_lower or "style" in reason_lower:
                category = "Lint/Style Issues"
            elif "agreement" in reason_lower or "reviewer" in reason_lower:
                category = "Review Agreement Missing"
            elif "deterministic" in reason_lower or "check" in reason_lower:
                category = "Deterministic Check Failed"
            else:
                category = "Other"
            
            failure = {
                "repo": repo,
                "pr": pr_num,
                "title": pr.get("title", "")[:60],
                "author": (pr.get("author") or {}).get("login", "unknown"),
                "sha": pr.get("headRefOid", "")[:12],
                "url": pr.get("url", ""),
                "reason": reason,
                "tier": tier,
            }
            
            if category not in failures["by_category"]:
                failures["by_category"][category] = []
            failures["by_category"][category].append(failure)
            
            if repo not in failures["by_repo"]:
                failures["by_repo"][repo] = []
            failures["by_repo"][repo].append(failure)
            
            failures["total"] += 1
    
    return failures


def format_karen_fails_md(failures):
    # type: (Dict[str, Any]) -> str
    """Format Karen failures as markdown, grouped by reason category."""
    lines = [
        "# Karen Gate Failures Report",
        "",
        f"Generated: {failures['timestamp']}",
        f"Total failures: {failures['total']}",
        "",
    ]
    
    # Group by category
    lines.append("## Failures by Category")
    lines.append("")
    
    for category in sorted(failures["by_category"].keys()):
        fails = failures["by_category"][category]
        lines.append(f"### {category} ({len(fails)})")
        lines.append("")
        lines.append("| PR | Title | Author | Reason |")
        lines.append("|----|-------|--------|--------|")
        
        for f in fails:
            reason_short = f["reason"][:50] + "..." if len(f["reason"]) > 50 else f["reason"]
            lines.append(f"| [{f['repo']}#{f['pr']}]({f['url']}) | {f['title'][:30]} | {f['author']} | {reason_short} |")
        
        lines.append("")
    
    # Group by repo
    lines.append("## Failures by Repository")
    lines.append("")
    
    for repo in sorted(failures["by_repo"].keys()):
        fails = failures["by_repo"][repo]
        lines.append(f"### {repo} ({len(fails)})")
        lines.append("")
        
        for f in fails:
            lines.append(f"- **#{f['pr']}** - {f['title'][:40]}")
            lines.append(f"  - Author: {f['author']}, Tier: {f['tier']}")
            lines.append(f"  - Reason: {f['reason']}")
            lines.append(f"  - URL: {f['url']}")
            lines.append("")
    
    lines.extend([
        "---",
        "",
        "*This report is auto-generated by StoryPool.*",
    ])
    
    return "\n".join(lines)


def write_karen_fails_report(failures=None, output_path=None):
    # type: (Optional[Dict[str, Any]], Optional[Path]) -> Path
    """Write Karen failures report to markdown file.
    
    Returns path to written file.
    """
    if failures is None:
        failures = collect_karen_failures()
    
    path = output_path or KAREN_FAILS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    
    md = format_karen_fails_md(failures)
    path.write_text(md)
    
    logger.info("Wrote Karen failures report to %s (%d failures)", path, failures["total"])
    return path


if __name__ == "__main__":
    raise SystemExit(main())
