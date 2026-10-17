#!/usr/bin/env python3
"""Create JetStream streams and consumers (idempotent)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

STREAM_JOBS = "JOBS"
STREAM_RESULTS = "RESULTS"
CONSUMER_WORKERS = "workers"


async def setup(nc_url: str, token: str, user: str) -> None:
    import nats
    from nats.js.api import (
        AckPolicy,
        ConsumerConfig,
        RetentionPolicy,
        StorageType,
        StreamConfig,
    )

    nc = await nats.connect(nc_url, user=user, password=token)
    js = nc.jetstream()

    jobs_cfg = StreamConfig(
        name=STREAM_JOBS,
        subjects=["jobs.>"],
        retention=RetentionPolicy.WORK_QUEUE,
        storage=StorageType.FILE,
        max_age=24 * 3600,
    )
    from nats.js.errors import BadRequestError

    try:
        await js.add_stream(jobs_cfg)
        print(f"created stream {STREAM_JOBS}")
    except BadRequestError:
        await js.update_stream(jobs_cfg)
        print(f"updated stream {STREAM_JOBS}")

    results_cfg = StreamConfig(
        name=STREAM_RESULTS,
        subjects=["results.>"],
        retention=RetentionPolicy.LIMITS,
        storage=StorageType.FILE,
        max_age=7 * 24 * 3600,
    )
    try:
        await js.add_stream(results_cfg)
        print(f"created stream {STREAM_RESULTS}")
    except BadRequestError:
        await js.update_stream(results_cfg)
        print(f"updated stream {STREAM_RESULTS}")

    consumer_cfg = ConsumerConfig(
        durable_name=CONSUMER_WORKERS,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=30 * 60,
        max_deliver=3,
        filter_subject="jobs.>",
    )
    try:
        await js.add_consumer(STREAM_JOBS, consumer_cfg)
        print(f"created consumer {CONSUMER_WORKERS}")
    except BadRequestError:
        print(f"consumer {CONSUMER_WORKERS} already exists")

    await nc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Setup NATS JetStream for Bob cluster")
    parser.add_argument(
        "--nats",
        default=os.environ.get("BOB_NATS_URL", "nats://127.0.0.1:4222"),
    )
    parser.add_argument("--user", default=os.environ.get("BOB_NATS_USER", "nucbox"))
    parser.add_argument(
        "--token",
        default=os.environ.get("BOB_NODE_TOKEN", ""),
        help="NATS password/token",
    )
    parser.add_argument(
        "--token-file",
        default=os.environ.get("BOB_NODE_TOKEN_FILE", ""),
    )
    args = parser.parse_args()
    token = args.token
    if args.token_file:
        with open(args.token_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{args.user.upper()}_TOKEN=") or line.startswith("NUCBOX_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"')
                    break
    if not token:
        print("BOB_NODE_TOKEN or --token-file required", file=sys.stderr)
        return 1
    asyncio.run(setup(args.nats, token, args.user))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
