"""Bounded GitHub discovery reads; cached data never authorizes a merge.

Conditional requests follow GitHub's REST best practices. Mutable head/CI gate
reads are deliberately uncached. The shared budget is mandatory on cache miss.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

from . import github_budget as budget


class GitHubReadError(RuntimeError):
    pass


def limited(result):
    text = ((result.stderr or '') + (result.stdout or '')).lower()
    return any(s in text for s in ('rate limit', 'rate_limit', 'secondary rate', 'abuse detection'))


def run(args, timeout=30, resource=None, deadline=None, *, background=False):
    """Budget CLI operations; mixed commands reserve both API resources."""
    if type(background) is not bool:
        raise budget.GitHubDeferred('GitHub request priority invalid; requests paused')
    if resource:
        resources = (resource,)
    elif args[:1] in (['api'], ['label']):
        resources = ('core',)
    elif args[:2] in (['pr', 'view'], ['pr', 'list'], ['pr', 'checks'], ['repo', 'list']):
        resources = ('graphql',)
    else:
        resources = ('core', 'graphql')
    for name in resources:
        if background:
            budget.reserve(name, background=True)
        else:
            budget.reserve(name)
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            raise subprocess.TimeoutExpired(['gh'] + args, 0)
    result = subprocess.run(['gh'] + args, capture_output=True, text=True, timeout=timeout)
    if result.returncode and limited(result):
        for name in resources:
            budget.observe(resource=name, limited=True)
        raise budget.GitHubDeferred('GitHub rate limit; work deferred with backoff')
    return result


def rest_json(endpoint, ttl=60):
    """Serialize same-endpoint reads across local services, with ETag refresh."""
    if not isinstance(endpoint, str) or not re.fullmatch(r'[A-Za-z0-9_./?=&%+:-]+', endpoint) or endpoint.startswith(('/', 'http')):
        raise ValueError('Expected a relative GitHub API endpoint')
    root = Path(os.environ.get('STORYPOOL_DATA_DIR', '~/.local/share/storypool')).expanduser() / 'github-cache'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = hashlib.sha256(endpoint.encode()).hexdigest()
    path = root / (key + '.json')
    with (root / (key + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            cached = json.loads(path.read_text())
        except (OSError, ValueError):
            cached = {}
        if 'value' in cached and 0 <= time.time() - cached.get('at', 0) < ttl:
            return cached['value']
        resource = 'search' if endpoint.startswith('search/') else 'core'
        # Cached discovery is background work; gate reads use uncached run().
        budget.reserve(resource, background=True)
        args = ['gh', 'api', '--include', '--method', 'GET', endpoint]
        if cached.get('etag'):
            args += ['--header', 'If-None-Match: ' + cached['etag']]
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        raw = result.stdout.replace('\r\n', '\n')
        headers = {}
        status = 200
        if raw.startswith('HTTP/'):
            block, _, raw = raw.partition('\n\n')
            status = int(block.splitlines()[0].split()[1])
            headers = {line.split(':', 1)[0].lower(): line.split(':', 1)[1].strip()
                       for line in block.splitlines()[1:] if ':' in line}
        rate_limited = status == 429 or (status == 403 and (headers.get('x-ratelimit-remaining') == '0' or limited(result)))
        budget.observe(headers, resource=resource, limited=rate_limited)
        if rate_limited or (result.returncode and limited(result)):
            if not rate_limited:
                budget.observe(resource=resource, limited=True)
            raise budget.GitHubDeferred('GitHub rate limit; work deferred with backoff')
        if status == 304 and 'value' in cached:
            value = cached['value']
        elif result.returncode == 0 and status == 200:
            try:
                value = json.loads(raw)
            except ValueError:
                raise GitHubReadError('GitHub returned invalid JSON') from None
        else:
            raise GitHubReadError('GitHub REST read failed')
        etag = headers.get('etag', cached.get('etag', ''))
        if '\n' in etag or '\r' in etag:
            raise GitHubReadError('Invalid GitHub cache validator')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'value': value, 'at': time.time(), 'etag': etag}))
        temporary.chmod(0o600)
        temporary.replace(path)
        return value


def active_repos(owner):
    if not re.fullmatch(r'[A-Za-z0-9-]+', owner):
        raise ValueError('Invalid GitHub owner')
    repos = set()
    # Search is an index of open PRs, not a gate: exact-head live reads still
    # precede review, undraft and merge. Refuse incomplete search results.
    for page in range(1, 11):
        data = rest_json('search/issues?q=' + quote('is:pr is:open user:' + owner, safe='') + '&per_page=100&page=' + str(page), ttl=120)
        if not isinstance(data, dict) or data.get('incomplete_results') or data.get('total_count', 0) > 1000:
            raise GitHubReadError('Open PR discovery incomplete; retry later')
        for item in data.get('items', []):
            url = item.get('repository_url', '')
            prefix = 'https://api.github.com/repos/'
            name = url[len(prefix):] if url.startswith(prefix) else ''
            if re.fullmatch(re.escape(owner) + r'/[A-Za-z0-9_.-]+', name, re.I):
                repos.add(name)
        if page * 100 >= data.get('total_count', 0):
            return [{'nameWithOwner': repo} for repo in sorted(repos)]
    raise GitHubReadError('Open PR discovery exceeds page budget')


def labels(repo):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Invalid repository')
    result = []
    for page in range(1, 11):
        batch = rest_json('repos/' + repo + '/labels?per_page=100&page=' + str(page), ttl=3600)
        if not isinstance(batch, list):
            raise GitHubReadError('Invalid label response')
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise GitHubReadError('Label list exceeds page budget')


def refresh_quota():
    data = rest_json('rate_limit', ttl=300)
    for resource in ('core', 'graphql', 'search'):
        values = data.get('resources', {}).get(resource, {})
        budget.observe({'x-ratelimit-' + name: str(values[name])
                        for name in ('remaining', 'limit', 'reset') if name in values}, resource=resource)
