"""Latency of the Redis-backed paths against real PostgreSQL + Redis.

Run (needs the stack up, e.g. `make up`):
  DATABASE_URL=postgresql+asyncpg://mirrormarket:mirrormarket@localhost:5433/mirrormarket \\
  REDIS_URL=redis://localhost:6380/0 uv run python -m benchmarks.redis_paths

Measures, on the same synthetic price data as `price_history_db`:
- price history computed from PostgreSQL (cache miss path),
- the same response served from the Redis cache (hit path),
- one GCRA rate-limit decision (a single Lua round trip).
Seeded rows and benchmark keys are deleted afterwards.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import random
import statistics
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.coordination.cache import JsonCache
from app.coordination.rate_limit import RateLimiter
from app.schemas.prices import PriceHistoryOut
from app.services.prices import PriceService
from benchmarks.citations import _commit
from benchmarks.price_history_db import ROWS, _seed

RUNS = 200
PREFIX = "bench:"


async def _time(fn: Callable[[], Awaitable[object]], runs: int) -> dict[str, float]:
    await fn()  # warm-up
    timings = []
    for _ in range(runs):
        started = time.perf_counter()
        await fn()
        timings.append((time.perf_counter() - started) * 1000)
    timings.sort()
    return {
        "median_ms": round(statistics.median(timings), 3),
        "p95_ms": round(timings[int(0.95 * (len(timings) - 1))], 3),
    }


async def run(database_url: str, redis_url: str) -> dict[str, object]:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(redis_url)
    rng = random.Random(7)  # noqa: S311 - reproducible synthetic data
    async with factory() as session:
        user_id, target = await _seed(session, rng)
        await session.commit()
    try:
        async with factory() as session:
            service = PriceService(session)

            async def load() -> PriceHistoryOut:
                return await service.history(
                    target, currency="USD", bucket="week", since=None, until=None
                )

            cache = JsonCache(redis, PREFIX, 300)

            async def cached() -> object:
                return await cache.get_or_load(
                    "prices", str(target), "USD|week", PriceHistoryOut, load
                )

            database = await _time(load, 20)
            await cached()
            hit = await _time(cached, RUNS)
            payload_bytes = len((await load()).model_dump_json())
        limiter = RateLimiter(redis, PREFIX)
        identity = uuid.uuid4().hex
        rate_limit = await _time(lambda: limiter.hit("bench", identity, 10**6, 60), RUNS)
    finally:
        async with factory() as session:
            await session.execute(
                text("DELETE FROM products WHERE created_by_id = :u"), {"u": user_id}
            )
            await session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
            await session.commit()
        keys = [key async for key in redis.scan_iter(f"{PREFIX}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        await engine.dispose()
    return {
        "benchmark": "redis_paths",
        "dataset": f"synthetic: {ROWS} price snapshots (same seed as price_history_db)",
        "price_history_from_postgres": database | {"runs": 20},
        "price_history_from_cache": hit | {"runs": RUNS, "payload_bytes": payload_bytes},
        "rate_limit_decision": rate_limit | {"runs": RUNS},
    }


def main() -> None:
    result = asyncio.run(run(os.environ["DATABASE_URL"], os.environ["REDIS_URL"])) | {
        "commit": _commit(),
        "python": platform.python_version(),
        "machine": platform.machine(),
    }
    out = Path(__file__).parent / "results" / "redis_paths.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
