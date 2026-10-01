"""The Kafka adapters and the whole event pipeline against a real broker."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import SessionFactory
from app.events.broker import Consumer, Message
from app.events.consumer import EventConsumer
from app.events.envelope import DLQ_SUFFIX
from app.events.handlers import project_activity
from app.events.kafka import KafkaConsumer, KafkaProducer, ensure_topics
from app.events.relay import OutboxRelay
from app.models.events import OutboxEvent
from tests.db.conftest import StackFactory, seed

pytestmark = [pytest.mark.kafka, pytest.mark.db]

DEADLINE_SECONDS = 60


async def collect(consumer: Consumer, count: int) -> list[Message]:
    """Poll until `count` messages arrived (joining a consumer group takes a few seconds)."""
    messages: list[Message] = []
    async with asyncio.timeout(DEADLINE_SECONDS):
        while len(messages) < count:
            messages.extend(await consumer.poll(1.0, 100))
    return messages


async def test_produce_consume_commit_and_rewind(kafka_settings: Settings) -> None:
    topic, group = f"test-{uuid.uuid4().hex}", f"group-{uuid.uuid4().hex}"
    await ensure_topics(kafka_settings, [topic])
    await ensure_topics(kafka_settings, [topic])  # already there: a no-op

    producer = KafkaProducer(kafka_settings)
    await producer.start()
    consumer = KafkaConsumer(kafka_settings, group, topic)
    await consumer.start()
    try:
        for i in range(3):  # same key -> same partition -> strict order
            await producer.send(topic, b"workspace-1", str(i).encode(), {"n": str(i)})
        first = await collect(consumer, 3)
        assert [m.value for m in first] == [b"0", b"1", b"2"]
        assert (first[0].key, first[0].headers) == (b"workspace-1", {"n": "0"})
        assert len({m.partition for m in first}) == 1
        assert [m.offset for m in first] == [0, 1, 2]

        await consumer.commit(first[0])
        await consumer.rewind(first[1])  # fetched but "not finished": must come back
        again = await collect(consumer, 2)
        assert [m.value for m in again] == [b"1", b"2"]
        await consumer.commit(again[1])
    finally:
        await consumer.stop()

    restarted = KafkaConsumer(kafka_settings, group, topic)
    await restarted.start()
    try:
        await producer.send(topic, b"workspace-1", b"3", {})
        # Everything up to offset 2 was committed, so only the new message arrives.
        assert [m.value for m in await collect(restarted, 1)] == [b"3"]
    finally:
        await restarted.stop()
        await producer.stop()


async def test_outbox_to_activity_feed_and_dead_letters_through_kafka(
    stack: StackFactory,
    db_session: AsyncSession,
    session_scope: SessionFactory,
    kafka_settings: Settings,
) -> None:
    topic = f"test-{uuid.uuid4().hex}"
    name = f"activity-{uuid.uuid4().hex}"
    await ensure_topics(kafka_settings, [topic, topic + DLQ_SUFFIX])

    producer = KafkaProducer(kafka_settings)
    await producer.start()
    kafka_consumer = KafkaConsumer(kafka_settings, name, topic)
    await kafka_consumer.start()
    dead_letters = KafkaConsumer(kafka_settings, f"{name}-dlq", topic + DLQ_SUFFIX)
    await dead_letters.start()
    try:
        async with stack() as s:
            owner, ws_id, pid = await seed(s.http)
            base = f"/api/v1/workspaces/{ws_id}"
            await s.http.post(f"{base}/comments", json={"body": "Hello"}, headers=owner.headers)
            await s.http.put(
                f"{base}/products/{pid}/vote", json={"value": 1}, headers=owner.headers
            )
            await db_session.execute(update(OutboxEvent).values(topic=topic))  # isolate the test

            relay = OutboxRelay(session_scope, producer, kafka_settings)
            result = await relay.publish_batch()
            assert (result.published, result.failed) == (2, 0)
            await producer.send(topic, ws_id.encode(), b"not an envelope", {})

            consumer = EventConsumer(
                name, kafka_consumer, producer, project_activity, session_scope, kafka_settings
            )
            async with asyncio.timeout(DEADLINE_SECONDS):
                while consumer.stats.handled + consumer.stats.dead_lettered < 3:
                    await consumer.run_once(timeout_seconds=1.0)
            assert (consumer.stats.handled, consumer.stats.dead_lettered) == (2, 1)

            feed = (await s.http.get(f"{base}/activity", headers=owner.headers)).json()
            assert sorted(i["summary"] for i in feed["items"]) == ["commented: Hello", "voted up"]

        (dead,) = await collect(dead_letters, 1)
        assert dead.value == b"not an envelope"
        assert dead.headers["dlq_consumer"] == name
        assert dead.headers["dlq_error"].startswith("invalid_envelope")
        assert dead.headers["dlq_source"].startswith(f"{topic}:")
    finally:
        await dead_letters.stop()
        await kafka_consumer.stop()
        await producer.stop()
