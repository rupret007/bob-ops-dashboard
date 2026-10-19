"""Refresh obsolete CI-only repair evidence without granting review authority.

A changed CI result can make a same-head FAIL obsolete. It never becomes a
PASS here: only the normal claimed Karen gate can publish a replacement.
"""
from __future__ import annotations

import json
import os
import re
import time

from ..never_touch import is_never_touch
from . import github_reads
from .coordination import ClaimClient, Lease
from .local_karen.config import KarenConfig
from .local_karen.verdict import load_verdict
from .local_karen.verdict_push import reviewed
from .pr_ci import ci_status_from_pr


def ci_is_only_repair_finding(verdict):
    """BORDERLINE remains review uncertainty; concrete FAIL findings need repair."""
    try:
        if verdict.passed is not False or not reviewed(verdict):
            return False
        checks = verdict.check_results
        if (not isinstance(checks, list) or not checks
                or any(not isinstance(c, dict)
                       or c.get('verdict') not in ('PASS', 'FAIL', 'SKIP') for c in checks)):
            return False
        failed = [c for c in checks if c.get('verdict') == 'FAIL']
        if not failed or any(c.get('check') != 'ci_green' for c in failed):
            return False
        # Do not discard BORDERLINE findings or infer their resolution. A new
        # full review must judge them; this only removes an obsolete CI repair.
        return all(r.get('verdict') in ('PASS', 'BORDERLINE')
                   for r in verdict.review_results)
    except (AttributeError, TypeError, ValueError):
        return False


def ci_failure_for_head(repo, number, sha, verdict_dir=None):
    if (not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo)
            or is_never_touch(repo)
            or type(number) is not int or number < 1
            or not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha)):
        return False
    owner, name = repo.split('/')
    config = KarenConfig(verdict_dir=str(verdict_dir)) if verdict_dir is not None else KarenConfig.load()
    verdict = load_verdict(owner, name, number, sha, config)
    return (verdict is not None and verdict.owner == owner and verdict.repo == name
            and verdict.pr_number == number and verdict.head_sha == sha
            and ci_is_only_repair_finding(verdict))


def current_ci(pr, sha):
    """A single current PR response ties the CI rollup to its full head SHA."""
    if (not isinstance(pr, dict) or pr.get('headRefOid') != sha):
        return 'head-changed'
    if pr.get('state') != 'OPEN' or pr.get('isDraft') is not False:
        return 'ineligible'
    ci = ci_status_from_pr(pr)
    if not ci:
        return 'unknown'
    if 'pending' in ci.values():
        return 'pending'
    return 'red' if 'failure' in ci.values() else 'green'


def observe_current_ci(repo, number, sha, timeout=10):
    if timeout <= 0:
        return 'unknown', {}
    result = github_reads.run(['pr', 'view', str(number), '--repo', repo, '--json',
                              'state,isDraft,headRefOid,statusCheckRollup,labels'],
                             timeout=min(timeout, 10))
    if result.returncode:
        return 'unknown', {}
    pr = json.loads(result.stdout)
    return current_ci(pr, sha), pr


def repair_preflight(repo, number, sha, deadline):
    """Only CI-only repair evidence needs an extra read; total tick work is bounded."""
    if not ci_failure_for_head(repo, number, sha):
        return 'repair'
    state, _ = observe_current_ci(repo, number, sha, deadline - time.monotonic())
    return 'repair' if state == 'red' else state


def refresh_candidate(repo, pr, verdict_dir):
    """Cached discovery selects work; fresh checks under a lease authorize queueing."""
    sha = pr.get('headRefOid')
    # get_open_prs supplies only open PRs but does not request the state field.
    details = {**pr, 'state': pr.get('state', 'OPEN')}
    return (current_ci(details, sha) == 'green'
            and ci_failure_for_head(repo, pr.get('number'), sha, verdict_dir))


def queue_refresh(repo, number, sha, verdict_dir):
    """Queue a fresh review, preserving verdicts/retry history and shared ownership."""
    if not ci_failure_for_head(repo, number, sha, verdict_dir):
        return False
    client = ClaimClient.from_env()
    claim = client.acquire(repo, number, sha, os.environ.get('STORYPOOL_MACHINE', 'mini'))
    with Lease(client, claim) as lease:
        state, pr = observe_current_ci(repo, number, sha)
        if state != 'green' or not ci_failure_for_head(repo, number, sha, verdict_dir):
            return False
        if any(isinstance(label, dict) and label.get('name') == 'needs-karen'
               for label in pr.get('labels', [])):
            return False
        from .backlog import ensure_label_exists
        lease.renew()
        ensure_label_exists(repo, 'needs-karen', color='d4c5f9',
                            description='Ready for Karen gate review')
        lease.renew()
        queued = github_reads.run(['pr', 'edit', str(number), '--repo', repo,
                                  '--add-label', 'needs-karen'], timeout=10)
        return queued.returncode == 0
