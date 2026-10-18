"""Cluster claims: one NATS account/bucket, create-only admission, CAS renewal.

No local fallback. PR keys deliberately exclude SHA: two heads of one PR still
write the same branch. The full pinned SHA is carried in the claim value.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path


class CoordinationError(RuntimeError):
    pass


class ClaimBusy(CoordinationError):
    pass


class ClaimLost(CoordinationError):
    pass


class ClaimClient:
    def __init__(self, url, bucket='STORYPOOL_CLAIMS', ttl=300, **connection):
        self.url, self.bucket, self.ttl = url, bucket, ttl
        self.connection = connection

    @classmethod
    def from_env(cls):
        # Both hosts MUST point to the same server/account; never infer a
        # machine-local broker on Mini or silently create another authority.
        try:
            path = Path(os.environ.get('STORYPOOL_COORD_CONFIG',
                str(Path.home() / '.config/storypool/coordination.json'))).expanduser()
            config = json.loads(path.read_text())
        except FileNotFoundError:
            config = {}
        except (OSError, ValueError):
            raise CoordinationError('Invalid coordination configuration; new work paused') from None
        if not isinstance(config, dict):
            raise CoordinationError('Invalid coordination configuration; new work paused')
        url = os.environ.get('STORYPOOL_COORD_URL', config.get('url', ''))
        if not url:
            raise CoordinationError('Cluster coordination is not configured; new work paused')
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        if parsed.scheme not in ('nats', 'tls') or not parsed.hostname or parsed.username or parsed.password:
            raise CoordinationError('Invalid coordination URL; credentials must use a token file')
        user = os.environ.get('STORYPOOL_COORD_USER', config.get('user', ''))
        options = {}
        if user:
            from cluster.submit import load_token
            try:
                token = load_token(user, os.path.expanduser(os.environ.get(
                    'STORYPOOL_COORD_TOKEN_FILE', config.get('token_file', '~/.config/bob-cluster/tokens.env'))))
            except SystemExit:
                raise CoordinationError('Coordination credentials unavailable') from None
            options = {'user': user, 'password': token,
                       'inbox_prefix': ('_INBOX_' + user).encode()}
        return cls(url, **options)

    async def _operation(self, operation, claim=None, bootstrap=False):
        import nats
        from nats.js.api import KeyValueConfig
        from nats.js.errors import KeyWrongLastSequenceError, APIError

        async def quiet_error(_):
            # NATS exceptions can include URLs/credentials. Only stable errors
            # below leave this module.
            pass

        nc = None
        try:
            nc = await asyncio.wait_for(nats.connect(self.url, connect_timeout=2,
                max_reconnect_attempts=1, allow_reconnect=False, error_cb=quiet_error,
                **self.connection), timeout=3)
            js = nc.jetstream(timeout=2)
            if bootstrap:
                await js.create_key_value(config=KeyValueConfig(
                    bucket=self.bucket, ttl=self.ttl, history=1))
            kv = await js.key_value(self.bucket)
            status = await kv.status()
            if abs(status.ttl - self.ttl) > 0.01:
                raise CoordinationError('Coordination bucket TTL mismatch; new work paused')
            if bootstrap:
                return None
            fields = {k: claim[k] for k in ('owner', 'repo', 'pr', 'sha', 'machine')}
            if 'task' in claim:
                fields['task'] = claim['task']
            value = json.dumps(fields).encode()
            if operation == 'acquire':
                revision = await kv.create(claim['key'], value)
            elif operation == 'renew':
                revision = await kv.update(claim['key'], value, last=claim['revision'])
            elif operation == 'release':
                await kv.delete(claim['key'], last=claim['revision'])
                return None
            else:
                raise ValueError('Unknown claim operation')
            return {**claim, 'revision': revision}
        except KeyWrongLastSequenceError:
            error = ClaimBusy if operation == 'acquire' else ClaimLost
            raise error('Work already claimed' if operation == 'acquire' else 'Cluster claim lost') from None
        except APIError as exc:
            if exc.err_code == 10071:
                raise ClaimLost('Cluster claim lost') from None
            raise CoordinationError('Coordination unavailable or permission denied; new work paused') from None
        except CoordinationError:
            raise
        except Exception:
            raise CoordinationError('Coordination unavailable or permission denied; new work paused') from None
        finally:
            if nc is not None:
                try:
                    await nc.close()
                except Exception:
                    pass

    def call(self, operation, claim=None, bootstrap=False):
        result = asyncio.run(self._operation(operation, claim, bootstrap))
        if bootstrap:
            asyncio.run(self._retry_operation('bootstrap', None))
        return result

    async def _retry_operation(self, operation, claim, state=None):
        """Durable retry state is separate from expiring work claims.

        Caller owns the PR lease; all mutations are serialized by that lease.
        Missing/unavailable storage fails closed, with no local fallback.
        """
        import nats
        from nats.js.api import KeyValueConfig
        from nats.js.errors import KeyNotFoundError, KeyDeletedError
        from .retry_policy import REASONS
        nc = None
        async def quiet_error(_):
            pass
        try:
            nc = await asyncio.wait_for(nats.connect(self.url, connect_timeout=2,
                max_reconnect_attempts=1, allow_reconnect=False, error_cb=quiet_error,
                **self.connection), timeout=3)
            js = nc.jetstream(timeout=2)
            bucket = self.bucket + '_RETRIES'
            if operation == 'bootstrap':
                await js.create_key_value(config=KeyValueConfig(bucket=bucket, ttl=0, history=1))
                return None
            kv = await js.key_value(bucket)
            if (await kv.status()).ttl:
                raise CoordinationError('Retry bucket must be durable; new fixes paused')
            key = 'fix.' + hashlib.sha256(
                '{}#{}@{}'.format(claim['repo'].lower(), claim['pr'], claim['sha']).encode()).hexdigest()
            if operation == 'get':
                try:
                    entry = await kv.get(key)
                    value = json.loads(entry.value)
                    claim['_retry_revision'] = entry.revision
                except (KeyNotFoundError, KeyDeletedError):
                    claim['_retry_revision'] = 0
                    return None
                if (not isinstance(value, dict) or type(value.get('attempts')) is not int
                        or value['attempts'] < 1 or type(value.get('retry_after')) not in (int, float)
                        or value.get('code') not in ('transient', *REASONS)):
                    raise CoordinationError('Invalid retry state; new fixes paused')
                return value
            if operation == 'put':
                if '_retry_revision' not in claim:
                    raise CoordinationError('Retry state must be read before write; new fixes paused')
                if claim['_retry_revision']:
                    revision = await kv.update(key, json.dumps(state).encode(), last=claim['_retry_revision'])
                else:
                    revision = await kv.create(key, json.dumps(state).encode())
                claim['_retry_revision'] = revision
                return None
            raise CoordinationError('Unknown retry operation')
        except CoordinationError:
            raise
        except Exception:
            raise CoordinationError('Durable retry coordination unavailable; new fixes paused') from None
        finally:
            if nc is not None:
                try:
                    await nc.close()
                except Exception:
                    pass

    def retry_state(self, claim, state=None):
        # Fence both reads and writes with a successful CAS renewal. A stale
        # worker cannot change the next owner's retry decision.
        renewed = self.call('renew', claim)
        claim.update(renewed)
        return asyncio.run(self._retry_operation('get' if state is None else 'put', claim, state))

    def acquire(self, repo, pr, sha, machine):
        from cluster.never_touch import is_never_touch
        if is_never_touch(repo) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise CoordinationError('Repository is not eligible for work')
        if not isinstance(pr, int) or pr <= 0 or not re.fullmatch(r'[0-9a-f]{40}', sha):
            raise CoordinationError('Claim requires a PR number and full head SHA')
        key = 'pr.' + hashlib.sha256(f'{repo.lower()}#{pr}'.encode()).hexdigest()
        return self.call('acquire', {'key': key, 'owner': uuid.uuid4().hex,
            'repo': repo, 'pr': pr, 'sha': sha, 'machine': machine})

    def acquire_task(self, repo, task, sha, machine):
        """Claim non-PR work by stable task identity, independent of head/node."""
        from cluster.never_touch import is_never_touch
        if is_never_touch(repo) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise CoordinationError('Repository is not eligible for work')
        if (not isinstance(task, str) or not re.fullmatch(r'[A-Za-z0-9_./:-]{1,200}', task)
                or not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha)):
            raise CoordinationError('Task claim requires a stable name and full head SHA')
        key = 'task.' + hashlib.sha256(f'{repo.lower()}#{task}'.encode()).hexdigest()
        return self.call('acquire', {'key': key, 'owner': uuid.uuid4().hex,
            'repo': repo, 'pr': None, 'task': task, 'sha': sha, 'machine': machine})


class Lease:
    """Renew in the background and fence each worker command on lease loss."""
    def __init__(self, client, claim):
        self.client, self.claim = client, claim
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.lost = False
        self.thread = None
        self.deadline = time.monotonic()

    def renew(self):
        with self.lock:
            if self.lost or (self.thread is not None and time.monotonic() >= self.deadline):
                self.lost = True
                raise ClaimLost('Cluster claim expired; worker stopped')
            try:
                self.claim = self.client.call('renew', self.claim)
                self.deadline = time.monotonic() + self.client.ttl / 2
            except CoordinationError:
                self.lost = True
                raise

    def _heartbeat(self):
        while not self.stop.wait(self.client.ttl / 6):
            try:
                self.renew()
            except CoordinationError:
                return

    def __enter__(self):
        if not self.claim:
            raise ClaimLost('Job has no cluster claim; redispatch required')
        self.renew()
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
        with self.lock:
            if not self.lost:
                try:
                    self.client.call('release', self.claim)
                except CoordinationError:
                    pass  # TTL recovers an unreachable broker; never delete a newer owner.


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Provision the shared claims bucket with an approved NATS identity')
    parser.add_argument('--bootstrap', action='store_true', required=True)
    parser.parse_args()
    try:
        client = ClaimClient.from_env()
        client.call('bootstrap', bootstrap=True)
        print('Shared claim bucket ready')
    except CoordinationError as exc:
        raise SystemExit(str(exc))
