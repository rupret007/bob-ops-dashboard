"""Bounded readonly quota admission for the selected SSH execution target."""
from __future__ import annotations

import json
import os
import re
import secrets
import selectors
import subprocess
import time

from .codex_quota import (APP_SOURCE, SOURCE, MAX_AGE_SECONDS, _empty, _number,
                          _observation, _stamp, _unique_object)
from .codex_quota_observer import ObservationError, stop_owned_process

MAX_REPLY_BYTES = 8192


def valid_host(host):
    return isinstance(host, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@:-]{0,252}', host) is not None


def validate_reply(value, nonce, elapsed):
    """Anchor freshness to the source clock plus the whole transport interval."""
    if (not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 1
            or value.get('limit_id') != 'codex' or value.get('nonce') != nonce
            or not _number(elapsed) or not 0 <= elapsed <= 10):
        return _empty('malformed')
    served, quota = value.get('served_at'), value.get('quota')
    if not _number(served) or not isinstance(quota, dict):
        return _empty('malformed')
    if quota.get('status') != 'ok':
        return _empty()
    try:
        observed = _stamp(quota.get('observed_at'))
        window = quota.get('window')
        if window not in ('primary', 'secondary') or quota.get('source') not in (SOURCE, APP_SOURCE):
            return _empty('malformed')
        limits = dict(limit_id='codex', plan_type=quota.get('plan_type'))
        limits[window] = dict(used_percent=quota.get('weekly_pct'),
                              window_minutes=quota.get('window_minutes'), resets_at=quota.get('resets_at'))
        result = _observation(observed, limits, served + elapsed, MAX_AGE_SECONDS)
        if observed > served:
            return _empty('malformed')
        return dict(result, source=quota['source'])
    except (ValueError, TypeError, OverflowError):
        return _empty('malformed')


def read_remote_codex_quota(host, *, timeout=10, executable='ssh'):
    """Read the target's installed file-only helper. Never fetch or infer here."""
    if not valid_host(host) or not _number(timeout) or not 0 < timeout <= 10:
        return _empty('malformed')
    nonce = secrets.token_hex(16)
    remote = 'exec "$HOME/.local/bin/storypool-codex-quota-read" --nonce ' + nonce
    started = time.monotonic()
    deadline = started + timeout
    process = None
    selector = selectors.DefaultSelector()
    try:
        process = subprocess.Popen([executable, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                                    host, remote], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        selector.register(process.stdout, selectors.EVENT_READ)
        data = b''
        closed = False
        while time.monotonic() < deadline and not closed:
            for key, _ in selector.select(min(.25, max(0, deadline-time.monotonic()))):
                block = os.read(key.fd, MAX_REPLY_BYTES+1)
                data += block
                if len(data) > MAX_REPLY_BYTES:
                    return _empty('malformed')
                if not block:
                    closed = True
            if process.poll() is not None and not data:
                return _empty()
        if not closed:
            return _empty()
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            return _empty()
        code = process.wait(timeout=remaining)
        if code:
            return _empty()
        packet = json.loads(data, object_pairs_hook=_unique_object)
    except (OSError, ValueError, TypeError, OverflowError, subprocess.TimeoutExpired):
        return _empty()
    finally:
        selector.close()
        if process is not None:
            try:
                stop_owned_process(process)
            except (OSError, ObservationError):
                return _empty('malformed')
    return validate_reply(packet, nonce, time.monotonic()-started)
