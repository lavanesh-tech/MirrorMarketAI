"""Redis client lifecycle.

One `redis.asyncio.Redis` (a connection pool) per process, created in the app
lifespan. `REDIS_URL` may be empty: then every Redis-backed feature degrades to
a no-op (no cache, no rate limits, no idempotency replay). Short socket timeouts
keep a slow or dead Redis from stalling requests, because callers fail open.
"""

from __future__ import annotations

import time

from redis.asyncio import Redis

from app.core.config import Settings


def create_redis(settings: Settings) -> Redis | None:
    if settings.redis_url is None:
        return None
    return Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=settings.redis_timeout_seconds,
        socket_timeout=settings.redis_timeout_seconds,
        health_check_interval=30,
    )


async def ping(redis: Redis) -> float:
    """Round-trip latency in milliseconds (raises on failure)."""
    started = time.perf_counter()
    await redis.ping()
    return round((time.perf_counter() - started) * 1000, 2)


def create_subscriber_redis(settings: Settings) -> Redis | None:
    """A separate client for pub/sub: it blocks waiting for messages, so no read timeout."""
    if settings.redis_url is None:
        return None
    return Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=settings.redis_timeout_seconds,
        socket_timeout=None,
        health_check_interval=30,
    )
