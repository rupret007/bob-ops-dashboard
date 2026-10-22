"""A bounded review cannot monopolize the poller or publish partial coverage."""
import time
from contextlib import contextmanager
from contextvars import ContextVar

REVIEW_SECONDS = 20 * 60
_deadline = ContextVar('karen_review_deadline', default=None)


class ReviewDeadlineExceeded(TimeoutError):
    pass


@contextmanager
def review_budget():
    token = _deadline.set(time.monotonic() + REVIEW_SECONDS)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining_timeout(provider_limit):
    deadline = _deadline.get()
    if deadline is None:
        return provider_limit
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ReviewDeadlineExceeded('Review exceeded 20-minute budget; incomplete review, retry required')
    return min(provider_limit, remaining)
