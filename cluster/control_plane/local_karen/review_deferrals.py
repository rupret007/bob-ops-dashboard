"""Scheduling refusals contain no review or repair authority."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

from .context_diagnostics import collect_context_diagnostics, sanitize_context_diagnostics

LIMIT = 1024 * 1024
KEY = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[1-9][0-9]*@[0-9a-f]{40}')
DIGEST = re.compile(r'[0-9a-f]{64}')
REASON = 'Repeated unchanged context refusal; awaiting head or reviewer-policy change'


def policy_identity(config):
    """Bind code, public reviewer choices and limits; never credential locations."""
    fields = ('reviewer_order', 'codex_effort_default', 'codex_effort_high_risk',
              'gemini_model', 'minimax_model', 'budget_per_run_usd', 'run_local_tests',
              'claude_enabled', 'claude_daily_cap')
    value = {name: getattr(config, name) for name in fields}
    value['model'] = os.environ.get('STORYPOOL_KAREN_MODEL', 'gpt-6-astra')
    value['effort'] = os.environ.get('STORYPOOL_KAREN_EFFORT', 'max')
    value['code'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(Path(__file__).parent.glob('*.py'))}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate scheduling key')
        result[key] = value
    return result


class ReviewDeferrals:
    def __init__(self, config):
        self.path = config.get_state_dir() / 'review-deferrals.json'
        self.policy = policy_identity(config)
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            self.records = {}
            return
        try:
            with os.fdopen(fd) as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_mode & 0o077 or info.st_size > LIMIT):
                    raise ValueError('Invalid scheduling file')
                self.records = json.loads(stream.read(LIMIT + 1), object_pairs_hook=_unique)
            if type(self.records) is not dict or len(self.records) > 10000:
                raise ValueError('Invalid scheduling entries')
            for key, row in self.records.items():
                if (not KEY.fullmatch(key) or type(row) is not dict
                        or set(row) != {'policy', 'signature', 'count', 'diagnostics'}
                        or not all(type(row[k]) is str and DIGEST.fullmatch(row[k])
                                   for k in ('policy', 'signature'))
                        or type(row['count']) is not int or row['count'] not in (1, 2)
                        or not row['diagnostics']
                        or row['diagnostics'] != sanitize_context_diagnostics(row['diagnostics'])
                        or row['signature'] != hashlib.sha256(json.dumps(sorted(
                            json.dumps(item, sort_keys=True) for item in row['diagnostics']
                        )).encode()).hexdigest()):
                    raise ValueError('Invalid scheduling entry')
        except (ValueError, TypeError, KeyError) as exc:
            raise OSError('Context deferral state unavailable') from exc

    def reconcile(self, candidates, observed_repos):
        # This is a label-filtered listing. Absence does not prove closure or a
        # changed head; auto-labeling may return the same head on a later cycle.
        current = {item.key.rsplit('@', 1)[0]: item.key for item in candidates
                   if item.repo in observed_repos}
        kept = {key: row for key, row in self.records.items()
                if row['policy'] == self.policy
                and (key.rsplit('@', 1)[0] not in current
                     or current[key.rsplit('@', 1)[0]] == key)}
        if kept != self.records:
            self.records = kept
            self.save()

    def blocked(self, candidate):
        row = self.records.get(candidate.key, {})
        return row.get('policy') == self.policy and row.get('count') == 2

    def observe(self, candidate, result):
        verdict = getattr(result, 'verdict', None)
        if (not KEY.fullmatch(candidate.key) or verdict is None
                or getattr(verdict, 'head_sha', None) != candidate.pr['headRefOid']
                or getattr(verdict, 'pr_number', None) != candidate.pr['number']
                or (getattr(verdict, 'owner', None), getattr(verdict, 'repo', None))
                != tuple(candidate.repo.split('/'))):
            return
        diagnostics = sanitize_context_diagnostics(getattr(result, 'context_diagnostics', []))
        reviews = getattr(verdict, 'review_results', [])
        if type(reviews) is list:
            diagnostics += collect_context_diagnostics([
                SimpleNamespace(reviewer=row.get('reviewer'), scope=row.get('scope'))
                for row in reviews if type(row) is dict and type(row.get('scope')) is dict
                and row['scope'].get('context_incomplete') is True])
        stable = [row for row in sanitize_context_diagnostics(diagnostics)
                  if row.get('source_context_error') in
                  ('source_identity_or_path', 'source_private', 'source_nontext')
                  or row.get('source_metadata_reason') in ('source_file_limit', 'source_total_limit')]
        if not stable or getattr(verdict, 'passed', None) is not False:
            if (getattr(verdict, 'status', None) == 'COMPLETE'
                    and not getattr(result, 'error', None) and not stable
                    and candidate.key in self.records):
                del self.records[candidate.key]
                self.save()
            return
        # Fixed metadata only: no source paths, model prose, findings or replies.
        stable = sorted({json.dumps(row, sort_keys=True) for row in stable})
        signature = hashlib.sha256(json.dumps(stable).encode()).hexdigest()
        previous = self.records.get(candidate.key, {})
        count = previous.get('count', 0) if (previous.get('policy'), previous.get('signature')) == (self.policy, signature) else 0
        self.records[candidate.key] = dict(policy=self.policy, signature=signature,
                                           count=min(count + 1, 2),
                                           diagnostics=[json.loads(row) for row in stable])
        self.save()

    def save(self):
        encoded = json.dumps(self.records, sort_keys=True)
        if len(self.records) > 10000 or len(encoded.encode()) > LIMIT:
            raise OSError('Context deferral state exceeds bound')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix='.review-deferrals-')
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(encoded)
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
