#!/usr/bin/env python3
"""Karen parity harness: compare local Karen to Grok-Karen verdicts.

Runs local Karen on recent PRs that have Grok-Karen verdict comments
and reports agreement rate. Target: 90%+ parity.

Usage:
    python -m cluster.control_plane.parity.karen_parity [--count N] [--repo OWNER/REPO]

Output:
    - Per-PR comparison (local vs Grok verdict)
    - Agreement rate
    - Disagreement analysis
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..dispatcher import REPOS


@dataclass
class GrokVerdict:
    """Parsed Grok-Karen verdict from PR comment."""
    pr_number: int
    repo: str
    verdict: str  # PASS, FAIL, BORDERLINE
    sha: str
    reason: str
    comment_id: int
    comment_body: str


@dataclass
class ParityResult:
    """Result of comparing local Karen to Grok-Karen."""
    pr_number: int
    repo: str
    sha: str
    grok_verdict: str
    local_verdict: str
    agreed: bool
    grok_reason: str
    local_reason: str
    local_findings: Optional[dict] = None


def _run_gh(args: list, default=None):
    """Run gh CLI command and return JSON result."""
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return default
        return json.loads(result.stdout) if result.stdout.strip() else default
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return default


def _extract_grok_verdict(comment_body: str) -> Optional[dict]:
    """Extract verdict from Grok-Karen comment format.
    
    Expected formats:
    - "**PASS**: reason..."
    - "**FAIL**: reason..."
    - "Karen verdict: PASS - reason"
    - JSON block with verdict field
    """
    body_upper = comment_body.upper()
    
    if "**PASS**" in body_upper or "VERDICT: PASS" in body_upper:
        match = re.search(r'\*\*PASS\*\*[:\s]*(.{10,200})', comment_body, re.IGNORECASE)
        reason = match.group(1).strip() if match else "Approved"
        return {"verdict": "PASS", "reason": reason}
    
    if "**FAIL**" in body_upper or "VERDICT: FAIL" in body_upper:
        match = re.search(r'\*\*FAIL\*\*[:\s]*(.{10,200})', comment_body, re.IGNORECASE)
        reason = match.group(1).strip() if match else "Rejected"
        return {"verdict": "FAIL", "reason": reason}
    
    if "**BORDERLINE**" in body_upper or "VERDICT: BORDERLINE" in body_upper:
        match = re.search(r'\*\*BORDERLINE\*\*[:\s]*(.{10,200})', comment_body, re.IGNORECASE)
        reason = match.group(1).strip() if match else "Needs review"
        return {"verdict": "BORDERLINE", "reason": reason}
    
    json_match = re.search(r'\{[\s\S]*?"verdict"[\s\S]*?\}', comment_body)
    if json_match:
        try:
            data = json.loads(json_match.group())
            if "verdict" in data:
                return {
                    "verdict": data["verdict"].upper(),
                    "reason": data.get("reason", ""),
                }
        except json.JSONDecodeError:
            pass
    
    if re.search(r'KARENPASS|karen.*pass|approved', body_upper):
        return {"verdict": "PASS", "reason": "Inferred PASS from comment"}
    if re.search(r'KARENFAIL|karen.*fail|rejected', body_upper):
        return {"verdict": "FAIL", "reason": "Inferred FAIL from comment"}
    
    return None


def find_grok_verdicts(repo: str, max_prs: int = 20) -> list[GrokVerdict]:
    """Find recent PRs with Grok-Karen verdict comments.
    
    Looks for comments from Grok bot or with Karen verdict patterns.
    """
    verdicts = []
    
    prs = _run_gh([
        "pr", "list", "--repo", repo, "--state", "all",
        "--limit", str(max_prs * 2),
        "--json", "number,headRefOid,state,title",
    ], default=[])
    
    if not prs:
        return verdicts
    
    for pr in prs[:max_prs * 2]:
        if len(verdicts) >= max_prs:
            break
            
        pr_number = pr.get("number")
        head_sha = pr.get("headRefOid", "")
        
        comments = _run_gh([
            "pr", "view", str(pr_number), "--repo", repo,
            "--json", "comments",
        ], default={})
        
        comment_list = comments.get("comments", [])
        
        for comment in comment_list:
            body = comment.get("body", "")
            author = comment.get("author", {}).get("login", "")
            
            is_grok = "grok" in author.lower() or "karen" in author.lower()
            has_verdict = any(v in body.upper() for v in ["**PASS**", "**FAIL**", "VERDICT:"])
            
            if not (is_grok or has_verdict):
                continue
            
            extracted = _extract_grok_verdict(body)
            if not extracted:
                continue
            
            sha_match = re.search(r'SHA[:\s]*([a-f0-9]{7,40})', body, re.IGNORECASE)
            verdict_sha = sha_match.group(1) if sha_match else head_sha[:12]
            
            verdicts.append(GrokVerdict(
                pr_number=pr_number,
                repo=repo,
                verdict=extracted["verdict"],
                sha=verdict_sha,
                reason=extracted["reason"][:200],
                comment_id=comment.get("id", 0),
                comment_body=body[:500],
            ))
            break
    
    return verdicts


def run_local_karen(repo: str, pr_number: int) -> Optional[dict]:
    """Run local Karen gate on a PR and return the result."""
    try:
        from ..local_karen.gate import run_gate
        from ..local_karen.config import KarenConfig
        
        owner, _, name = repo.partition("/")
        config = KarenConfig.load()
        
        result = run_gate(owner, name, pr_number, config, post_comment=False)
        
        return {
            "verdict": "PASS" if result.verdict.passed else "FAIL",
            "reason": result.verdict.reason,
            "sha": result.verdict.head_sha,
            "findings": result.verdict.check_results if hasattr(result.verdict, "check_results") else None,
        }
    except ImportError as e:
        return {"verdict": "ERROR", "reason": f"Import error: {e}", "sha": "", "findings": None}
    except Exception as e:
        return {"verdict": "ERROR", "reason": f"Local Karen error: {e}", "sha": "", "findings": None}


def compare_verdicts(grok_verdict: GrokVerdict, local_result: dict) -> ParityResult:
    """Compare Grok verdict to local Karen result."""
    grok_v = grok_verdict.verdict.upper()
    local_v = local_result.get("verdict", "ERROR").upper()
    
    if grok_v == "BORDERLINE":
        agreed = local_v in ("BORDERLINE", "FAIL")
    else:
        agreed = grok_v == local_v
    
    # Agreement is meaningful only when both reviewers saw the same full tip.
    # Missing or abbreviated historical SHAs cannot prove this.
    local_sha = local_result.get("sha", "")
    same_tip = bool(re.fullmatch(r"[a-f0-9]{40}", grok_verdict.sha or "")) and local_sha == grok_verdict.sha
    agreed = agreed and same_tip

    return ParityResult(
        pr_number=grok_verdict.pr_number,
        repo=grok_verdict.repo,
        sha=grok_verdict.sha,
        grok_verdict=grok_v,
        local_verdict=local_v,
        agreed=agreed,
        grok_reason=grok_verdict.reason,
        local_reason=local_result.get("reason", ""),
        local_findings=local_result.get("findings"),
    )


def run_parity_test(repos: list[str], count: int = 10) -> dict:
    """Run parity test across repos.
    
    Returns:
        Dict with results, agreement_rate, and disagreements.
    """
    all_verdicts = []
    
    per_repo = max(1, count // len(repos)) if repos else count
    
    for repo in repos:
        print(f"Finding Grok verdicts in {repo}...", file=sys.stderr)
        verdicts = find_grok_verdicts(repo, max_prs=per_repo)
        all_verdicts.extend(verdicts)
        if len(all_verdicts) >= count:
            break
    
    all_verdicts = all_verdicts[:count]
    
    if not all_verdicts:
        return {
            "results": [],
            "total": 0,
            "agreed": 0,
            "agreement_rate": 0.0,
            "disagreements": [],
            "error": "No Grok-Karen verdicts found",
        }
    
    results = []
    agreed_count = 0
    disagreements = []
    
    for grok_verdict in all_verdicts:
        print(f"Testing {grok_verdict.repo}#{grok_verdict.pr_number}...", file=sys.stderr)
        
        local_result = run_local_karen(grok_verdict.repo, grok_verdict.pr_number)
        
        if local_result is None:
            local_result = {"verdict": "ERROR", "reason": "Local Karen returned None", "sha": ""}
        
        comparison = compare_verdicts(grok_verdict, local_result)
        results.append(comparison)
        
        if comparison.agreed:
            agreed_count += 1
        else:
            disagreements.append({
                "repo": grok_verdict.repo,
                "pr": grok_verdict.pr_number,
                "sha": grok_verdict.sha,
                "grok": grok_verdict.verdict,
                "local": local_result.get("verdict", "ERROR"),
                "grok_reason": grok_verdict.reason[:100],
                "local_reason": local_result.get("reason", "")[:100],
            })
    
    agreement_rate = (agreed_count / len(results)) * 100 if results else 0.0
    
    return {
        "results": [
            {
                "repo": r.repo,
                "pr": r.pr_number,
                "sha": r.sha,
                "grok": r.grok_verdict,
                "local": r.local_verdict,
                "agreed": r.agreed,
            }
            for r in results
        ],
        "total": len(results),
        "agreed": agreed_count,
        "agreement_rate": agreement_rate,
        "disagreements": disagreements,
        "target_met": len(results) >= 10 and agreement_rate >= 90.0,
    }


def format_report(parity_result: dict) -> str:
    """Format parity test results as a human-readable report."""
    lines = [
        "# Karen Parity Report",
        "",
        f"**Total PRs tested:** {parity_result['total']}",
        f"**Agreed:** {parity_result['agreed']}",
        f"**Agreement rate:** {parity_result['agreement_rate']:.1f}%",
        f"**Target (90%):** {'✅ MET' if parity_result.get('target_met') else '❌ NOT MET'}",
        "",
    ]
    
    if parity_result.get("error"):
        lines.extend([
            "## Error",
            f"⚠️ {parity_result['error']}",
            "",
        ])
    
    lines.extend([
        "## Per-PR Results",
        "",
        "| Repo | PR | Grok | Local | Agreed |",
        "|------|-----|------|-------|--------|",
    ])
    
    for r in parity_result.get("results", []):
        agreed_icon = "✅" if r["agreed"] else "❌"
        lines.append(f"| {r['repo']} | #{r['pr']} | {r['grok']} | {r['local']} | {agreed_icon} |")
    
    if parity_result.get("disagreements"):
        lines.extend([
            "",
            "## Disagreements",
            "",
        ])
        for d in parity_result["disagreements"]:
            lines.extend([
                f"### {d['repo']}#{d['pr']}",
                f"- **Grok:** {d['grok']} - {d['grok_reason']}",
                f"- **Local:** {d['local']} - {d['local_reason']}",
                "",
            ])
    
    return "\n".join(lines)


def main() -> int:
    """CLI entry point for parity testing."""
    parser = argparse.ArgumentParser(
        description="Compare local Karen to Grok-Karen verdicts"
    )
    parser.add_argument(
        "--count", "-n",
        type=int,
        default=10,
        help="Number of PRs to test (default: 10)",
    )
    parser.add_argument(
        "--repo", "-r",
        type=str,
        action="append",
        help="Repository to test (can specify multiple; default: all managed repos)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON instead of human-readable report",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        help="Write report to file",
    )
    
    args = parser.parse_args()
    
    repos = args.repo if args.repo else REPOS
    
    result = run_parity_test(repos, count=args.count)
    
    if args.json:
        output = json.dumps(result, indent=2)
    else:
        output = format_report(result)
    
    if args.output:
        args.output.write_text(output)
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(output)
    
    target_met = result.get("target_met", False)
    return 0 if target_met else 1


if __name__ == "__main__":
    raise SystemExit(main())
