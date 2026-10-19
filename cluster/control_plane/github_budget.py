"""Fail-closed, account-wide GitHub request budget in the existing durable KV.

Both machines use ClaimClient's server, account and credentials. The local JSON
file is an allowlisted dashboard snapshot only; it never authorizes a request.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

from cluster.coordination_buckets import bucket_name
from .coordination import ClaimClient

KEY = 'github.account.v1'
MINUTE_LIMIT = 40
RESERVES = {'core': 100, 'graphql': 100, 'search': 1}
SNAPSHOT = 'github-api-budget.json'


class GitHubDeferred(RuntimeError):
    """A warning/retry condition, never a review FAIL or permission to proceed."""


def _resource(value):
    value = str(value).lower()
    return value if value in RESERVES else 'core'


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _state(value):
    if (not isinstance(value, dict) or value.get('version') != 1
            or not isinstance(value.get('requests'), list)
            or len(value['requests']) > MINUTE_LIMIT
            or not all(_number(t) for t in value['requests'])
            or not isinstance(value.get('resources'), dict)):
        raise GitHubDeferred('GitHub shared API budget invalid; requests paused')
    for resource, quota in value['resources'].items():
        if resource not in RESERVES or not isinstance(quota, dict):
            raise GitHubDeferred('GitHub shared API budget invalid; requests paused')
        if any(key not in ('remaining', 'limit', 'reset', 'observed_at',
                           'blocked_until', 'limited_count') or not _number(number)
               for key, number in quota.items()):
            raise GitHubDeferred('GitHub shared API budget invalid; requests paused')
    return value


def _public(state, now, status='ok', reason=''):
    return {'status': status, 'updated_at': now, 'minute_limit': MINUTE_LIMIT,
            'requests_last_minute': sum(t > now - 60 for t in state['requests']),
            'resources': {resource: {key: value for key, value in quota.items()
                          if key != 'limited_count'}
                          for resource, quota in state['resources'].items()},
            'reason': reason}


def _directory(data_dir=None):
    return Path(data_dir or os.environ.get('STORYPOOL_DATA_DIR',
                str(Path.home() / '.local/share/storypool'))).expanduser()


def _persist(value):
    # Snapshot failures cannot grant extra requests or hide a coordination error.
    path = _directory() / SNAPSHOT
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=str(path.parent),
                                         prefix='.github-budget-', delete=False) as out:
            temporary = out.name
            json.dump(value, out)
        os.replace(temporary, path)
    except OSError:
        pass
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def snapshot(data_dir=None):
    """Read local, safe quota data without contacting GitHub or NATS."""
    empty = _public({'requests': [], 'resources': {}}, 0, 'unknown')
    try:
        value = json.loads((_directory(data_dir) / SNAPSHOT).read_text())
        state = _state({'version': 1, 'requests': [],
                        'resources': value['resources']})
        result = _public(state, value['updated_at'])
        if not _number(result['updated_at']):
            return empty
        result['status'] = value['status'] if value['status'] in (
            'ok', 'unknown', 'deferred', 'unavailable') else 'unknown'
        count = value.get('requests_last_minute', 0)
        result['requests_last_minute'] = count if type(count) is int and 0 <= count <= MINUTE_LIMIT else 0
        # Do not return arbitrary strings from a disk file on the dashboard.
        return result
    except (OSError, ValueError, KeyError, TypeError, GitHubDeferred):
        return empty


def _integer(headers, name):
    try:
        number = int(headers[name])
        return number if number >= 0 else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _delay(headers, now):
    raw = headers.get('retry-after', '')
    try:
        seconds = float(raw)
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return max(0, parsedate_to_datetime(raw).timestamp() - now)
    except (TypeError, ValueError, OverflowError, AttributeError):
        return 0


def _mutate(state, operation, resource, headers, limited, now):
    state['requests'] = [t for t in state['requests'] if t > now - 60]
    quota = state['resources'].setdefault(resource, {})
    if operation == 'reserve':
        if quota.get('blocked_until', 0) > now:
            raise GitHubDeferred('GitHub API rate-limit backoff active; retry later')
        reset = quota.get('reset', 0)
        if 'remaining' in quota and quota['remaining'] <= RESERVES[resource]:
            if not reset or reset > now:
                raise GitHubDeferred('GitHub API quota reserve reached; retry after reset')
        if len(state['requests']) >= MINUTE_LIMIT:
            raise GitHubDeferred('GitHub shared API minute budget exhausted; retry later')
        if reset and reset <= now:
            quota.pop('remaining', None)
        if 'remaining' in quota:
            quota['remaining'] = max(0, quota['remaining'] - 1)
        state['requests'].append(now)
        return
    reset = _integer(headers, 'x-ratelimit-reset')
    remaining = _integer(headers, 'x-ratelimit-remaining')
    limit = _integer(headers, 'x-ratelimit-limit')
    previous_reset = quota.get('reset', 0)
    # Late responses from the previous period cannot replace a newer quota.
    current_period = reset is None or reset >= previous_reset
    if current_period:
        if remaining is not None:
            quota['remaining'] = (remaining if reset is not None and reset > previous_reset
                                  else min(remaining, quota.get('remaining', remaining)))
        if reset is not None:
            quota['reset'] = reset
        if limit is not None:
            quota['limit'] = limit
        quota['observed_at'] = now
    if limited:
        failures = min(int(quota.get('limited_count', 0)) + 1, 8)
        quota['limited_count'] = failures
        # Reset applies to primary limits; Retry-After also covers secondary limits.
        wait = max(60 * 2 ** (failures - 1), _delay(headers, now),
                   max(0, reset - now) if reset is not None else 0)
        quota['blocked_until'] = max(quota.get('blocked_until', 0), now + wait)
    elif quota.get('blocked_until', 0) <= now:
        quota['limited_count'] = 0


async def _operation(operation, resource, headers, limited):
    nc = None
    try:
        # Lazy import: dashboard snapshots work when NATS is unavailable/uninstalled.
        import nats
        from nats.js.errors import KeyNotFoundError, KeyDeletedError, KeyWrongLastSequenceError
        client = ClaimClient.from_env()
        async def quiet_error(_):
            pass
        nc = await asyncio.wait_for(nats.connect(client.url, connect_timeout=2,
            max_reconnect_attempts=1, allow_reconnect=False, error_cb=quiet_error,
            **client.connection), timeout=3)
        kv = await nc.jetstream(timeout=2).key_value(bucket_name('retries', client.bucket))
        if (await kv.status()).ttl:
            raise GitHubDeferred('GitHub shared API budget must be durable; requests paused')
        for _ in range(64):
            now = time.time()
            try:
                entry = await kv.get(KEY)
                state, revision = _state(json.loads(entry.value)), entry.revision
            except (KeyNotFoundError, KeyDeletedError):
                state, revision = {'version': 1, 'requests': [], 'resources': {}}, 0
            try:
                _mutate(state, operation, resource, headers, limited, now)
            except GitHubDeferred as exc:
                _persist(_public(state, now, 'deferred', str(exc)))
                raise
            try:
                value = json.dumps(state).encode()
                if revision:
                    await kv.update(KEY, value, last=revision)
                else:
                    await kv.create(KEY, value)
                result = _public(state, now)
                _persist(result)
                return result
            except KeyWrongLastSequenceError:
                continue
        raise GitHubDeferred('GitHub shared API budget busy; retry later')
    except GitHubDeferred:
        raise
    except Exception:
        previous = snapshot()
        previous.update(status='unavailable', updated_at=time.time())
        _persist(previous)
        # Never expose connection URLs, credential values or arbitrary server errors.
        raise GitHubDeferred('GitHub shared API budget unavailable; requests paused') from None
    finally:
        if nc is not None:
            try:
                await nc.close()
            except Exception:
                pass


def reserve(resource='core'):
    """Atomically admit one account request, or raise GitHubDeferred."""
    return asyncio.run(_operation('reserve', _resource(resource), {}, False))


def observe(headers=None, resource='core', limited=False):
    """Record safe rate headers/backoff after a response, sharing across hosts."""
    safe = {str(key).lower(): value for key, value in (headers or {}).items()
            if str(key).lower() in ('x-ratelimit-resource', 'x-ratelimit-remaining',
                'x-ratelimit-reset', 'x-ratelimit-limit', 'retry-after')}
    resource = _resource(safe.get('x-ratelimit-resource', resource))
    return asyncio.run(_operation('observe', resource, safe, bool(limited)))
