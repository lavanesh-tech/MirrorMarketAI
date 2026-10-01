from __future__ import annotations

import pytest
from pydantic import BaseModel
from redis.asyncio import Redis

from app.coordination.cache import JsonCache

pytestmark = pytest.mark.redis


class Item(BaseModel):
    value: int


class Loader:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> Item:
        self.calls += 1
        return Item(value=self.calls)


async def test_miss_then_hit(redis: Redis) -> None:
    cache, load = JsonCache(redis, "t:", 60), Loader()
    first = await cache.get_or_load("ns", "e1", "p", Item, load)
    second = await cache.get_or_load("ns", "e1", "p", Item, load)
    assert first == (Item(value=1), False)
    assert second == (Item(value=1), True)
    assert load.calls == 1


async def test_params_and_entities_are_separate(redis: Redis) -> None:
    cache, load = JsonCache(redis, "t:", 60), Loader()
    await cache.get_or_load("ns", "e1", "a", Item, load)
    await cache.get_or_load("ns", "e1", "b", Item, load)
    await cache.get_or_load("ns", "e2", "a", Item, load)
    assert load.calls == 3


async def test_invalidate_bumps_version_and_only_that_entity(redis: Redis) -> None:
    cache, load = JsonCache(redis, "t:", 60), Loader()
    await cache.get_or_load("ns", "e1", "p", Item, load)
    await cache.get_or_load("ns", "e2", "p", Item, load)
    await cache.invalidate("ns", "e1")
    value, hit = await cache.get_or_load("ns", "e1", "p", Item, load)
    assert (value.value, hit) == (3, False)
    assert (await cache.get_or_load("ns", "e2", "p", Item, load))[1] is True


async def test_entries_have_ttl(redis: Redis) -> None:
    cache = JsonCache(redis, "t:", 30)
    await cache.get_or_load("ns", "e1", "p", Item, Loader())
    (key,) = await redis.keys("t:cache:*")
    assert 0 < await redis.ttl(key) <= 30


async def test_corrupt_entry_is_reloaded(redis: Redis) -> None:
    cache, load = JsonCache(redis, "t:", 60), Loader()
    await cache.get_or_load("ns", "e1", "p", Item, load)
    (key,) = await redis.keys("t:cache:*")
    await redis.set(key, b"not json")
    value, hit = await cache.get_or_load("ns", "e1", "p", Item, load)
    assert (value.value, hit) == (2, False)


async def test_without_redis_or_with_redis_down_it_just_loads(dead_redis: Redis) -> None:
    for cache in (JsonCache(None, "t:", 60), JsonCache(dead_redis, "t:", 60)):
        load = Loader()
        await cache.invalidate("ns", "e1")
        await cache.get_or_load("ns", "e1", "p", Item, load)
        assert (await cache.get_or_load("ns", "e1", "p", Item, load)) == (Item(value=2), False)
