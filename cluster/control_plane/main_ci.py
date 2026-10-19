"""Restart-safe main CI checks, using GitHub as the durable merge authority."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cluster.never_touch import check_merge_allowed
from .coordination import ClaimClient, CoordinationError, Lease
from . import github_reads
from .github_budget import GitHubDeferred

_deadline = ContextVar('main_ci_deadline', default=None)
RECEIPT_SECONDS = 600
RENEW_AFTER_SECONDS = 480


def command(argv, cwd=None):
    deadline = _deadline.get()
    timeout = 120 if deadline is None else min(120, deadline - time.monotonic())
    if timeout <= 0:
        raise subprocess.TimeoutExpired(argv, 0)
    if argv[0] == 'gh':
        resource = 'search' if argv[1:2] == ['search'] else None
        api_read = (argv[1:2] == ['api'] and len(argv) > 2
                    and all(arg in ('--paginate', '--slurp') or arg.startswith('repos/')
                            for arg in argv[2:]))
        # This module also serves immediate merge validation. Only scheduled
        # sweeps carry a batch deadline and use the background allowance.
        background = (deadline is not None and (api_read
                      or argv[1:3] in (['pr', 'view'], ['pr', 'list'], ['search', 'prs'])))
        result = github_reads.run(argv[1:], timeout=timeout, resource=resource, deadline=deadline,
                                  background=background)
        result.check_returncode()
        return result.stdout.strip()
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          check=True, timeout=timeout).stdout.strip()


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
    except GitHubDeferred:
        if _deadline.get() is not None:
            raise  # The bounded batch can distinguish minute waits from primary/backoff.
        return False, 'deferred'
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


def recent_prs():
    since = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime('%Y-%m-%dT%H:%M:%SZ')
    prs = gh_json('search', 'prs', '--owner', 'rupret007', '--merged',
                  '--merged-at', '>=' + since, '--limit', '100', '--json', 'number,repository')
    return [pr for pr in prs if check_merge_allowed(pr['repository']['nameWithOwner'])[0]]


def reconcile_one(client, pr):
    repo, number = pr['repository']['nameWithOwner'], pr['number']
    try:
        details = merged_pr(repo, number)
        claim = client.acquire(repo, number, details['headRefOid'],
                               os.environ.get('STORYPOOL_MACHINE', 'unknown'))
        with Lease(client, claim) as lease:
            status = observe(repo, number, details, lease)
    except GitHubDeferred as exc:
        return {'repo': repo, 'pr': number, 'main_ci': 'deferred',
                'budget_code': exc.code, 'retry_after': exc.retry_after}
    except (CoordinationError, OSError, subprocess.SubprocessError, ValueError, KeyError):
        status = 'deferred'
    return {'repo': repo, 'pr': number, 'main_ci': status}


def reconcile_recent():
    """Every watchdog tick rechecks GitHub receipts, including after restarts."""
    client = ClaimClient.from_env()  # No reads or mutations if coordination is absent.
    return [reconcile_one(client, pr) for pr in recent_prs()]


def reconcile_batch(root):
    """Bound probe work, persisting scheduling progress rather than approval.

    Old or missing observations remain unconfirmed. Every attempted action
    still re-fetches GitHub and acquires its exact-head cluster claim.
    """
    from .health_state import atomic_json
    from .health_peer import number
    path = Path(root) / 'main-ci-progress.json'
    try:
        saved = json.loads(path.read_text()) if path.stat().st_size <= 1024 * 1024 else {}
        records = saved.get('records', {}) if isinstance(saved, dict) else {}
        if not isinstance(records, dict):
            records = {}
    except (OSError, ValueError):
        records = {}

    token = _deadline.set(time.monotonic() + 120)
    try:
        client = ClaimClient.from_env()
        while True:
            try:
                prs = recent_prs()
                break
            except GitHubDeferred as exc:
                if _wait_for_minute(exc.code, exc.retry_after):
                    continue
                return dict(results=[], attempted=0, total=len(records),
                            unconfirmed=max(1, len(records)), discovery_deferred=True)
        key = lambda pr: '{}#{}'.format(pr['repository']['nameWithOwner'], pr['number'])
        records = {key(pr): records.get(key(pr), {}) for pr in prs}
        def checked(pr):
            row = records[key(pr)]
            value = row.get('checked_at') if isinstance(row, dict) else None
            valid = (isinstance(row, dict) and row.get('repo') == pr['repository']['nameWithOwner']
                     and row.get('pr') == pr['number'] and row.get('main_ci') in
                     ('success', 'recovered', 'ineligible', 'failure', 'unknown', 'pending'))
            return value if valid and number(value) and 0 <= time.time() - value <= RECEIPT_SECONDS else 0
        def attempted_at(pr):
            row = records[key(pr)]
            value = row.get('attempted_at') if isinstance(row, dict) else None
            return value if number(value) and 0 <= value <= time.time() else 0
        # Least recently attempted first; a slow PR cannot monopolize each tick.
        due = sorted(prs, key=attempted_at)
        attempted = set()
        stopped = False
        for pr in due:
            previous_checked = checked(pr)
            if previous_checked and time.time() - previous_checked < RENEW_AFTER_SECONDS:
                continue
            if time.monotonic() >= _deadline.get():
                break
            while time.monotonic() < _deadline.get():
                result = reconcile_one(client, pr)
                now = time.time()
                attempted.add(key(pr))
                if result['main_ci'] == 'deferred' and checked(pr):
                    # An admission deferral supplies no replacement observation.
                    # Preserve the old receipt only within its original 600s lifetime.
                    records[key(pr)] = dict(records[key(pr)], last_deferred_at=now)
                else:
                    records[key(pr)] = dict(result, attempted_at=now,
                                            checked_at=now if result['main_ci'] != 'deferred' else 0)
                atomic_json(path, dict(records=records))
                if result.get('budget_code'):
                    if _wait_for_minute(result['budget_code'], result.get('retry_after')):
                        continue  # reconcile_one already exited its PR lease.
                    stopped = True
                break
            if stopped:
                break
        results = [records[key(pr)] if checked(pr) else dict(repo=pr['repository']['nameWithOwner'],
                    pr=pr['number'], main_ci='deferred') for pr in prs]
        return dict(results=results, attempted=len(attempted), total=len(prs),
                    unconfirmed=sum(r['main_ci'] in ('deferred', 'unknown', 'pending') for r in results))
    finally:
        _deadline.reset(token)


def _wait_for_minute(code, retry_after):
    """Only shared-minute exhaustion can wait, after the PR lease is released."""
    deadline = _deadline.get()
    if code != 'minute-exhausted' or deadline is None:
        return False
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    delay = retry_after if type(retry_after) in (int, float) and retry_after > 0 else 5
    time.sleep(min(5, delay, remaining))
    return time.monotonic() < deadline
