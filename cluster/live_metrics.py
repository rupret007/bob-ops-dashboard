"""Opt-in push metrics. No JetStream, leases, raw records or browser credentials."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import math
import time
from usage_collect import collect_machine_snapshot, HOST_LABELS
from nats.errors import Error as NatsError

PROVIDERS = ('codex', 'gemini', 'cursor', 'claude', 'minimax')
NUMBERS = ('load1', 'load5', 'mem_used_gb', 'swap_used_gb', 'disk_free_gb',
           'queue_depth', 'tokens_today', 'codex_weekly_pct', 'gemini_usd_today')


def number(value):
    return value if (type(value) in (int, float) and math.isfinite(value) and value >= 0) else None


def aggregate(host, machine, lanes, usage, *, now=None):
    if host not in HOST_LABELS:
        raise ValueError('expected mini or nucbox')
    out = {'v': 'live-metrics/v1', 'host': host, 'ts': time.time() if now is None else now,
           'lanes': {p: number(lanes.get(p)) for p in PROVIDERS},
           'grok_bot': {'label': 'Grok Bot (conserve-only)', 'status': 'unknown'}}
    out.update({k: number((machine if k in ('load1', 'load5', 'mem_used_gb', 'swap_used_gb',
                                          'disk_free_gb') else usage).get(k)) for k in NUMBERS})
    if out['codex_weekly_pct'] is not None and out['codex_weekly_pct'] > 100:
        out['codex_weekly_pct'] = None
    return out


def validate(raw):
    """Strict wire allowlist: reject, rather than forward arbitrary strings/fields."""
    if not isinstance(raw, dict) or raw.get('host') not in HOST_LABELS:
        raise ValueError('invalid metrics')
    if number(raw.get('ts')) is None or not isinstance(raw.get('lanes'), dict):
        raise ValueError('invalid metrics')
    clean = aggregate(raw['host'], raw, raw['lanes'], raw, now=raw['ts'])
    if clean != raw:
        raise ValueError('invalid metrics')
    return clean


class Publisher:
    """One instance per host runner; share it across all provider lanes.

    nc MUST be a dedicated metrics identity, never a LeaseService/worker client.
    Usage callback reads local sources; machine sampling is off the event loop.
    """
    def __init__(self, nc, host, usage=lambda: {}, machine=collect_machine_snapshot, providers=PROVIDERS):
        if host not in HOST_LABELS:
            raise ValueError('expected mini or nucbox')
        self.nc, self.host, self.usage, self.machine = nc, host, usage, machine
        if any(p not in PROVIDERS for p in providers):
            raise ValueError("unsupported provider")
        self.lanes = {p: 0 if p in providers else None for p in PROVIDERS}
        self.lock = None

    async def emit(self):
        if self.lock is None:
            self.lock = asyncio.Lock()
        async with self.lock:
            machine, usage = await asyncio.gather(asyncio.to_thread(self.machine),
                                                   asyncio.to_thread(self.usage))
            payload = aggregate(self.host, machine, self.lanes, usage)
            try:
                await self.nc.publish('metrics.' + self.host, json.dumps(payload, allow_nan=False).encode())
                await self.nc.flush(timeout=2)
            except (OSError, asyncio.TimeoutError, NatsError):
                return False
            return True

    async def periodic(self, interval=2):
        if not 1 <= interval <= 2:
            raise ValueError('metrics interval must be 1–2 seconds')
        while True:
            started = time.monotonic()
            await self.emit()
            await asyncio.sleep(max(0, interval - (time.monotonic() - started)))

    @asynccontextmanager
    async def lane(self, provider):
        if provider not in PROVIDERS or self.lanes[provider] is None:
            raise ValueError('unsupported lane provider; Grok Bot is conserve-only')
        self.lanes[provider] += 1
        try:
            await self.emit()
            yield
        finally:
            self.lanes[provider] -= 1
            await self.emit()


def delivery_queue_depth(messages):
    """Ready messages remaining in the existing worker consumer, as observed at delivery.

    Empty/time-out fetch is not a queue census. Never query JetStream with the
    metrics identity or infer zero from an idle fetch.
    """
    if not messages:
        return None
    metadata = messages[-1].metadata
    depth = getattr(metadata, 'num_pending', None)
    return depth if type(depth) is int and depth >= 0 else None
