"""Whole-queue review scheduling; metadata never supplies gate authority."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .. import github_reads
from ..github_budget import GitHubDeferred

MAX_REVIEWS_PER_CYCLE = 3
ADMISSION_SECONDS = 20 * 60
METADATA_SECONDS = 30
METADATA_BATCH = 25
SMALL_LINES = 300


@dataclass
class Candidate:
    repo: str
    pr: dict
    lines: object = None
    ci: str = 'unknown'

    @property
    def key(self):
        return '{}#{}@{}'.format(self.repo, self.pr.get('number'), self.pr.get('headRefOid', ''))


def enrich(candidates, max_batches=None):
    """At most thirty seconds of optional, budgeted metadata across the whole queue."""
    deadline = time.monotonic() + METADATA_SECONDS
    valid = [candidate for candidate in candidates
             if re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', candidate.repo)
             and type(candidate.pr.get('number')) is int and candidate.pr['number'] > 0
             and isinstance(candidate.pr.get('headRefOid'), str)
             and re.fullmatch(r'[0-9a-f]{40}', candidate.pr['headRefOid'])]
    if max_batches is not None:
        if type(max_batches) is not int or max_batches <= 0:
            raise ValueError('Invalid metadata batch bound')
        valid = valid[:max_batches * METADATA_BATCH]
    for offset in range(0, len(valid), METADATA_BATCH):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        batch = valid[offset:offset + METADATA_BATCH]
        fields = []
        for index, candidate in enumerate(batch):
            owner, repo = candidate.repo.split('/')
            fields.append('r%d:repository(owner:%s,name:%s){pullRequest(number:%d){'
                          'headRefOid additions deletions commits(last:1){nodes{commit{'
                          'statusCheckRollup{state}}}}}}' %
                          (index, json.dumps(owner), json.dumps(repo), candidate.pr['number']))
        query = 'query{' + ''.join(fields) + '}'
        try:
            response = github_reads.run(['api', 'graphql', '-f', 'query=' + query],
                                        timeout=min(remaining, 10), resource='graphql', background=True)
            if response.returncode:
                continue
            data = json.loads(response.stdout).get('data') or {}
            for index, candidate in enumerate(batch):
                value = (data.get('r%d' % index) or {}).get('pullRequest') or {}
                if value.get('headRefOid') != candidate.pr.get('headRefOid'):
                    continue
                sizes = [value.get('additions'), value.get('deletions')]
                if all(type(size) is int and size >= 0 for size in sizes):
                    candidate.lines = sum(sizes)
                nodes = (value.get('commits') or {}).get('nodes') or []
                state = (((nodes[0].get('commit') or {}).get('statusCheckRollup') or {}).get('state') if nodes else None)
                candidate.ci = {'SUCCESS': 'green', 'FAILURE': 'red', 'ERROR': 'red',
                                'PENDING': 'pending', 'EXPECTED': 'pending'}.get(state, 'unknown')
        except GitHubDeferred:
            raise  # A shared-budget deferral must not launch doomed gate reads.
        except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError,
                github_reads.GitHubReadError):
            continue  # Missing scheduling metadata stays unknown, never green.
    return candidates


class ReviewQueue:
    """Persist only scheduling deferrals, independently of review/retry authority."""

    def __init__(self, config):
        self.path = config.get_state_dir() / 'review-queue.json'
        try:
            if self.path.stat().st_size > 1024 * 1024:
                raise ValueError('Oversized review queue state')
            value = json.loads(self.path.read_text())
            self.deferred = {key: count for key, count in value.get('deferred', {}).items()
                             if isinstance(key, str) and type(count) is int and count >= 0}
        except (OSError, ValueError, TypeError, AttributeError):
            self.deferred = {}

    def order(self, candidates):
        def priority(candidate):
            green = candidate.ci == 'green'
            small = candidate.lines is not None and candidate.lines < SMALL_LINES
            tier = 0 if green and small else 1 if green else 2 if candidate.ci != 'red' else 3
            return (tier, candidate.lines if candidate.lines is not None else float('inf'),
                    candidate.repo, candidate.pr.get('number', 0))

        ordered = sorted(candidates, key=priority)
        aged = [candidate for candidate in ordered if self.deferred.get(candidate.key, 0) >= 2]
        # Reserve the first admission for one explicitly large deferred change.
        # If metadata was unavailable, also give old unknown-sized work a turn.
        large = [candidate for candidate in aged
                 if candidate.lines is not None and candidate.lines >= SMALL_LINES]
        if large or aged:
            oldest = min(large or aged,
                         key=lambda candidate: (-self.deferred[candidate.key], candidate.key))
            ordered.remove(oldest)
            ordered.insert(0, oldest)
        return ordered

    def begin(self, candidates, observed_repos=None):
        previous = self.deferred
        # A scheduled partial scan can reconcile heads only for repos whose
        # complete listing succeeded; unscanned/failed repos retain their age.
        self.deferred = ({key: count for key, count in previous.items()
                          if key.split('#', 1)[0] not in observed_repos}
                         if observed_repos is not None else {})
        self.deferred.update({candidate.key: min(previous.get(candidate.key, 0) + 1, 1000000)
                              for candidate in candidates})
        self.save()

    def attempted(self, candidate):
        self.deferred[candidate.key] = 0
        self.save()

    def save(self):
        encoded = json.dumps({'deferred': self.deferred})
        if len(self.deferred) > 10000 or len(encoded.encode('utf-8')) > 1024 * 1024:
            raise OSError('Review scheduling state exceeds bound')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp = tempfile.mkstemp(dir=str(self.path.parent), prefix='.review-queue-')
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(encoded)
            os.replace(temp, self.path)
        finally:
            Path(temp).unlink(missing_ok=True)
