"""Fetch plan limits through the readonly app-server RPC, never a model turn."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from datetime import datetime, timezone

from .codex_quota import (APP_SOURCE, CACHE_MAX_BYTES, MAX_AGE_SECONDS, _observation,
                          normalize_app_limits, quota_cache_path)

METHODS = ('initialize', 'initialized', 'account/rateLimits/read')
MAX_OUTPUT_BYTES = 262144
MAX_MESSAGES = 128
TIMEOUT_SECONDS = 20


class ObservationError(Exception):
    """Only fixed categories may cross the subprocess boundary."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ObservationError('malformed_protocol')
        result[key] = value
    return result


def fetch_limits(*, executable='codex', timeout=TIMEOUT_SECONDS):
    """One bounded three-message exchange; close the owned process afterward.

    The executable is injectable for offline protocol tests. Production selects
    the installed codex executable from the native unit's fixed PATH.
    """
    if type(timeout) not in (int, float) or not 0 < timeout <= TIMEOUT_SECONDS:
        raise ObservationError('invalid_timeout')
    deadline = time.monotonic() + timeout
    process = None
    selector = selectors.DefaultSelector()
    try:
        process = subprocess.Popen([executable, 'app-server', '--stdio', '-c', 'analytics.enabled=false'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        selector.register(process.stdout, selectors.EVENT_READ)
        buffer, total, count, initialized = b'', 0, 0, False

        def send(method, identifier=None, params=None):
            if method not in METHODS:
                raise ObservationError('method_denied')
            message = dict(method=method, params=params or {})
            if identifier is not None:
                message['id'] = identifier
            process.stdin.write((json.dumps(message) + '\n').encode())
            process.stdin.flush()

        send('initialize', 1, dict(clientInfo=dict(name='storypool_quota_observer',
             title='StoryPool quota observer', version='0.1.0')))
        while time.monotonic() < deadline:
            for key, _ in selector.select(min(.25, max(0, deadline - time.monotonic()))):
                block = os.read(key.fd, 65536)
                if not block:
                    raise ObservationError('server_closed')
                total += len(block)
                if total > MAX_OUTPUT_BYTES:
                    raise ObservationError('output_limit')
                buffer += block
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    count += 1
                    if count > MAX_MESSAGES:
                        raise ObservationError('message_limit')
                    try:
                        message = json.loads(line, object_pairs_hook=_object)
                    except (ValueError, UnicodeError):
                        raise ObservationError('malformed_protocol') from None
                    if not isinstance(message, dict):
                        raise ObservationError('malformed_protocol')
                    if 'method' in message:
                        if 'id' in message:
                            # Never answer token-refresh, tool or other server requests.
                            raise ObservationError('server_request_denied')
                        continue  # Discard notification names and all parameters.
                    identifier = message.get('id')
                    if type(identifier) is not int:
                        raise ObservationError('malformed_protocol')
                    if 'error' in message:
                        raise ObservationError('rpc_error')
                    if identifier == 1 and not initialized:
                        if not isinstance(message.get('result'), dict):
                            raise ObservationError('malformed_protocol')
                        initialized = True
                        send('initialized')
                        send('account/rateLimits/read', 2)
                    elif identifier == 2 and initialized:
                        if 'result' not in message:
                            raise ObservationError('malformed_protocol')
                        return message['result']
                    else:
                        raise ObservationError('unexpected_response')
            if process.poll() is not None:
                raise ObservationError('server_exited')
        raise ObservationError('timeout')
    except ObservationError:
        raise
    except (OSError, ValueError, TypeError, OverflowError):
        raise ObservationError('transport_or_protocol') from None
    finally:
        selector.close()
        if process is not None:
            stop_owned_process(process)


def stop_owned_process(process):
    """Close the process and all helpers in its separately owned group."""
    if process.stdin is not None:
        try:
            process.stdin.close()
        except OSError:
            pass
    try:
        process.wait(timeout=.5)
    except subprocess.TimeoutExpired:
        pass
    # The server can exit while a host/helper remains. The whole group
    # belongs to this one probe; never leave it behind on a fast exit.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=.5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        raise ObservationError('cleanup_timeout') from None
    if process.stdout is not None:
        process.stdout.close()


def _private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise ObservationError('unsafe_cache_directory')


def _save(path, value):
    raw = (json.dumps(value, separators=(',', ':'), allow_nan=False) + '\n').encode()
    if len(raw) > CACHE_MAX_BYTES:
        raise ObservationError('cache_size')
    descriptor, temporary = tempfile.mkstemp(prefix='.codex-', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def observe_once(cache_path=None, *, executable='codex', timeout=TIMEOUT_SECONDS):
    """Persist only a new server observation; failed transport never renews it."""
    path = Path(cache_path) if cache_path is not None else quota_cache_path()
    lock = None
    try:
        _private_directory(path.parent)
        descriptor = os.open(path.parent / 'observer.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        lock = os.fdopen(descriptor, 'a')
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ObservationError('unsafe_observer_lock')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(status='busy', source=APP_SOURCE)
        # Use request start, not completion time, as the freshness bound.
        observed = time.time()
        stamp = datetime.fromtimestamp(observed, timezone.utc).isoformat()
        packet = dict(schema=1, source=APP_SOURCE, observed_at=stamp, status='malformed')
        result = dict(status='malformed', source=APP_SOURCE, weekly_pct=None)
        try:
            body = fetch_limits(executable=executable, timeout=timeout)
        except ObservationError as exc:
            if str(exc) in ('malformed_protocol', 'unexpected_response', 'output_limit',
                            'message_limit', 'server_request_denied', 'rpc_error'):
                _save(path, packet)
                return result
            raise
        try:
            limits = normalize_app_limits(body)
            validated = _observation(observed, limits, time.time(), MAX_AGE_SECONDS)
            if validated['status'] != 'ok':
                raise ValueError('Malformed observation')
            packet.update(status='ok', limits=limits)
            result = dict(validated, source=APP_SOURCE)
            if limits['limit_reached']:
                result.update(status='blocked', weekly_pct=None)
        except (ValueError, TypeError, OverflowError):
            pass
        # Invalid authoritative replies replace older success with an unknown
        # observation. Raw account IDs, server messages and credits never persist.
        _save(path, packet)
        return result
    except (ObservationError, OSError, ValueError, TypeError, OverflowError) as exc:
        return dict(status='error', source=APP_SOURCE,
                    reason=str(exc) if isinstance(exc, ObservationError) else 'observer_io')
    finally:
        if lock is not None:
            lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    result = observe_once()
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result['status'] in ('ok', 'blocked', 'busy') else 1


if __name__ == '__main__':
    raise SystemExit(main())
