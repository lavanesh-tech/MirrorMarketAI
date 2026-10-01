from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

DEAD_REDIS_URL = "redis://127.0.0.1:1/0"  # nothing listens on port 1


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url)
    yield client
    await client.aclose()


@pytest.fixture
async def dead_redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(DEAD_REDIS_URL, socket_connect_timeout=0.2, socket_timeout=0.2)
    yield client
    await client.aclose()
