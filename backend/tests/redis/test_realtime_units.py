from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from redis.asyncio import Redis

from app.realtime.bus import EventBus, envelope
from app.realtime.hub import Connection, Hub
from app.realtime.presence import Presence

WS = uuid.uuid4()


def conn(
    workspace_id: uuid.UUID = WS, user_id: uuid.UUID | None = None, size: int = 10
) -> Connection:
    return Connection(workspace_id, user_id or uuid.uuid4(), asyncio.Queue(maxsize=size))


def test_hub_rooms_counts_and_overflow() -> None:
    hub = Hub()
    user = uuid.uuid4()
    a, b, other = conn(user_id=user), conn(user_id=user, size=1), conn(uuid.uuid4())
    for c in (a, b, other):
        hub.join(c)
    assert (hub.total, hub.count(WS, user), hub.users(WS)) == (3, 2, [str(user)])
    assert hub.broadcast(WS, "one") == 2
    assert hub.broadcast(WS, "two") == 1  # b's queue (size 1) is full
    assert (b.overflowed.is_set(), a.overflowed.is_set()) == (True, False)
    assert other.queue.empty()
    assert hub.broadcast(uuid.uuid4(), "nobody") == 0
    for c in (a, b, other, other):  # leaving twice is harmless
        hub.leave(c)
    assert (hub.total, hub.users(WS)) == (0, [])


def test_envelope_shape() -> None:
    actor = uuid.uuid4()
    event = json.loads(envelope("vote.changed", WS, {"product_id": actor}, actor))
    assert event["type"] == "vote.changed"
    assert (event["workspace_id"], event["actor_id"]) == (str(WS), str(actor))
    assert event["data"] == {"product_id": str(actor)}
    assert json.loads(envelope("x", WS, {}, None))["actor_id"] is None


async def test_bus_without_redis_delivers_locally() -> None:
    hub = Hub()
    c = conn()
    hub.join(c)
    bus = EventBus(hub, None, None, "t:")
    bus.start()  # no subscriber: a no-op
    await bus.publish(WS, "x", {"a": 1})
    assert json.loads(c.queue.get_nowait())["data"] == {"a": 1}
    await bus.stop()


async def test_bus_ignores_malformed_channels() -> None:
    hub = Hub()
    c = conn()
    hub.join(c)
    bus = EventBus(hub, None, None, "t:")
    bus._deliver(b"t:rt:not-a-uuid", b"{}")
    bus._deliver(f"t:rt:{WS}", "{}")
    assert c.queue.qsize() == 1


@pytest.mark.redis
async def test_bus_falls_back_to_local_when_publish_fails(redis: Redis, dead_redis: Redis) -> None:
    hub = Hub()
    c = conn()
    hub.join(c)
    bus = EventBus(hub, dead_redis, None, "t:")
    bus.subscribed.set()  # pretend the subscriber was healthy a moment ago
    await bus.publish(WS, "x", {})
    assert c.queue.qsize() == 1


@pytest.mark.redis
async def test_bus_subscriber_reconnects(redis_url: str) -> None:
    hub = Hub()
    c = conn()
    hub.join(c)
    publisher = Redis.from_url(redis_url)
    subscriber = Redis.from_url(redis_url, socket_timeout=None)
    bus = EventBus(hub, publisher, subscriber, "t:")
    bus.start()
    bus.start()  # idempotent
    try:
        async with asyncio.timeout(3):
            await bus.subscribed.wait()
        await publisher.client_kill_filter(_type="pubsub")
        async with asyncio.timeout(5):
            while bus.subscribed.is_set():  # noqa: ASYNC110 - polling an Event being cleared
                await asyncio.sleep(0.02)
            await bus.subscribed.wait()
            await bus.publish(WS, "after", {})
            assert json.loads(await c.queue.get())["type"] == "after"
    finally:
        await bus.stop()
        await subscriber.aclose()
        await publisher.aclose()


@pytest.mark.redis
async def test_presence_with_redis_and_fallbacks(redis: Redis, dead_redis: Redis) -> None:
    hub = Hub()
    user = uuid.uuid4()
    a, b = conn(user_id=user), conn(user_id=user)
    presence = Presence(hub, redis, "t:", 30)
    await presence.touch(a)
    await presence.touch(b)
    assert await presence.users(WS) == [str(user)]
    await presence.remove(a)
    assert await presence.users(WS) == [str(user)]  # second tab still there
    await presence.remove(b)
    assert await presence.users(WS) == []

    hub.join(a)
    for degraded in (Presence(hub, None, "t:", 30), Presence(hub, dead_redis, "t:", 30)):
        await degraded.touch(a)
        await degraded.remove(a)
        assert await degraded.users(WS) == [str(user)]  # local connections
