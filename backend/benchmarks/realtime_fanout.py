"""Event fan-out latency: publish -> Redis pub/sub -> queued for every connection in a room.

Run (needs Redis, e.g. `make up`):
  REDIS_URL=redis://localhost:6380/0 uv run python -m benchmarks.realtime_fanout

Measures the time from `EventBus.publish` until the event sits in the outbound
queue of all N connections of one workspace, through a real Redis round trip and
with Redis disabled (single-process delivery). It does NOT include the network
write to browsers: connections here are in-memory queues, not sockets.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import statistics
import time
import uuid
from pathlib import Path

from redis.asyncio import Redis

from app.realtime.bus import EventBus
from app.realtime.hub import Connection, Hub
from benchmarks.citations import _commit

RUNS = 200
WARMUP = 5
ROOM_SIZES = (10, 100, 1000)
PREFIX = "bench:"


async def _measure(bus: EventBus, hub: Hub, size: int) -> dict[str, float | bool]:
    workspace_id = uuid.uuid4()
    connections = [
        Connection(workspace_id, uuid.uuid4(), asyncio.Queue(maxsize=RUNS + 10))
        for _ in range(size)
    ]
    for connection in connections:
        hub.join(connection)
    last = connections[-1]
    timings = []
    for i in range(RUNS + WARMUP):
        started = time.perf_counter()
        await bus.publish(workspace_id, "bench", {"i": i})
        await last.queue.get()
        if i >= WARMUP:
            timings.append((time.perf_counter() - started) * 1000)
    delivered = min(c.queue.qsize() for c in connections[:-1]) if size > 1 else RUNS + WARMUP
    for connection in connections:
        hub.leave(connection)
    timings.sort()
    return {
        "median_ms": round(statistics.median(timings), 3),
        "p95_ms": round(timings[int(0.95 * (len(timings) - 1))], 3),
        "every_connection_got_every_event": delivered == RUNS + WARMUP,
    }


async def run(redis_url: str) -> dict[str, object]:
    results: dict[str, object] = {}
    local_hub = Hub()
    local = EventBus(local_hub, None, None, PREFIX)
    publisher = Redis.from_url(redis_url)
    subscriber = Redis.from_url(redis_url, socket_timeout=None)
    hub = Hub()
    bus = EventBus(hub, publisher, subscriber, PREFIX)
    bus.start()
    try:
        async with asyncio.timeout(5):
            await bus.subscribed.wait()
        for size in ROOM_SIZES:
            results[f"room_{size}"] = {
                "via_redis_pubsub": await _measure(bus, hub, size),
                "single_process": await _measure(local, local_hub, size),
            }
    finally:
        await bus.stop()
        await subscriber.aclose()
        await publisher.aclose()
    return {
        "benchmark": "realtime_fanout",
        "what": "publish -> queued for all connections of one workspace (no socket I/O)",
        "runs_per_case": RUNS,
        "results": results,
    }


def main() -> None:
    result = asyncio.run(run(os.environ["REDIS_URL"])) | {
        "commit": _commit(),
        "python": platform.python_version(),
        "machine": platform.machine(),
    }
    out = Path(__file__).parent / "results" / "realtime_fanout.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
