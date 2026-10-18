"""Durable shared owner-task inbox; only the guarded runtime may admit work.

Staging is deliberately distinct from executable admission. This module starts
no processes and writes no local queue. The consumer fences its transition with
shared task and branch leases and delegates all execution to the single runtime.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time

from .coordination import ClaimClient, CoordinationError, Lease

OWNER_TASK_CONTRACT = 1
POLICY = 'draft-only-exact-head-karen-v1'
CHECKS = frozenset(('shared_coordination', 'global_unpaused', 'codex_unpaused',
    'node_available', 'machine_guards', 'lane_capacity', 'branch_claim',
    'fresh_shared_quota', 'draft_only', 'exact_head_karen_required', 'durable_idempotency'))
FIELDS = ('contract', 'request_id', 'repo', 'head_sha', 'branch', 'provider', 'policy')
FORBIDDEN = re.compile(r'\b(?:rad-dad-show-night|grok|merge|undraft|force.push|go.live|send|publish|release|disable.{0,20}(?:guard|test))\b', re.I)


def validate_request(request):
    if (not isinstance(request, dict) or type(request.get('contract')) is not int
            or request['contract'] != OWNER_TASK_CONTRACT
            or request.get('repo') != 'rupret007/bob-brain'
            or request.get('provider') != 'codex' or request.get('policy') != POLICY
            or not isinstance(request.get('request_id'), str)
            or not re.fullmatch(r'webbob-[a-f0-9]{64}', request['request_id'])
            or not isinstance(request.get('head_sha'), str)
            or not re.fullmatch(r'[a-f0-9]{40}', request['head_sha'])
            or request.get('branch') != 'draft/webbob-task-' + request['request_id'][7:31]
            or not isinstance(request.get('description'), str)
            or not 1 <= len(request['description'].strip()) <= 2000
            or '\x00' in request['description'] or FORBIDDEN.search(request['description'])):
        raise CoordinationError('Invalid bounded draft-only owner task')


def validate_admission(receipt, request, now):
    if not isinstance(receipt, dict):
        raise CoordinationError('Runtime did not return a durable receipt')
    quota, observed = receipt.get('codex_weekly_pct'), receipt.get('admitted_at')
    if (type(receipt.get('contract')) is not int
            or not isinstance(receipt.get('receipt_id'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', receipt['receipt_id'])
            or any(receipt.get(k) != request[k] for k in FIELDS)
            or receipt.get('state') != 'queued'
            or set(receipt.get('checks', {})) != CHECKS
            or any(v is not True for v in receipt['checks'].values())
            or type(quota) not in (int, float) or not math.isfinite(quota) or not 0 <= quota < 75
            or type(observed) not in (int, float) or not math.isfinite(observed)
            or not 0 <= now - observed <= 30):
        raise CoordinationError('Runtime did not confirm guarded admission')


class SharedInbox:
    """No-TTL KV rows survive lease expiry, restarts and either host's loss."""
    def __init__(self, client):
        self.client = client

    def call(self, operation, key=None, value=None, revision=None):
        return asyncio.run(self._call(operation, key, value, revision))

    async def _call(self, operation, key, value, revision):
        import nats
        from nats.js.errors import KeyNotFoundError, KeyDeletedError, NoKeysError
        nc = None
        async def quiet_error(_):
            pass
        try:
            nc = await asyncio.wait_for(nats.connect(self.client.url, connect_timeout=2,
                allow_reconnect=False, error_cb=quiet_error, **self.client.connection), timeout=3)
            js = nc.jetstream(timeout=2)
            bucket = self.client.bucket + '_OWNER_TASKS'
            if operation == 'bootstrap':
                from .coordination import _provision_bucket
                await _provision_bucket(js, bucket, 0)
                return None
            kv = await js.key_value(bucket)
            if (await kv.status()).ttl:
                raise CoordinationError('Owner-task inbox must be durable')
            if operation == 'create':
                return await kv.create(key, json.dumps(value, sort_keys=True).encode())
            if operation == 'update':
                return await kv.update(key, json.dumps(value, sort_keys=True).encode(), last=revision)
            if operation == 'keys':
                try:
                    return await kv.keys()
                except NoKeysError:
                    return []
            if operation == 'get':
                try:
                    entry = await kv.get(key)
                except (KeyNotFoundError, KeyDeletedError):
                    return None
                return json.loads(entry.value), entry.revision
            raise CoordinationError('Unknown owner-task inbox operation')
        except CoordinationError:
            raise
        except Exception:
            raise CoordinationError('Durable owner-task inbox unavailable; no work admitted') from None
        finally:
            if nc is not None:
                await nc.close()


def stage_owner_draft_task(request, *, inbox=None):
    """Atomically record owner-approved intent without claiming guard admission."""
    validate_request(request)
    inbox = inbox or SharedInbox(ClaimClient.from_env())
    key = request['request_id']
    digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    row = {'request': dict(request), 'digest': digest, 'state': 'awaiting_admission',
           'recorded_at': time.time()}
    try:
        inbox.call('create', key, row)
    except CoordinationError:
        # A create timeout is ambiguous. Resolve only from the durable authority.
        existing = inbox.call('get', key)
        if existing is None or existing[0].get('digest') != digest:
            raise CoordinationError('Owner-task admission could not be confirmed') from None
        row = existing[0]
    if row['state'] == 'queued':
        return row['receipt']
    return dict({k: request[k] for k in FIELDS}, state='awaiting_admission',
                receipt_id=key, recorded_at=row['recorded_at'])


def consume_pending(runtime, *, inbox, client, machine):
    """Drain intents into the *existing* runtime, never a worker launcher.

    runtime must transactionally deduplicate request_id and enqueue work only
    after quota/pause/drain/guard/lane checks, repeating checks at execution.
    An ambiguous runtime result remains pending and is safe only because that
    runtime is required to provide durable cluster-wide idempotency.
    """
    admitted = 0
    for key in inbox.call('keys'):
        entry = inbox.call('get', key)
        if entry is None or entry[0].get('state') != 'awaiting_admission':
            continue
        row, revision = entry
        request = row['request']
        validate_request(request)
        claim = client.acquire_task(request['repo'], 'owner-task:' + key,
                                    request['head_sha'], machine)
        with Lease(client, claim):
            branch = client.acquire_task(request['repo'], 'branch:' + request['branch'],
                                        request['head_sha'], machine)
            with Lease(client, branch) as branch_lease:
                # Reread inside the lease; another consumer may have finished.
                row, revision = inbox.call('get', key)
                if row['state'] != 'awaiting_admission':
                    continue
                receipt = runtime(dict(request), branch_lease=branch_lease)
                validate_admission(receipt, request, time.time())
                branch_lease.renew()
                row.update(state='queued', receipt=receipt)
                inbox.call('update', key, row, revision)
                admitted += 1
    return admitted


def admit_owner_draft_task(request):
    # Web Bob receives a truthful pending receipt, never fabricated guards.
    return stage_owner_draft_task(request)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Operator-only shared owner-task inbox provisioning')
    parser.add_argument('--bootstrap', action='store_true', required=True)
    parser.parse_args()
    SharedInbox(ClaimClient.from_env()).call('bootstrap')
    print('Durable shared owner-task inbox provisioned; no task or Codex process started')
