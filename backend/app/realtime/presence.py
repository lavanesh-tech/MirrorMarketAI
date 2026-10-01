"""Who is online in a workspace.

With Redis: a sorted set per workspace, member `user_id:connection_id`, score =
last heartbeat (Redis-independent wall clock seconds). Entries older than the TTL
are ignored and trimmed, so a crashed replica's users disappear on their own.
Without Redis (or when it fails): this replica's own connections.
"""

from __future__ import annotations

import logging
import time
import uuid

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.realtime.hub import Connection, Hub

logger = logging.getLogger(__name__)


class Presence:
    def __init__(self, hub: Hub, redis: Redis | None, prefix: str, ttl_seconds: int) -> None:
        self.hub = hub
        self._redis = redis
        self._prefix = prefix
        self.ttl_seconds = ttl_seconds

    def _key(self, workspace_id: uuid.UUID) -> str:
        return f"{self._prefix}presence:{workspace_id}"

    @staticmethod
    def _member(connection: Connection) -> str:
        return f"{connection.user_id}:{connection.id}"

    async def touch(self, connection: Connection) -> None:
        if self._redis is None:
            return
        key = self._key(connection.workspace_id)
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                pipe.zadd(key, {self._member(connection): time.time()})
                pipe.expire(key, self.ttl_seconds * 2)
                await pipe.execute()
        except RedisError:
            logger.warning("presence heartbeat failed")

    async def remove(self, connection: Connection) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.zrem(self._key(connection.workspace_id), self._member(connection))
        except RedisError:
            logger.warning("presence removal failed; the entry expires by TTL")

    async def users(self, workspace_id: uuid.UUID) -> list[str]:
        """Sorted distinct user ids currently online."""
        if self._redis is None:
            return self.hub.users(workspace_id)
        key = self._key(workspace_id)
        cutoff = time.time() - self.ttl_seconds
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                pipe.zremrangebyscore(key, "-inf", cutoff)
                pipe.zrange(key, 0, -1)
                _, members = await pipe.execute()
        except RedisError:
            logger.warning("presence lookup failed; using local connections")
            return self.hub.users(workspace_id)
        return sorted(
            {(m.decode() if isinstance(m, bytes) else m).split(":", 1)[0] for m in members}
        )
