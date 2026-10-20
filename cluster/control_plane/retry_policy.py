"""Bounded exact-head retries. Messages are controlled constants, never exception text."""
from __future__ import annotations

import time
import subprocess

MAX_ATTEMPTS = 3
REASONS = {
    'missing-lockfile': 'blocked: needs package-lock.json for reproducible tests',
    'protected-pr': 'blocked: closed, fork, or protected-branch PR needs Jeff',
    'head-changed': 'blocked: PR head changed; review the new full SHA',
    'unsupported-tests': 'blocked: No supported test command; needs Jeff',
    'retry-limit': 'blocked: retry limit reached for this full SHA; needs Jeff',
    'already-fixed': 'blocked: this full SHA already produced a verified fix',
}

# Measurement categories are separate from permanent preflight blocks. These
# outcomes keep the existing attempt count/backoff; a category is not proof that
# an automatic retry will work, nor permission to reset its exact-head budget.
FAILURE_REASONS = {
    'provider-failed': 'Provider command failed; no verified fix was recorded',
    'tests-failed': 'Required tests did not pass; no fix was published',
    'test-setup-failed': 'Test setup or dependency installation failed; no fix was published',
    'no-change': 'No code change was produced; no fix was published',
    'git-failed': 'Repository preparation or commit failed; no verified fix was recorded',
    'push-failed': 'Push failed or its result is uncertain; verify the remote head before retrying',
    'verification-failed': 'Fix outcome could not be verified; the job remains failed',
    'deadline': 'Worker command or job deadline was reached; no verified fix was recorded',
    'coordination-failed': 'Worker ownership or retry coordination was unavailable',
    'ownership-lost': 'Worker ownership was lost; the command was stopped',
    'findings-unavailable': 'Trusted exact-head Karen findings were unavailable',
    'setup-failed': 'Worker setup failed; no verified fix was recorded',
    'worker-failed': 'Supervisor failed outside a classified operation; no verified fix was recorded',
}
STAGE_CODES = {
    'provider': 'provider-failed', 'tests': 'tests-failed', 'test-setup': 'test-setup-failed', 'git': 'git-failed',
    'push': 'push-failed', 'verification': 'verification-failed',
    'coordination': 'coordination-failed', 'findings': 'findings-unavailable',
    'setup': 'setup-failed', 'supervisor': 'worker-failed',
}


class WorkerFailure(RuntimeError):
    """A supervisor-owned category, never copied from model/CLI error text."""
    def __init__(self, code, stage):
        if (not isinstance(code, str) or not isinstance(stage, str)
                or code not in FAILURE_REASONS or stage not in STAGE_CODES):
            raise ValueError('Unknown controlled worker failure')
        self.code, self.stage = code, stage
        super().__init__(FAILURE_REASONS[code])


def classify_failure(exc, stage):
    """Use trusted operation boundaries and exception types, never strings/argv."""
    stage = stage if isinstance(stage, str) and stage in STAGE_CODES else 'supervisor'
    if isinstance(exc, PreflightFailure):
        return exc.code, 'setup'
    if isinstance(exc, WorkerFailure):
        return exc.code, exc.stage
    if isinstance(exc, subprocess.TimeoutExpired):
        return 'deadline', stage
    from .coordination import ClaimLost, CoordinationError
    if isinstance(exc, ClaimLost):
        return 'ownership-lost', stage
    if isinstance(exc, CoordinationError):
        return 'coordination-failed', stage
    return STAGE_CODES[stage], stage


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
