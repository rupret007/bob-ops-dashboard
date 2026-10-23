"""Default-off packet capacity for a hash-bound, expiring operator review.

This scope grants no review/merge admission and changes no source-fetch limit.
The trusted operator must verify owner authorization and all normal gates first.
No environment variable or service configuration activates it.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import math
import os
import re
import stat
import time

DEFAULT_LIMITS = (4, 48000)
OPERATOR_LIMITS = (8, 96000)
_scope = ContextVar("karen_operator_packet_scope", default=None)
_FIELDS = frozenset(("repo", "pr", "head", "issued_at", "expires_at",
                     "entries", "packet_bytes", "authorization_sha256"))


def _reject():
    raise ValueError("Invalid or inactive operator packet scope")


def _digest(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _identity(pr):
    if (any(type(getattr(pr, key, None)) is not str or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", getattr(pr, key)) is None
            for key in ("owner", "repo")) or
            type(pr.number) is not int or pr.number < 1 or
            type(pr.head_sha) is not str or re.fullmatch(r"[0-9a-f]{40}", pr.head_sha) is None):
        _reject()
    return (pr.owner + "/" + pr.repo, pr.number, pr.head_sha)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject()
        result[key] = value
    return result


def _read_intent(path, expected_digest, authorization_digest):
    if not _digest(expected_digest) or not _digest(authorization_digest):
        _reject()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_mode & 0o077 or info.st_size > 4096):
            _reject()
        data = stream.read(4097)
    if len(data) > 4096 or hashlib.sha256(data).hexdigest() != expected_digest:
        _reject()
    value = json.loads(data, object_pairs_hook=_unique)
    if (type(value) is not dict or set(value) != _FIELDS or
            value["authorization_sha256"] != authorization_digest or
            type(value["entries"]) is not int or type(value["packet_bytes"]) is not int or
            (value["entries"], value["packet_bytes"]) != OPERATOR_LIMITS or
            type(value["repo"]) is not str or type(value["pr"]) is not int or
            type(value["head"]) is not str or
            any(type(value[k]) not in (int, float) or not math.isfinite(value[k])
                for k in ("issued_at", "expires_at")) or
            not 0 < value["expires_at"] - value["issued_at"] <= 3600):
        _reject()
    return value


def packet_limits(pr):
    """Return unchanged defaults unless this exact review has an active scope."""
    value = _scope.get()
    if value is None:
        return DEFAULT_LIMITS
    if (not value["active"] or _identity(pr) != (value["repo"], value["pr"], value["head"]) or
            not value["issued_at"] <= time.time() < value["expires_at"]):
        _reject()
    return OPERATOR_LIMITS


@contextmanager
def operator_packet_scope(pr, path, *, intent_sha256, authorization_sha256):
    """Trusted caller supplies a private intent and independently verified hashes.

    Exiting closes even copied contexts; a mismatch/expiry raises rather than
    silently falling back to a smaller packet and losing required evidence.
    """
    if _scope.get() is not None:
        _reject()
    identity = _identity(pr)
    value = _read_intent(path, intent_sha256, authorization_sha256)
    if identity != (value["repo"], value["pr"], value["head"]):
        _reject()
    value["active"] = True
    token = _scope.set(value)
    try:
        packet_limits(pr)
        yield
    finally:
        value["active"] = False
        _scope.reset(token)
