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
* Lease service (Karen L1): lane hosts never touch ``$KV.LANE_LEASES.>`` themselves. A raw KV put or
  delete skips the revision check, so any host with that publish grant could steal or drop any lease.
  Hosts send lease requests to ``lanesvc.lease.<host>.<op>`` (their own host token only, enforced by
  the stage-2 permissions in ``lanes_perms.py``). ``LeaseService`` runs as the hub operator, binds the
  lease owner to the requesting host and does the compare-and-set itself. ``LeaseClient`` is the host
  side. Cross-host exclusivity is kept because lease keys are still repo + PR, not per host.
* Work queue (stream ``LANES``, work-queue retention, subjects ``lanes.<provider>.<host>.<job>``):
  each host gets its own durable pull consumer ``lane_<provider>_<host>`` (Karen L1: ``$JS.ACK`` is
  granted per consumer, so a host can only ack, nak or term its own deliveries). ``max_ack_pending``
  is that host's lane target for the provider, so the *server* enforces the concurrency cap.
  ``enqueue`` sets ``Nats-Msg-Id`` to provider + job + head SHA (+ attempt), so the same work published
  twice inside the duplicate window is stored once, without one provider's copy swallowing another's.
  Delivery is at-least-once: a lane takes the lease for the work before acting (``take``), naks with a
  delay of one lease TTL when another lane holds it (Karen L3), acks only after a RESULT/BLOCKER line,
  ``term``s work that must not be retried and sends ``in_progress`` while running.
* Dead letters (stream ``LANES_DLQ``, Karen L4): ``DeadLetterWatcher`` listens for the server's
  MAX_DELIVERIES advisories on ``LANES``, copies the exhausted job into ``LANES_DLQ`` with its delivery
  facts and removes it from the work queue. ``dead_letters`` lists them for the dashboard.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Optional

LEASE_BUCKET = "LANE_LEASES"
LEASE_STREAM = "KV_" + LEASE_BUCKET
LANES_STREAM = "LANES"
LANES_SUBJECTS = "lanes.*.*.*"  # lanes.<provider>.<host>.<job>
DLQ_STREAM = "LANES_DLQ"
DLQ_SUBJECTS = "lanes_dlq.*.*"  # lanes_dlq.<provider>.<host>
LEASE_SVC_PREFIX = "lanesvc.lease"
LEASE_SVC_QUEUE = "lanesvc"
LEASE_OPS = ("acquire", "renew", "release", "holder")
MAX_DELIVERIES_ADVISORY = "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES." + LANES_STREAM + ".*"
DEFAULT_LEASE_S = 900
DEFAULT_DUP_WINDOW_S = 2 * 3600
DEFAULT_DLQ_AGE_S = 7 * 24 * 3600
MAX_META_BYTES = 2048
PROVIDERS = ("codex", "cursor", "gemini", "claude", "minimax")
_TOKEN_RE = re.compile(r"[^A-Za-z0-9_-]+")
_HOST_RE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
# Role names that own a reply inbox on the hub. A lane host with one of these names would get the
# role's inbox (``_INBOX_hub`` carries the operator's JetStream API replies), so they are never hosts.
RESERVED_HOST_NAMES = frozenset({"hub", "conductor"})
_OWNER_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,80}$")


class LeaseHeld(Exception):
    """Another owner holds the lease."""

    def __init__(self, key: str, holder: dict):
        super().__init__(f"{key} held by {holder.get('owner')!r}")
        self.key = key
        self.holder = holder


class LeaseLost(Exception):
    """The caller's revision is stale: the lease expired or was taken by someone else."""


class LeaseForbidden(PermissionError):
    """The lease service refused the request (owner not bound to the requesting host, bad input)."""


class LaneConfigConflict(RuntimeError):
    """An existing consumer differs in a field the server cannot edit in place (err_code 10012)."""


def token(text: str, limit: int = 60) -> str:
    """One NATS subject/KV token: letters, digits, '_' and '-' only (no dots, no wildcards).

    Lossy input (characters replaced, or longer than ``limit``) gets an 8-hex sha256 suffix of the
    original text, so 'My.Repo' and 'My-Repo', or two long ids sharing a prefix, never collapse into
    the same lease or dedupe key (Karen L6).
    """
    raw = (text or "").strip()
    t = _TOKEN_RE.sub("-", raw).strip("-")
    if not t:
        raise ValueError("empty token")
    if t != raw or len(t) > limit:
        digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
        t = f"{t[: max(1, limit - 9)].rstrip('-')}-{digest}"
    return t


def check_provider(provider: str) -> str:
    """Allowlist: only providers in PROVIDERS can be enqueued, consumed or configured."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")
    return provider


def is_valid_host(host) -> bool:
    """A lane host name: lowercase token, and never a reserved role name (Karen S1)."""
    return isinstance(host, str) and bool(_HOST_RE.fullmatch(host)) and host not in RESERVED_HOST_NAMES


def check_host(host: str) -> str:
    if not is_valid_host(host):
        raise ValueError(f"bad host {host!r}")
    return host


def consumer_name(provider: str, host: str) -> str:
    return f"lane_{check_provider(provider)}_{check_host(host)}"


def lease_key(repo: str, kind: str, ref: str) -> str:
    """Key for one unit of work, e.g. lease_key('rupret007/webjam', 'pr', '390') -> 'rupret007.webjam.pr.390'.

    Keyed by repo + PR/issue (not by job id or host), so an auto-* job and a gemfix-* job on the same PR
    contend for the same lease from any machine: one writer per branch.
    """
    owner, _, name = (repo or "").partition("/")
    if not owner or not name:
        raise ValueError("repo must be OWNER/NAME")
    if kind not in ("pr", "issue", "task"):
        raise ValueError("kind must be pr, issue or task")
    return ".".join((token(owner), token(name), kind, token(str(ref))))


def owner_host(owner: str) -> str:
    """Owners are '<host>' or '<host>/<lane>'; the host part is what the lease service binds to."""
    return (owner or "").split("/", 1)[0]


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
    """Get or create the lease bucket (history 1; entries expire ttl_s after their last write).

    If the bucket already exists with a different TTL, the TTL is applied to it (Karen L6: it used to
    be ignored silently, so a config change never took effect).
    """
    from nats.js.api import KeyValueConfig, StorageType
    from nats.js.errors import BucketNotFoundError

    try:
        kv = await js.key_value(LEASE_BUCKET)
    except BucketNotFoundError:
        return await js.create_key_value(
            KeyValueConfig(bucket=LEASE_BUCKET, history=1, ttl=float(ttl_s), storage=StorageType.FILE)
        )
    info = await js.stream_info(LEASE_STREAM)
    if abs(float(info.config.max_age or 0) - float(ttl_s)) > 0.001:
        info.config.max_age = float(ttl_s)
        await js.update_stream(info.config)
    return kv


async def holder(kv, key: str) -> Optional[dict]:
    entry = await _entry(kv, key)
    if entry is None:
        return None
    return _decode(entry.value)


def _decode(value) -> dict:
    try:
        d = json.loads(value)
        return d if isinstance(d, dict) else {"owner": "?"}
    except ValueError:
        return {"owner": "?"}


async def _entry(kv, key: str):
    from nats.js.errors import KeyNotFoundError

    try:
        e = await kv.get(key)
    except KeyNotFoundError:
        return None
    if e is None or not e.value:
        return None
    return e


async def acquire(kv, key: str, owner: str, meta: Optional[dict] = None) -> Lease:
    """Take the lease or raise LeaseHeld. Atomic across every machine on the hub. Trusted side only."""
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


# --------------------------------------------------------------------------------------------------
# Lease service (hub operator) and client (lane hosts). Karen L1.
# --------------------------------------------------------------------------------------------------

def lease_subject(host: str, op: str) -> str:
    if op not in LEASE_OPS:
        raise ValueError(f"bad lease op {op!r}")
    return f"{LEASE_SVC_PREFIX}.{check_host(host)}.{op}"


class LeaseService:
    """Hub-side lease authority. Only this process (operator credentials) writes ``$KV.LANE_LEASES``.

    The requesting host is taken from the request *subject* (``lanesvc.lease.<host>.<op>``), which the
    stage-2 permissions let each host publish only for its own host token. The service refuses any
    owner whose host part differs, and for renew/release it also checks that the stored holder belongs
    to that host before the compare-and-set, so guessing another host's revision gets nothing.
    """

    def __init__(self, nc, kv):
        self.nc = nc
        self.kv = kv
        self.sub = None

    async def start(self):
        self.sub = await self.nc.subscribe(f"{LEASE_SVC_PREFIX}.*.*", queue=LEASE_SVC_QUEUE, cb=self._on_msg)
        return self

    async def stop(self):
        if self.sub is not None:
            await self.sub.unsubscribe()
            self.sub = None

    async def _on_msg(self, msg):
        try:
            reply = await self.handle(msg.subject, msg.data)
        except Exception as e:  # never crash the service on one bad request
            reply = {"ok": False, "error": "internal", "detail": type(e).__name__}
        if msg.reply:
            await self.nc.publish(msg.reply, json.dumps(reply, sort_keys=True).encode())

    async def handle(self, subject: str, data: bytes) -> dict:
        parts = subject.split(".")
        if len(parts) != 4 or ".".join(parts[:2]) != LEASE_SVC_PREFIX:
            return {"ok": False, "error": "forbidden", "detail": "bad subject"}
        host, op = parts[2], parts[3]
        if not is_valid_host(host) or op not in LEASE_OPS:
            return {"ok": False, "error": "forbidden", "detail": "bad host or op"}
        if len(data) > MAX_META_BYTES + 512:
            return {"ok": False, "error": "forbidden", "detail": "request too large"}
        try:
            req = json.loads(data or b"{}")
            key = str(req.get("key") or "")
            if not key or len(key) > 255 or any(c in key for c in " *>\t\r\n") or key.startswith(".") \
                    or key.endswith(".") or ".." in key:
                raise ValueError("bad key")
        except (ValueError, AttributeError):
            return {"ok": False, "error": "forbidden", "detail": "bad request"}

        if op == "holder":
            h = await holder(self.kv, key)
            return {"ok": True, "holder": h}

        owner = str(req.get("owner") or "")
        if not _OWNER_RE.match(owner) or owner_host(owner) != host:
            return {"ok": False, "error": "forbidden", "detail": "owner must be <host> or <host>/<lane>"}
        meta = req.get("meta")
        if meta is not None and (not isinstance(meta, dict) or len(json.dumps(meta)) > MAX_META_BYTES):
            return {"ok": False, "error": "forbidden", "detail": "bad meta"}
        meta = dict(meta or {})
        meta["host"] = host

        if op == "acquire":
            try:
                lease = await acquire(self.kv, key, owner, meta)
            except LeaseHeld as e:
                return {"ok": False, "error": "held", "holder": e.holder}
            return {"ok": True, "revision": lease.revision}

        try:
            revision = int(req.get("revision"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "forbidden", "detail": "revision required"}
        entry = await _entry(self.kv, key)
        if entry is None or entry.revision != revision:
            return {"ok": False, "error": "lost"}
        stored = _decode(entry.value)
        if stored.get("owner") != owner or owner_host(stored.get("owner", "")) != host:
            return {"ok": False, "error": "lost"}
        lease = Lease(key, owner, revision)
        if op == "renew":
            try:
                lease = await renew(self.kv, lease, meta)
            except LeaseLost:
                return {"ok": False, "error": "lost"}
            return {"ok": True, "revision": lease.revision}
        released = await release(self.kv, lease)
        return {"ok": released} if released else {"ok": False, "error": "lost"}


class LeaseClient:
    """Lane-host side of the lease service. Owners must be '<host>' or '<host>/<lane>'.

    Connect the host with ``inbox_prefix=inbox_prefix(host)`` so replies land in the host's own inbox
    namespace (the stage-2 permissions only let it subscribe there).
    """

    def __init__(self, nc, host: str, timeout: float = 5.0):
        self.nc = nc
        self.host = check_host(host)
        self.timeout = timeout

    async def _call(self, op: str, body: dict) -> dict:
        msg = await self.nc.request(lease_subject(self.host, op), json.dumps(body, sort_keys=True).encode(),
                                    timeout=self.timeout)
        return json.loads(msg.data)

    @staticmethod
    def _raise(key: str, r: dict):
        err = r.get("error")
        if err == "held":
            raise LeaseHeld(key, r.get("holder") or {})
        if err == "lost":
            raise LeaseLost(key)
        raise LeaseForbidden(f"{key}: {err}: {r.get('detail', '')}".rstrip(": "))

    async def acquire(self, key: str, owner: str, meta: Optional[dict] = None) -> Lease:
        r = await self._call("acquire", {"key": key, "owner": owner, "meta": meta or {}})
        if not r.get("ok"):
            self._raise(key, r)
        return Lease(key, owner, int(r["revision"]))

    async def renew(self, lease: Lease, meta: Optional[dict] = None) -> Lease:
        r = await self._call("renew", {"key": lease.key, "owner": lease.owner, "revision": lease.revision,
                                       "meta": meta or {}})
        if not r.get("ok"):
            self._raise(lease.key, r)
        return Lease(lease.key, lease.owner, int(r["revision"]))

    async def release(self, lease: Lease) -> bool:
        r = await self._call("release", {"key": lease.key, "owner": lease.owner, "revision": lease.revision})
        if r.get("ok"):
            return True
        if r.get("error") == "lost":
            return False
        self._raise(lease.key, r)

    async def holder(self, key: str) -> Optional[dict]:
        r = await self._call("holder", {"key": key})
        if not r.get("ok"):
            self._raise(key, r)
        return r.get("holder")


def inbox_prefix(host: str) -> str:
    """Per-host reply inbox namespace, so one host cannot read another host's replies or deliveries."""
    return f"_INBOX_{check_host(host)}"


def role_inbox_prefix(role: str) -> str:
    """Reply inbox for a hub role (``hub`` operator, ``conductor``). Lane hosts can never take these names."""
    if role not in RESERVED_HOST_NAMES:
        raise ValueError(f"unknown role {role!r}")
    return f"_INBOX_{role}"


OPERATOR_INBOX = role_inbox_prefix("hub")


# --------------------------------------------------------------------------------------------------
# Work queue
# --------------------------------------------------------------------------------------------------

def subject(provider: str, host: str, job_id: str) -> str:
    return f"lanes.{check_provider(provider)}.{check_host(host)}.{token(job_id, 80)}"


def dedupe_key(provider: str, job_id: str, head_sha: str = "", attempt: int = 0, spec: Optional[dict] = None) -> str:
    """Same provider + job on the same head SHA is the same work; a new push is new work.

    * The provider is part of the key, so one provider's copy never swallows another's.
    * ``attempt`` > 0 makes a deliberate retry on the same SHA (after FAILED/BLOCKER) new work (Karen L5).
    * Jobs without a SHA are keyed by a hash of their spec instead of a shared '@nohead', so unrelated
      headless jobs are not suppressed for the whole window (Karen L5).
    The host is deliberately *not* part of the key: routing the same work to a second host is a duplicate.
    """
    attempt = int(attempt)
    if attempt < 0:
        raise ValueError("attempt must be >= 0")
    head = (head_sha or "").strip()[:40]
    if not head:
        canon = json.dumps(spec or {}, sort_keys=True, default=str).encode()
        head = "nohead-" + hashlib.sha256(canon).hexdigest()[:12]
    key = f"{check_provider(provider)}:{token(job_id, 80)}@{head}"
    return f"{key}#a{attempt}" if attempt else key


def _stream_drift(have, want) -> bool:
    """Only the fields this module manages; manual edits to anything else are left alone (Karen L6)."""
    def norm(v):
        return getattr(v, "value", v)

    for field in ("subjects", "retention", "storage", "max_age", "duplicate_window"):
        a, b = norm(getattr(have, field, None)), norm(getattr(want, field, None))
        if isinstance(a, float) or isinstance(b, float):
            if abs(float(a or 0) - float(b or 0)) > 0.001:
                return True
        elif a != b:
            return True
    return False


async def _ensure_stream(js, cfg) -> None:
    from nats.js.errors import NotFoundError

    try:
        info = await js.stream_info(cfg.name)
    except NotFoundError:
        await js.add_stream(cfg)
        return
    if _stream_drift(info.config, cfg):
        for field in ("subjects", "retention", "storage", "max_age", "duplicate_window"):
            setattr(info.config, field, getattr(cfg, field))
        await js.update_stream(info.config)


async def ensure_lanes(js, targets: dict, ack_wait_s: int = 600, max_deliver: int = 3,
                       dup_window_s: int = DEFAULT_DUP_WINDOW_S, max_age_s: int = 24 * 3600,
                       dlq_age_s: int = DEFAULT_DLQ_AGE_S, recreate_idle: bool = False) -> None:
    """Idempotently create the LANES and LANES_DLQ streams and one consumer per (provider, host).

    targets maps provider -> {host: concurrent lane target}; the target becomes that host's consumer
    ``max_ack_pending`` (the server-side cap). Editable changes (e.g. a new target) are applied in place.

    If an existing consumer differs in a field the server cannot edit (err_code 10012, e.g. ack policy),
    this raises LaneConfigConflict instead of crashing on an unhandled ServerError (Karen L2). With
    ``recreate_idle=True`` it deletes and recreates the consumer only when nothing is in flight
    (``num_ack_pending == 0``); in-flight ack state is never thrown away.
    """
    from nats.js.api import AckPolicy, ConsumerConfig, RetentionPolicy, StorageType, StreamConfig
    from nats.js.errors import APIError

    plan = []
    for provider, hosts in (targets or {}).items():
        check_provider(provider)
        if not isinstance(hosts, dict) or not hosts:
            raise ValueError(f"targets[{provider!r}] must map host -> lane target")
        for host, target in hosts.items():
            check_host(host)
            if int(target) < 1:
                raise ValueError(f"bad target {provider}/{host}={target}")
            plan.append((provider, host, int(target)))

    await _ensure_stream(js, StreamConfig(
        name=LANES_STREAM, subjects=[LANES_SUBJECTS], retention=RetentionPolicy.WORK_QUEUE,
        storage=StorageType.FILE, max_age=float(max_age_s), duplicate_window=float(dup_window_s)))
    await _ensure_stream(js, StreamConfig(
        name=DLQ_STREAM, subjects=[DLQ_SUBJECTS], retention=RetentionPolicy.LIMITS,
        storage=StorageType.FILE, max_age=float(dlq_age_s), duplicate_window=float(dup_window_s)))

    for provider, host, target in plan:
        name = consumer_name(provider, host)
        ccfg = ConsumerConfig(durable_name=name, name=name, ack_policy=AckPolicy.EXPLICIT,
                              ack_wait=float(ack_wait_s), max_deliver=max_deliver,
                              filter_subject=f"lanes.{provider}.{host}.*", max_ack_pending=target)
        try:
            await js.add_consumer(LANES_STREAM, ccfg)
        except APIError as e:
            if getattr(e, "err_code", None) != 10012:
                raise
            info = await js.consumer_info(LANES_STREAM, name)
            if not recreate_idle or info.num_ack_pending:
                raise LaneConfigConflict(
                    f"consumer {name} has a non-editable difference ({e.description}); "
                    f"{info.num_ack_pending} job(s) in flight. Not recreated: drain it, then rerun with "
                    "recreate_idle=True.") from None
            await js.delete_consumer(LANES_STREAM, name)
            await js.add_consumer(LANES_STREAM, ccfg)


async def enqueue(js, provider: str, job_id: str, spec: dict, head_sha: str = "", *, host: str,
                  attempt: int = 0) -> bool:
    """Publish one lane job for one host. True if stored, False if it was a duplicate in the window.

    False means *this exact work* (provider, job, head SHA, attempt) is already queued or ran inside the
    window; to retry it on the same SHA after FAILED/BLOCKER, pass ``attempt=N+1``. Unknown providers
    raise ValueError before anything is published.
    """
    check_provider(provider)
    check_host(host)
    body = dict(spec)
    body.update(job=job_id, provider=provider, host=host, head=head_sha or "", attempt=int(attempt))
    ack = await js.publish(subject(provider, host, job_id), json.dumps(body, sort_keys=True).encode(),
                           stream=LANES_STREAM,
                           headers={"Nats-Msg-Id": dedupe_key(provider, job_id, head_sha, attempt, spec)})
    return not ack.duplicate


async def subscriber(js, provider: str, host: str):
    """Bind to this host's durable pull consumer (created by ensure_lanes on the operator side).

    Uses bind (no consumer create/info calls), so a lane host only needs MSG.NEXT and ACK on its own
    consumer under the stage-2 permissions.
    """
    return await js.pull_subscribe_bind(durable=consumer_name(provider, host), stream=LANES_STREAM)


async def pull(sub, batch: int = 1, timeout: float = 2.0) -> list:
    """Fetch up to batch jobs; the server never hands out more than max_ack_pending unacked jobs."""
    import asyncio

    from nats.errors import TimeoutError as NatsTimeout

    try:
        return await sub.fetch(batch, timeout=timeout)
    except (NatsTimeout, asyncio.TimeoutError):  # empty queue or cap reached: not an error (10/16 worker crash loop)
        return []


async def take(msg, leases, key: str, owner: str, meta: Optional[dict] = None,
               nak_delay_s: float = DEFAULT_LEASE_S) -> Optional[Lease]:
    """LeaseHeld policy for a delivered job (Karen L3).

    Takes the lease for ``key``. If another lane holds it (typically a redelivery while the first lane is
    still running after a missed ``in_progress``), the job is ``nak``ed with a delay of one lease TTL:
    not ``term``ed, because the holder may still fail and the work must not be lost; not redelivered
    at once, because that would spin. Returns the Lease, or None when the job was nak'd.
    Each nak counts toward ``max_deliver``; a job that keeps hitting a held lease ends in LANES_DLQ.
    """
    try:
        return await leases.acquire(key, owner, meta)
    except LeaseHeld:
        await msg.nak(delay=nak_delay_s)
        return None


# --------------------------------------------------------------------------------------------------
# Dead letters (Karen L4)
# --------------------------------------------------------------------------------------------------

class DeadLetterWatcher:
    """Operator-side: move jobs that hit max_deliver from LANES into LANES_DLQ and surface them.

    Subscribes to ``$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.LANES.*``. For each advisory it copies the
    stored job to ``lanes_dlq.<provider>.<host>`` (headers carry the original sequence, consumer and
    delivery count; ``Nats-Msg-Id`` makes the copy idempotent) and deletes it from LANES, so it no
    longer sits silently until max_age. ``on_dead`` (optional coroutine) gets each record, for alerts.
    """

    def __init__(self, nc, js, on_dead=None):
        self.nc = nc
        self.js = js
        self.on_dead = on_dead
        self.sub = None
        self.moved = 0

    async def start(self):
        self.sub = await self.nc.subscribe(MAX_DELIVERIES_ADVISORY, cb=self._on_advisory)
        return self

    async def stop(self):
        if self.sub is not None:
            await self.sub.unsubscribe()
            self.sub = None

    async def _on_advisory(self, msg):
        try:
            adv = json.loads(msg.data)
            await self.move(int(adv["stream_seq"]), str(adv.get("consumer", "")), int(adv.get("deliveries", 0)))
        except Exception:  # an unparseable advisory must not kill the watcher
            return

    async def move(self, seq: int, consumer: str, deliveries: int) -> Optional[dict]:
        from nats.js.errors import NotFoundError

        try:
            raw = await self.js.get_msg(LANES_STREAM, seq)
        except NotFoundError:
            return None  # already acked/termed/moved
        parts = (raw.subject or "").split(".")
        provider, host = (parts[1], parts[2]) if len(parts) == 4 else ("unknown", "unknown")
        if provider not in PROVIDERS or not is_valid_host(host):
            provider, host = "unknown", "unknown"
        headers = {"Nats-Msg-Id": dlq_msg_id(raw), "Lanes-Orig-Seq": str(seq),
                   "Lanes-Orig-Subject": raw.subject or "", "Lanes-Consumer": consumer,
                   "Lanes-Deliveries": str(deliveries)}
        await self.js.publish(f"lanes_dlq.{provider}.{host}", raw.data or b"", stream=DLQ_STREAM, headers=headers)
        try:
            await self.js.delete_msg(LANES_STREAM, seq)
        except NotFoundError:
            pass
        self.moved += 1
        rec = {"seq": seq, "subject": raw.subject, "consumer": consumer, "deliveries": deliveries,
               "job": _decode(raw.data or b"{}")}
        if self.on_dead is not None:
            await self.on_dead(rec)
        return rec


def dlq_msg_id(raw) -> str:
    """Idempotency key for a dead-letter copy, independent of the LANES sequence number (Karen S3).

    Keyed on the job's own enqueue ``Nats-Msg-Id`` (provider + job + head SHA + attempt), so a stream that
    was recreated (sequence numbers restart) can never make a *different* job look like a duplicate of
    an earlier dead letter. Without that header, falls back to a hash of the original subject and body.
    """
    orig = ""
    hdrs = getattr(raw, "headers", None) or {}
    if isinstance(hdrs, dict):
        orig = str(hdrs.get("Nats-Msg-Id") or "").strip()
    if orig:
        return f"dlq-{orig}"
    h = hashlib.sha256((raw.subject or "").encode() + b"\n" + (raw.data or b"")).hexdigest()[:24]
    return f"dlq-sha-{h}"


async def dead_letters(js, limit: int = 50) -> list:
    """Newest-first list of dead-lettered jobs for the dashboard (reads LANES_DLQ)."""
    from nats.js.errors import NotFoundError

    try:
        info = await js.stream_info(DLQ_STREAM)
    except NotFoundError:
        return []
    out = []
    seq = info.state.last_seq
    while seq >= max(1, info.state.first_seq) and len(out) < limit:
        try:
            raw = await js.get_msg(DLQ_STREAM, seq)
        except NotFoundError:
            seq -= 1
            continue
        h = raw.headers or {}
        out.append({"seq": int(h.get("Lanes-Orig-Seq", 0) or 0), "subject": h.get("Lanes-Orig-Subject", ""),
                    "consumer": h.get("Lanes-Consumer", ""), "deliveries": int(h.get("Lanes-Deliveries", 0) or 0),
                    "job": _decode(raw.data or b"{}")})
        seq -= 1
    return out
