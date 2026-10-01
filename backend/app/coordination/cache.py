"""Read-through JSON cache with version-based invalidation.

Writers never delete cache keys. They INCR a per-entity version number, and
readers build keys that include the current version, so after a write every
reader misses and reloads; stale entries just expire by TTL. This avoids SCAN or
DEL fan-out, and a slow reader that stores an old result after a write stores it
under the old version, where nobody reads it any more.

Any Redis error degrades to calling the loader: the cache is an optimisation,
never a source of truth.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)


class JsonCache:
    def __init__(self, redis: Redis | None, prefix: str, ttl_seconds: int) -> None:
        self._redis = redis
        self._prefix = prefix
        self.ttl_seconds = ttl_seconds

    def _version_key(self, namespace: str, entity: str) -> str:
        return f"{self._prefix}ver:{namespace}:{entity}"

    async def _version(self, namespace: str, entity: str) -> int:
        assert self._redis is not None  # noqa: S101 - callers check first
        raw = await self._redis.get(self._version_key(namespace, entity))
        return int(raw) if raw is not None else 0

    async def invalidate(self, namespace: str, entity: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.incr(self._version_key(namespace, entity))
        except RedisError:
            # Cached reads may now be stale for up to the TTL; that is the documented bound.
            logger.warning("cache invalidation failed", extra={"cache_namespace": namespace})

    async def get_or_load(
        self,
        namespace: str,
        entity: str,
        params: str,
        model: type[M],
        loader: Callable[[], Awaitable[M]],
    ) -> tuple[M, bool]:
        """(value, hit). `params` distinguishes query variants of the same entity."""
        if self._redis is None:
            return await loader(), False
        try:
            version = await self._version(namespace, entity)
            digest = hashlib.sha256(params.encode()).hexdigest()[:24]
            key = f"{self._prefix}cache:{namespace}:{entity}:v{version}:{digest}"
            raw = await self._redis.get(key)
        except RedisError:
            logger.warning("cache unavailable; loading", extra={"cache_namespace": namespace})
            return await loader(), False
        if raw is not None:
            try:
                return model.model_validate_json(raw), True
            except ValidationError:
                logger.warning("cache entry unreadable", extra={"cache_namespace": namespace})
        value = await loader()
        try:
            await self._redis.set(key, value.model_dump_json(), ex=self.ttl_seconds)
        except RedisError:
            logger.warning("cache write failed", extra={"cache_namespace": namespace})
        return value, False
