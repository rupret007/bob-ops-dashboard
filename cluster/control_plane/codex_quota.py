"""Read only Codex plan quota events; session text and paths never leave here."""
from __future__ import annotations

import json
import math
import os
import re
import stat
import time
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path

MAX_FILES = 16
MAX_TAIL_BYTES = 262144
MAX_DIRECTORY_ENTRIES = 2048
MAX_AGE_SECONDS = 3600
WEEK_MINUTES = 10080
SOURCE = 'codex_session_rate_limits'
APP_SOURCE = 'codex_app_server_rate_limits'
CACHE_MAX_BYTES = 32768


def quota_cache_path():
    return Path.home() / '.local/share/storypool/quota/codex.json'


def _empty(status='unknown'):
    return dict(status=status, weekly_pct=None, observed_at=None, resets_at=None,
                window_minutes=None, window=None, plan_type=None, source=SOURCE)


def _entries(path):
    with os.scandir(path) as entries:
        result = list(islice(entries, MAX_DIRECTORY_ENTRIES + 1))
    if len(result) > MAX_DIRECTORY_ENTRIES:
        raise OSError('Session directory exceeds scan bound')
    return result


def _directories(path, digits, count):
    children = [Path(entry.path) for entry in _entries(path)
                if re.fullmatch(r'[0-9]{' + str(digits) + '}', entry.name)
                and entry.is_dir(follow_symlinks=False)]
    return sorted(children, reverse=True)[:count]


def _session_files(root):
    days = []
    for year in _directories(root, 4, 2):
        for month in _directories(year, 2, 2):
            days.extend(_directories(month, 2, 4))
    files = []
    for day in sorted(days, reverse=True)[:4]:
        for entry in _entries(day):
            if entry.name.endswith('.jsonl') and entry.is_file(follow_symlinks=False):
                files.append((entry.stat(follow_symlinks=False).st_mtime_ns,
                              Path(entry.path)))
    return [path for _, path in sorted(files, reverse=True)[:MAX_FILES]]


def _tail_records(path):
    with path.open('rb') as source:
        source.seek(0, 2)
        offset = max(0, source.tell() - MAX_TAIL_BYTES)
        source.seek(offset)
        tail = source.read(MAX_TAIL_BYTES)
    # The first line may start mid-record; the last may still be appending.
    if offset:
        tail = tail.partition(b'\n')[2]
    if not tail.endswith(b'\n'):
        tail = tail.rpartition(b'\n')[0]
    for line in tail.splitlines():
        if line.strip():
            yield json.loads(line, object_pairs_hook=_unique_object)


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _unique_object(pairs):
    value = {}
    for key, entry in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key')
        value[key] = entry
    return value


def _observation(stamp, limits, now, max_age):
    result = _empty('malformed')
    result['observed_at'] = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    windows = [(name, limits.get(name)) for name in ('primary', 'secondary')]
    weekly = [(name, window) for name, window in windows if isinstance(window, dict)
              and type(window.get('window_minutes')) is int
              and window['window_minutes'] == WEEK_MINUTES]
    if not weekly or stamp > now:
        return result
    values = []
    for name, window in weekly:
        pct, reset = window.get('used_percent'), window.get('resets_at')
        if (not _number(pct) or not 0 <= pct <= 100 or type(reset) is not int
                or reset <= stamp or reset > stamp + WEEK_MINUTES * 60 + 60):
            return result
        values.append((float(pct), reset, name))
    # Conflicting weekly windows cannot authorize a call based on the lower one.
    if len({(pct, reset) for pct, reset, _ in values}) > 1:
        return result
    pct, reset, name = values[0]
    plan = limits.get('plan_type')
    result.update(resets_at=reset, window_minutes=WEEK_MINUTES, window=name,
                  plan_type=plan if plan in ('free', 'plus', 'pro', 'team',
                                            'business', 'enterprise', 'edu') else None)
    if now - stamp > max_age or reset <= now:
        result['status'] = 'stale'
    else:
        result.update(status='ok', weekly_pct=pct)
    return result


def _read_session_quota(session_root=None, *, now=None, max_age_seconds=MAX_AGE_SECONDS):
    """Select the latest actual observation from bounded recent session tails.

    File names/mtimes bound discovery only. Older valid data never replaces a
    newer malformed Codex observation. Missing, expired or stale data is unknown
    for admission; no dollar estimates or session content are returned.
    """
    now = time.time() if now is None else now
    if not _number(now) or not _number(max_age_seconds) or max_age_seconds <= 0:
        return _empty('malformed')
    root = Path(session_root) if session_root is not None else Path.home() / '.codex/sessions'
    latest = None
    try:
        for path in _session_files(root):
            for row in _tail_records(path):
                if not isinstance(row, dict) or row.get('type') != 'event_msg':
                    continue
                payload = row.get('payload')
                if not isinstance(payload, dict) or payload.get('type') != 'token_count':
                    continue
                limits = payload.get('rate_limits')
                if not isinstance(limits, dict) or limits.get('limit_id') != 'codex':
                    continue
                raw_stamp = row.get('timestamp')
                if not isinstance(raw_stamp, str) or len(raw_stamp) > 40:
                    return _empty('malformed')
                stamp = datetime.fromisoformat(raw_stamp.replace('Z', '+00:00'))
                if stamp.tzinfo is None:
                    return _empty('malformed')
                observed = stamp.timestamp()
                value = _observation(observed, limits, now, max_age_seconds)
                if latest is None or observed > latest[0]:
                    latest = (observed, value)
                elif observed == latest[0]:
                    if value['status'] != 'ok' or latest[1]['status'] != 'ok':
                        latest = (observed, _empty('malformed'))
                    elif (value['weekly_pct'], value['resets_at']) != (latest[1]['weekly_pct'], latest[1]['resets_at']):
                        latest = (observed, dict(_empty('malformed'), observed_at=raw_stamp))
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError, TypeError, OverflowError):
        return _empty('malformed')
    return latest[1] if latest is not None else _empty()


def _stamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('Invalid observation timestamp')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Observation timestamp must include timezone')
    return stamp.timestamp()


def normalize_app_limits(body):
    """Accept only the exact Codex bucket, discarding all account/credit data."""
    if not isinstance(body, dict):
        raise ValueError('Malformed rate limits')
    mapping = body.get('rateLimitsByLimitId')
    if mapping is not None:
        if not isinstance(mapping, dict) or not isinstance(mapping.get('codex'), dict):
            raise ValueError('Missing Codex bucket')
        selected = mapping['codex']
    else:
        selected = body.get('rateLimits')
    if not isinstance(selected, dict) or selected.get('limitId') != 'codex':
        raise ValueError('Wrong Codex bucket')

    def normalize(bucket):
        value = dict(limit_id='codex', plan_type=bucket.get('planType'))
        for name in ('primary', 'secondary'):
            window = bucket.get(name)
            if window is not None and not isinstance(window, dict):
                raise ValueError('Malformed quota window')
            if window is not None and (not _number(window.get('usedPercent'))
                    or not 0 <= window['usedPercent'] <= 100
                    or type(window.get('windowDurationMins')) is not int
                    or not 0 < window['windowDurationMins'] <= 525600
                    or type(window.get('resetsAt')) is not int):
                raise ValueError('Malformed quota window')
            value[name] = None if window is None else dict(
                used_percent=window.get('usedPercent'),
                window_minutes=window.get('windowDurationMins'), resets_at=window.get('resetsAt'))
        # A server-classified reached limit cannot authorize another call.
        value['limit_reached'] = bucket.get('rateLimitReachedType') is not None
        return value

    normalized = normalize(selected)
    legacy = body.get('rateLimits')
    if mapping is not None and legacy is not None:
        if not isinstance(legacy, dict) or legacy.get('limitId') != 'codex' or normalize(legacy) != normalized:
            raise ValueError('Conflicting Codex observations')
    if 'ordinaryUsageAllowed' in body:
        allowed = body['ordinaryUsageAllowed']
        if allowed is not None and type(allowed) is not bool:
            raise ValueError('Malformed usage admission')
        if allowed is False:
            normalized['limit_reached'] = True
    # Unknown plan strings must not enter the cache or dashboard.
    if normalized['plan_type'] not in ('free', 'plus', 'pro', 'team', 'business', 'enterprise', 'edu'):
        normalized['plan_type'] = None
    return normalized


def _read_cache(path, now, max_age):
    empty = dict(_empty(), source=APP_SOURCE)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or info.st_mode & 0o077 or info.st_size > CACHE_MAX_BYTES):
                raise ValueError('Unsafe quota cache')
            raw = handle.read(CACHE_MAX_BYTES + 1)
            if len(raw) > CACHE_MAX_BYTES:
                raise ValueError('Oversized quota cache')
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(value, dict) or value.get('schema') != 1 or value.get('source') != APP_SOURCE:
            raise ValueError('Malformed quota cache')
        stamp = _stamp(value.get('observed_at'))
        result = dict(empty, status='malformed', observed_at=value['observed_at'])
        if value.get('status') == 'malformed':
            return result
        limits = value.get('limits')
        if (value.get('status') != 'ok' or not isinstance(limits, dict)
                or limits.get('limit_id') != 'codex' or type(limits.get('limit_reached')) is not bool):
            return result
        result = dict(_observation(stamp, limits, now, max_age), source=APP_SOURCE)
        if limits['limit_reached'] and result['status'] == 'ok':
            result.update(status='blocked', weekly_pct=None)
        return result
    except FileNotFoundError:
        return empty
    except (OSError, ValueError, TypeError, OverflowError):
        return dict(empty, status='malformed')


def read_codex_quota(session_root=None, *, cache_path=None, now=None, max_age_seconds=MAX_AGE_SECONDS):
    """Pure bounded reads of the freshest authoritative local observation.

    No CLI, network or inference is started here. Explicit fixture session roots
    do not read the operator cache unless a cache_path is also supplied.
    """
    now = time.time() if now is None else now
    if not _number(now) or not _number(max_age_seconds) or max_age_seconds <= 0:
        return _empty('malformed')
    session = _read_session_quota(session_root, now=now, max_age_seconds=max_age_seconds)
    if (cache_path is None and session_root is not None
            and Path(session_root).expanduser() != Path.home() / '.codex/sessions'):
        return session
    cached = _read_cache(Path(cache_path) if cache_path is not None else quota_cache_path(), now, max_age_seconds)
    observations = []
    for row in (session, cached):
        if row['observed_at'] is None:
            if row['status'] == 'malformed':
                return row
            continue
        try:
            stamp = _stamp(row['observed_at'])
        except (ValueError, TypeError, OverflowError):
            return _empty('malformed')
        if stamp > now:
            return dict(_empty('malformed'), source=row['source'])
        observations.append((stamp, row))
    if not observations:
        return session
    latest = max(stamp for stamp, row in observations)
    current = [row for stamp, row in observations if stamp == latest]
    if len(current) == 2 and any(current[0][key] != current[1][key]
                                 for key in ('status', 'weekly_pct', 'resets_at')):
        return dict(_empty('malformed'), observed_at=current[0]['observed_at'])
    return current[0]


def main():
    """Fixed readonly SSH boundary: local-clock age travels with the quota."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    served = time.time()
    print(json.dumps(dict(schema=1, served_at=served, quota=read_codex_quota(now=served)),
                     sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
