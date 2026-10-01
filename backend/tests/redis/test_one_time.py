from __future__ import annotations

import asyncio
import base64
import hashlib

import pytest
from redis.asyncio import Redis

from app.coordination.one_time import OneTimeTokenStore, pkce_pair


def test_pkce_challenge_is_s256_of_verifier() -> None:
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge == expected.rstrip(b"=").decode()
    assert pkce_pair()[0] != verifier


@pytest.mark.redis
async def test_token_is_single_use(redis: Redis) -> None:
    store = OneTimeTokenStore(redis, "t:")
    token = await store.issue("oauth", {"redirect": "/home", "verifier": "v"}, 60)
    assert await store.consume("oauth", token) == {"redirect": "/home", "verifier": "v"}
    assert await store.consume("oauth", token) is None


@pytest.mark.redis
async def test_purpose_unknown_and_storage_are_safe(redis: Redis) -> None:
    store = OneTimeTokenStore(redis, "t:")
    token = await store.issue("oauth", {"a": 1}, 60)
    assert await store.consume("email", token) is None
    assert await store.consume("oauth", "not-a-token") is None
    (key,) = await redis.keys("t:once:*")
    assert token not in (key.decode() if isinstance(key, bytes) else key)
    assert 0 < await redis.ttl(key) <= 60


@pytest.mark.redis
async def test_concurrent_consumers_get_it_once(redis: Redis) -> None:
    store = OneTimeTokenStore(redis, "t:")
    token = await store.issue("oauth", {"a": 1}, 60)
    results = await asyncio.gather(*(store.consume("oauth", token) for _ in range(10)))
    assert sum(r is not None for r in results) == 1
