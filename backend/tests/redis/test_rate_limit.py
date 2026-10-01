from __future__ import annotations

import asyncio

import pytest
from redis.asyncio import Redis

from app.coordination.rate_limit import RateLimiter

pytestmark = pytest.mark.redis


async def test_allows_limit_then_blocks_with_retry_after(redis: Redis) -> None:
    limiter = RateLimiter(redis, "t:")
    decisions = [await limiter.hit("login", "1.2.3.4", 3, 60) for _ in range(4)]
    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert [d.remaining for d in decisions[:3]] == [2, 1, 0]
    assert 1 <= decisions[3].retry_after_seconds <= 20  # one interval = 60 s / 3


async def test_identities_and_names_are_independent(redis: Redis) -> None:
    limiter = RateLimiter(redis, "t:")
    assert (await limiter.hit("login", "a", 1, 60)).allowed
    assert not (await limiter.hit("login", "a", 1, 60)).allowed
    assert (await limiter.hit("login", "b", 1, 60)).allowed
    assert (await limiter.hit("register", "a", 1, 60)).allowed


async def test_keys_hold_no_raw_identity_and_expire(redis: Redis) -> None:
    limiter = RateLimiter(redis, "t:")
    await limiter.hit("login", "alice@example.com", 5, 60)
    keys = [k.decode() if isinstance(k, bytes) else k for k in await redis.keys("t:rl:*")]
    assert len(keys) == 1
    assert "alice" not in keys[0]
    assert 0 < await redis.pttl(keys[0]) <= 12_000  # TAT is at most one interval ahead


async def test_tokens_refill_over_time(redis: Redis) -> None:
    limiter = RateLimiter(redis, "t:")
    assert (await limiter.hit("x", "id", 1, 1)).allowed
    assert not (await limiter.hit("x", "id", 1, 1)).allowed
    await asyncio.sleep(1.05)
    assert (await limiter.hit("x", "id", 1, 1)).allowed


async def test_without_redis_everything_is_allowed() -> None:
    limiter = RateLimiter(None, "t:")
    decision = await limiter.hit("login", "x", 1, 60)
    assert decision.allowed
    assert decision.remaining == 1


async def test_fails_open_when_redis_is_down(dead_redis: Redis) -> None:
    limiter = RateLimiter(dead_redis, "t:")
    for _ in range(3):
        assert (await limiter.hit("login", "x", 1, 60)).allowed
