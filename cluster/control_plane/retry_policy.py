"""Bounded exact-head retries. Messages are controlled constants, never exception text."""
from __future__ import annotations

import time

MAX_ATTEMPTS = 3
REASONS = {
    'missing-lockfile': 'blocked: needs package-lock.json for reproducible tests',
    'protected-pr': 'blocked: closed, fork, or protected-branch PR needs Jeff',
    'head-changed': 'blocked: PR head changed; review the new full SHA',
    'unsupported-tests': 'blocked: No supported test command; needs Jeff',
    'retry-limit': 'blocked: retry limit reached for this full SHA; needs Jeff',
    'already-fixed': 'blocked: this full SHA already produced a verified fix',
}


class PreflightFailure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(REASONS[code])


def reserve(previous=None, historical_attempts=0, now=None):
    now = time.time() if now is None else now
    state = dict(previous or {})
    attempts = max(int(state.get('attempts', 0)), historical_attempts)
    if state.get('code') in REASONS:
        return None, REASONS[state['code']]
    if attempts >= MAX_ATTEMPTS:
        return None, REASONS['retry-limit']
    if state.get('retry_after', 0) > now:
        return None, 'retry backoff: next attempt in {} seconds'.format(int(state['retry_after'] - now))
    attempts += 1
    return dict(attempts=attempts, retry_after=now + min(3600, 300 * 2 ** (attempts - 1)), code='transient'), ''
