"""Lane work queue + leases on JetStream (stage 1 of merging the file lane system into the cluster).

Today lanes are dispatched from a flat file (~/lane-wt/logs/fill.queue) and guarded by JSON claim files.
This module is the JetStream replacement, built so the file system and NATS can run side by side
(shadow mode) before cutover. See docs in cluster/LANES.md.

Primitives
----------
* Leases (KV bucket ``LANE_LEASES``): ``acquire`` uses KV *create*, which the server only accepts when
  the key is absent, so two machines can never both hold the same work. The returned revision is a
  fencing token: ``renew`` and ``release`` must present it (compare-and-set), so a lane whose lease
  lapsed and was re-taken cannot extend or drop the new holder's lease. Entries expire on their own
  after the bucket TTL unless renewed (heartbeat), so a dead lane frees its work without a reaper.
* Work queue (stream ``LANES``, work-queue retention, subjects ``lanes.<provider>.<job>``):
  ``enqueue`` sets ``Nats-Msg-Id`` to a dedupe key (job id + PR head SHA), so the same work published
  twice inside the duplicate window is stored once. Each provider gets one durable pull consumer whose
  ``max_ack_pending`` equals that provider's lane target, so the *server* enforces the concurrency cap
  that fill.sh used to approximate by counting screen sessions. Delivery is at-least-once: a lane must
  take the lease for the work before acting and ``ack`` only after a RESULT/BLOCKER line, ``term`` for
  work that must not be retried, and ``in_progress`` while running.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Optional

LEASE_BUCKET = "LANE_LEASES"
LANES_STREAM = "LANES"
LANES_SUBJECTS = "lanes.>"
DEFAULT_LEASE_S = 900
DEFAULT_DUP_WINDOW_S = 2 * 3600
PROVIDERS = ("codex", "cursor", "gemini", "claude", "minimax")
_TOKEN_RE = re.compile(r"[^A-Za-z0-9_-]+")
_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9]{0,15}$")


class LeaseHeld(Exception):
    """Another owner holds the lease."""

    def __init__(self, key: str, holder: dict):
        super().__init__(f"{key} held by {holder.get('owner')!r}")
        self.key = key
        self.holder = holder


class LeaseLost(Exception):
    """The caller's revision is stale: the lease expired or was taken by someone else."""


def token(text: str, limit: int = 60) -> str:
    """One NATS subject/KV token: letters, digits, '_' and '-' only (no dots, no wildcards)."""
    t = _TOKEN_RE.sub("-", (text or "").strip()).strip("-")[:limit]
    if not t:
        raise ValueError("empty token")
    return t


def lease_key(repo: str, kind: str, ref: str) -> str:
    """Key for one unit of work, e.g. lease_key('rupret007/webjam', 'pr', '390') -> 'rupret007.webjam.pr.390'.

    Keyed by repo + PR/issue (not by job id), so an auto-* job and a gemfix-* job on the same PR
    contend for the same lease: one writer per branch.
    """
    owner, _, name = (repo or "").partition("/")
    if not owner or not name:
        raise ValueError("repo must be OWNER/NAME")
    if kind not in ("pr", "issue", "task"):
        raise ValueError("kind must be pr, issue or task")
    return ".".join((token(owner), token(name), kind, token(str(ref))))


@dataclass
class Lease:
    key: str
    owner: str
    revision: int

    @property
    def fence(self) -> int:
        """Monotonic fencing token; pass it to anything that writes on the lease's behalf."""
        return self.revision


def _payload(owner: str, meta: Optional[dict]) -> bytes:
    body = {"owner": owner, "at": time.time()}
    if meta:
        body.update({k: v for k, v in meta.items() if k not in ("owner", "at")})
    return json.dumps(body, sort_keys=True).encode()


async def lease_bucket(js, ttl_s: int = DEFAULT_LEASE_S):
    """Get or create the lease bucket (history 1; entries expire ttl_s after their last write)."""
    from nats.js.api import KeyValueConfig, StorageType
    from nats.js.errors import BucketNotFoundError

    try:
        return await js.key_value(LEASE_BUCKET)
    except BucketNotFoundError:
        return await js.create_key_value(
            KeyValueConfig(bucket=LEASE_BUCKET, history=1, ttl=float(ttl_s), storage=StorageType.FILE)
        )


async def holder(kv, key: str) -> Optional[dict]:
    from nats.js.errors import KeyNotFoundError

    try:
        e = await kv.get(key)
    except KeyNotFoundError:
        return None
    if e is None or not e.value:
        return None
    try:
        return json.loads(e.value)
    except ValueError:
        return {"owner": "?"}


async def acquire(kv, key: str, owner: str, meta: Optional[dict] = None) -> Lease:
    """Take the lease or raise LeaseHeld. Atomic across every machine on the hub."""
    from nats.js.errors import KeyWrongLastSequenceError

    if not owner:
        raise ValueError("owner required")
    try:
        rev = await kv.create(key, _payload(owner, meta))
    except KeyWrongLastSequenceError:
        raise LeaseHeld(key, await holder(kv, key) or {}) from None
    return Lease(key, owner, rev)


async def renew(kv, lease: Lease, meta: Optional[dict] = None) -> Lease:
    """Heartbeat: compare-and-set on the caller's revision. Raises LeaseLost if it is stale."""
    from nats.js.errors import KeyWrongLastSequenceError

    try:
        rev = await kv.update(lease.key, _payload(lease.owner, meta), last=lease.revision)
    except KeyWrongLastSequenceError:
        raise LeaseLost(lease.key) from None
    return Lease(lease.key, lease.owner, rev)


async def release(kv, lease: Lease) -> bool:
    """Drop the lease only if the caller still holds it (stale releases are ignored, return False)."""
    from nats.js.errors import APIError, KeyWrongLastSequenceError

    try:
        await kv.delete(lease.key, last=lease.revision)
    except KeyWrongLastSequenceError:
        return False
    except APIError as e:  # nats-py 2.16 surfaces a CAS miss on delete as a plain 400, err_code 10071
        if getattr(e, "err_code", None) == 10071:
            return False
        raise
    return True


def subject(provider: str, job_id: str) -> str:
    if not _PROVIDER_RE.match(provider or ""):
        raise ValueError("bad provider")
    return f"lanes.{provider}.{token(job_id, 80)}"


def dedupe_key(job_id: str, head_sha: str = "") -> str:
    """Same job on the same head SHA is the same work; a new push is new work."""
    return f"{token(job_id, 80)}@{(head_sha or 'nohead')[:40]}"


async def ensure_lanes(js, targets: dict, ack_wait_s: int = 600, max_deliver: int = 3,
                       dup_window_s: int = DEFAULT_DUP_WINDOW_S, max_age_s: int = 24 * 3600) -> None:
    """Idempotently create the LANES stream and one consumer per provider.

    targets maps provider -> concurrent lane target (becomes max_ack_pending, the server-side cap).
    """
    from nats.js.api import AckPolicy, ConsumerConfig, RetentionPolicy, StorageType, StreamConfig
    from nats.js.errors import BadRequestError, NotFoundError

    cfg = StreamConfig(name=LANES_STREAM, subjects=[LANES_SUBJECTS], retention=RetentionPolicy.WORK_QUEUE,
                       storage=StorageType.FILE, max_age=float(max_age_s), duplicate_window=float(dup_window_s))
    try:
        await js.stream_info(LANES_STREAM)
        await js.update_stream(cfg)
    except NotFoundError:
        await js.add_stream(cfg)
    for provider, target in targets.items():
        if not _PROVIDER_RE.match(provider) or int(target) < 1:
            raise ValueError(f"bad target {provider}={target}")
        name = f"lane_{provider}"
        ccfg = ConsumerConfig(durable_name=name, ack_policy=AckPolicy.EXPLICIT, ack_wait=float(ack_wait_s),
                              max_deliver=max_deliver, filter_subject=f"lanes.{provider}.>",
                              max_ack_pending=int(target))
        try:
            await js.add_consumer(LANES_STREAM, ccfg)
        except BadRequestError:
            await js.delete_consumer(LANES_STREAM, name)  # config changed (e.g. new target): recreate
            await js.add_consumer(LANES_STREAM, ccfg)


async def enqueue(js, provider: str, job_id: str, spec: dict, head_sha: str = "") -> bool:
    """Publish one lane job. Returns True if stored, False if it was a duplicate inside the window."""
    body = dict(spec)
    body.update(job=job_id, provider=provider, head=head_sha or "")
    ack = await js.publish(subject(provider, job_id), json.dumps(body, sort_keys=True).encode(),
                           stream=LANES_STREAM, headers={"Nats-Msg-Id": dedupe_key(job_id, head_sha)})
    return not ack.duplicate


async def subscriber(js, provider: str):
    """Durable pull subscription for one provider's lane consumer (reuse it; unsubscribe when done)."""
    if not _PROVIDER_RE.match(provider or ""):
        raise ValueError("bad provider")
    return await js.pull_subscribe(f"lanes.{provider}.>", durable=f"lane_{provider}", stream=LANES_STREAM)


async def pull(sub, batch: int = 1, timeout: float = 2.0) -> list:
    """Fetch up to batch jobs; the server never hands out more than max_ack_pending unacked jobs."""
    import asyncio

    from nats.errors import TimeoutError as NatsTimeout

    try:
        return await sub.fetch(batch, timeout=timeout)
    except (NatsTimeout, asyncio.TimeoutError):  # empty queue or cap reached: not an error (10/16 worker crash loop)
        return []
