"""Workspace event bus: publish once, deliver on every API replica.

A WebSocket lives on one replica, but the HTTP request that creates a comment may
hit another. So events go through Redis pub/sub: every replica subscribes to
`<prefix>rt:*` and forwards messages to its local connections. Without Redis (or
when PUBLISH fails) the event is delivered to this replica's connections
directly, which is correct for a single process.

Pub/sub is at-most-once and has no history. That is fine here because events are
only hints: PostgreSQL holds the truth, and a client that reconnects refetches.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.realtime.hub import Hub

logger = logging.getLogger(__name__)

RECONNECT_SECONDS = 1.0


def envelope(
    event_type: str, workspace_id: uuid.UUID, data: dict[str, Any], actor_id: uuid.UUID | None
) -> str:
    return json.dumps(
        {
            "type": event_type,
            "workspace_id": str(workspace_id),
            "actor_id": str(actor_id) if actor_id else None,
            "at": datetime.now(UTC).isoformat(),
            "data": data,
        },
        default=str,
    )


class EventBus:
    def __init__(
        self, hub: Hub, redis: Redis | None, subscriber: Redis | None, prefix: str
    ) -> None:
        self.hub = hub
        self._redis = redis
        self._subscriber = subscriber
        self._channel_prefix = f"{prefix}rt:"
        self._task: asyncio.Task[None] | None = None
        self.subscribed = asyncio.Event()

    async def publish(
        self,
        workspace_id: uuid.UUID,
        event_type: str,
        data: dict[str, Any],
        actor_id: uuid.UUID | None = None,
    ) -> None:
        """Never raises: a failed notification must not fail the request that caused it."""
        text = envelope(event_type, workspace_id, data, actor_id)
        if self._redis is not None and self.subscribed.is_set():
            try:
                await self._redis.publish(f"{self._channel_prefix}{workspace_id}", text)
            except RedisError:
                logger.warning("event publish failed; delivering locally only")
            else:
                return
        self.hub.broadcast(workspace_id, text)

    def start(self) -> None:
        if self._subscriber is not None and self._task is None:
            self._task = asyncio.create_task(self._run(), name="realtime-subscriber")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        assert self._subscriber is not None  # noqa: S101 - start() checks
        while True:
            pubsub = self._subscriber.pubsub()
            try:
                await pubsub.psubscribe(f"{self._channel_prefix}*")
                self.subscribed.set()
                async for message in pubsub.listen():
                    if message["type"] == "pmessage":
                        self._deliver(message["channel"], message["data"])
            except RedisError:
                logger.warning("realtime subscriber lost Redis; reconnecting")
            finally:
                self.subscribed.clear()
                with contextlib.suppress(RedisError):
                    await pubsub.aclose()  # type: ignore[no-untyped-call]
            await asyncio.sleep(RECONNECT_SECONDS)

    def _deliver(self, channel: bytes | str, data: bytes | str) -> None:
        name = channel.decode() if isinstance(channel, bytes) else channel
        try:
            workspace_id = uuid.UUID(name.removeprefix(self._channel_prefix))
        except ValueError:
            return
        self.hub.broadcast(workspace_id, data.decode() if isinstance(data, bytes) else data)
