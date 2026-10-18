"""Restart-safe main CI checks, using GitHub as the durable merge authority."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cluster.never_touch import check_merge_allowed
from .coordination import ClaimClient, CoordinationError, Lease


def command(argv, cwd=None):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          check=True, timeout=120).stdout.strip()


def gh_json(*args):
    return json.loads(command(['gh', *args]))


def check_ci(repo, ref='main'):
    """Require completed green checks/statuses; absence and errors are unknown."""
    try:
        checks = gh_json('api', '--paginate', '--slurp', f'repos/{repo}/commits/{ref}/check-runs?per_page=100')
        status = gh_json('api', f'repos/{repo}/commits/{ref}/status')
        runs = [run for page in checks for run in page['check_runs']]
        # Names are shared across workflows. Only attempts from the same
        # check suite may supersede each other; otherwise a green workflow
        # can hide an unrelated red workflow with the same job name.
        latest = {}
        for run in runs:
            suite = (run.get('check_suite') or {}).get('id')
            key = (run.get('app', {}).get('id'), run['name'],
                   ('suite', suite) if suite is not None else ('run', run['id']))
            if run['id'] > latest.get(key, {}).get('id', -1):
                latest[key] = run
        states = [r.get('conclusion') if r.get('status') == 'completed' else 'pending'
                  for r in latest.values()]
        if status.get('statuses'):
            states.append(status.get('state'))
        if any(s in ('failure', 'error', 'timed_out', 'cancelled', 'action_required', 'startup_failure') for s in states):
            return False, 'failure'
        if states and all(s in ('success', 'neutral', 'skipped') for s in states):
            return True, 'success'
        return False, 'pending' if states else 'unknown'
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return False, 'unknown'


def merged_pr(repo, number):
    return gh_json('pr', 'view', str(number), '--repo', repo, '--json',
                   'number,state,isDraft,headRefOid,mergeCommit,baseRefName,mergedAt')


def open_revert(repo, number, sha, base, lease):
    """Create an actual revert commit on a new branch, then a Karen-gated PR."""
    if not check_merge_allowed(repo)[0] or not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('Ineligible revert target')
    branch = 'storypool-revert-' + sha
    lease.renew()
    existing = gh_json('pr', 'list', '--repo', repo, '--head', branch,
                       '--state', 'all', '--json', 'url')
    if existing:
        return existing[0]['url']
    with tempfile.TemporaryDirectory(prefix='storypool-revert-') as directory:
        path = str(Path(directory) / 'repo')
        command(['git', 'clone', '--quiet', '--single-branch', '--branch', base,
                 f'https://github.com/{repo}.git', path])
        lease.renew()
        command(['git', 'checkout', '-b', branch], path)
        command(['git', '-c', 'user.name=StoryPool', '-c',
                 'user.email=storypool@users.noreply.github.com',
                 'revert', '--no-edit', sha], path)
        lease.renew()
        # Push only the new branch; no main update and no force option.
        try:
            command(['git', 'push', 'origin', f'HEAD:refs/heads/{branch}'], path)
        except subprocess.CalledProcessError:
            # Recover a crash after branch publication but before PR creation.
            # Rebuilt commits can have different timestamps; only identical
            # trees may reuse the already published branch. Never overwrite it.
            lease.renew()
            command(['git', 'fetch', 'origin', f'refs/heads/{branch}'], path)
            local_tree = command(['git', 'rev-parse', 'HEAD^{tree}'], path)
            remote_tree = command(['git', 'rev-parse', 'FETCH_HEAD^{tree}'], path)
            if local_tree != remote_tree:
                raise ValueError('Existing revert branch differs; operator review required')

    lease.renew()
    return command(['gh', 'pr', 'create', '--repo', repo, '--head', branch,
                    '--base', base, '--title', f'Revert #{number}: main CI failed',
                    '--body', f'StoryPool detected failed main CI at {sha} after #{number}. '
                    'This revert requires the exact-head, two-reviewer Karen gate before merge.',
                    '--label', 'needs-karen'])


def observe(repo, number, details, lease):
    """Pin CI and any revert to the squash commit, never the PR head."""
    if not check_merge_allowed(repo)[0] or details.get('state') != 'MERGED':
        return 'ineligible'
    sha = (details.get('mergeCommit') or {}).get('oid', '')
    if not re.fullmatch('[0-9a-f]{40}', sha):
        return 'unknown'
    lease.renew()
    green, status = check_ci(repo, sha)
    if not green and status == 'failure':
        base = details['baseRefName']
        current = gh_json('api', f'repos/{repo}/commits/{base}')['sha']
        if current != sha:
            current_ok, current_status = check_ci(repo, current)
            if current_ok:
                return 'recovered'
            if current_status != 'failure':
                return current_status
        open_revert(repo, number, sha, base, lease)
    return status


def reconcile_recent():
    """Every watchdog tick rechecks GitHub receipts, including after restarts."""
    since = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime('%Y-%m-%dT%H:%M:%SZ')
    results = []
    client = ClaimClient.from_env()  # No reads or mutations if coordination is absent.
    prs = gh_json('search', 'prs', '--owner', 'rupret007', '--merged',
                  '--merged-at', '>=' + since, '--limit', '100', '--json', 'number,repository')
    for pr in prs:
        repo, number = pr['repository']['nameWithOwner'], pr['number']
        if not check_merge_allowed(repo)[0]:
            continue
        try:
            details = merged_pr(repo, number)
            claim = client.acquire(repo, number, details['headRefOid'],
                                   os.environ.get('STORYPOOL_MACHINE', 'unknown'))
            with Lease(client, claim) as lease:
                status = observe(repo, number, details, lease)
            results.append({'repo': repo, 'pr': number, 'main_ci': status})
        except (CoordinationError, OSError, subprocess.SubprocessError, ValueError, KeyError):
            results.append({'repo': repo, 'pr': number, 'main_ci': 'deferred'})
    return results
