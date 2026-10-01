"""Two API replicas sharing Redis: events and presence cross process boundaries."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from redis.asyncio import Redis

from tests.db.conftest import StackFactory, register_user, workspace

pytestmark = [pytest.mark.db, pytest.mark.api, pytest.mark.redis]


async def test_event_published_on_one_replica_reaches_sockets_on_another(
    stack: StackFactory, redis_url: str
) -> None:
    async with stack(redis_url=redis_url) as a, stack(redis_url=redis_url) as b:
        for replica in (a, b):
            async with asyncio.timeout(3):
                await replica.app.state.bus.subscribed.wait()
        owner = await register_user(a.http)
        ws_id = await workspace(a.http, owner)

        on_a = await a.connect(ws_id, owner)
        await on_a.until("presence.joined")
        on_b = await b.connect(ws_id, owner)  # same user, other replica: already online
        assert await on_a.silent()

        created = await b.http.post(
            f"/api/v1/workspaces/{ws_id}/comments", json={"body": "hi"}, headers=owner.headers
        )
        for sock in (on_a, on_b):
            event = await sock.until("comment.created")
            assert event["data"]["id"] == created.json()["id"]
        assert await on_a.silent()  # delivered exactly once per socket

        listed = await b.http.get(f"/api/v1/workspaces/{ws_id}/presence", headers=owner.headers)
        assert listed.json()["user_ids"] == [owner.id]
        await on_a.disconnect()
        assert await on_b.silent()  # still online on replica B: no presence.left
        await on_b.disconnect()
        after = await a.http.get(f"/api/v1/workspaces/{ws_id}/presence", headers=owner.headers)
        assert after.json()["user_ids"] == []


async def test_stale_presence_entries_expire(stack: StackFactory, redis_url: str) -> None:
    async with stack(redis_url=redis_url, presence_ttl_seconds=30) as s:
        owner = await register_user(s.http)
        ws_id = await workspace(s.http, owner)
        ghost = uuid.uuid4()
        async with Redis.from_url(redis_url) as client:  # a replica that crashed a minute ago
            await client.zadd(f"mm:presence:{ws_id}", {f"{ghost}:dead": time.time() - 60})
            sock = await s.connect(ws_id, owner)
            users = await s.app.state.presence.users(uuid.UUID(ws_id))
            assert users == [owner.id]
            assert await client.zcard(f"mm:presence:{ws_id}") == 1
            assert 0 < await client.ttl(f"mm:presence:{ws_id}") <= 60
        await sock.disconnect()
