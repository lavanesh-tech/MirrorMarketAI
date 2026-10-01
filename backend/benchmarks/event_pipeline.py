"""Throughput of the event pipeline: outbox -> relay -> broker -> idempotent consumer.

Run against the Compose stack (stop the event-worker first so it does not compete):
  docker compose stop event-worker
  DATABASE_URL=postgresql+asyncpg://mirrormarket:mirrormarket@localhost:5433/mirrormarket \\
  KAFKA_BOOTSTRAP_SERVERS=localhost:9094 uv run python -m benchmarks.event_pipeline

Without KAFKA_BOOTSTRAP_SERVERS the in-memory broker is used, which measures only
the PostgreSQL side (claiming outbox rows, inbox insert, projection insert).

Seeds a throwaway workspace and N synthetic `vote.changed` outbox events on a
benchmark-only topic, times the relay and then the consumer, checks that a second
delivery of every event is ignored, and deletes everything it created.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import time
import uuid
from pathlib import Path

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.events.broker import Consumer, InMemoryBroker, Producer
from app.events.consumer import EventConsumer
from app.events.envelope import DLQ_SUFFIX, new_event
from app.events.handlers import project_activity
from app.events.kafka import KafkaConsumer, KafkaProducer, ensure_topics
from app.events.relay import OutboxRelay
from app.models.events import OutboxEvent, ProcessedEvent, WorkspaceActivity
from benchmarks.citations import _commit

EVENTS = 2000
DEADLINE_SECONDS = 120


Sessions = async_sessionmaker[AsyncSession]


async def _seed(
    sessions: Sessions,
    run_id: str,
    topic: str,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    ws_id: uuid.UUID,
) -> None:
    async with sessions() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, password_hash, display_name)"
                " VALUES (:u, :e, 'x', 'bench')"
            ),
            {"u": user_id, "e": f"bench-{run_id}@example.com"},
        )
        await session.execute(
            text("INSERT INTO organizations (id, name, created_by_id) VALUES (:o, 'bench', :u)"),
            {"o": org_id, "u": user_id},
        )
        await session.execute(
            text(
                "INSERT INTO comparison_workspaces (id, organization_id, created_by_id, name)"
                " VALUES (:w, :o, :u, 'bench')"
            ),
            {"w": ws_id, "o": org_id, "u": user_id},
        )
        session.add_all(
            new_event(
                "vote.changed", {"value": 1}, workspace_id=ws_id, actor_id=user_id, topic=topic
            )
            for _ in range(EVENTS)
        )
        await session.commit()


async def _cleanup(
    sessions: Sessions,
    topic: str,
    name: str,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    ws_id: uuid.UUID,
) -> None:
    async with sessions() as session:
        await session.execute(delete(OutboxEvent).where(OutboxEvent.topic == topic))
        await session.execute(delete(ProcessedEvent).where(ProcessedEvent.consumer == name))
        await session.execute(text("DELETE FROM comparison_workspaces WHERE id = :w"), {"w": ws_id})
        await session.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": org_id})
        await session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        await session.commit()


async def run(database_url: str, bootstrap: str | None) -> dict[str, object]:  # noqa: PLR0915 - one linear benchmark script
    run_id = uuid.uuid4().hex[:8]
    topic, name = f"bench.events.{run_id}", f"bench-{run_id}"
    settings = Settings(
        _env_file=None,
        kafka_enabled=bootstrap is not None,
        kafka_bootstrap_servers=bootstrap or "unused:9092",
        kafka_topic_partitions=1,
    )
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id, org_id, ws_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    kafka_producer: KafkaProducer | None = None
    kafka_consumer: KafkaConsumer | None = None
    producer: Producer
    consumer: Consumer
    if bootstrap:
        await ensure_topics(settings, [topic, topic + DLQ_SUFFIX])
        kafka_producer = KafkaProducer(settings)
        await kafka_producer.start()
        kafka_consumer = KafkaConsumer(settings, name, topic)
        await kafka_consumer.start()
        producer, consumer = kafka_producer, kafka_consumer
    else:
        memory = InMemoryBroker()
        producer, consumer = memory.producer(), memory.consumer(name, topic)

    await _seed(sessions, run_id, topic, user_id, org_id, ws_id)

    try:
        relay = OutboxRelay(sessions, producer, settings)
        started = time.perf_counter()
        published = 0
        while published < EVENTS:
            published += (await relay.publish_batch()).published
        relay_seconds = time.perf_counter() - started

        event_consumer = EventConsumer(
            name, consumer, producer, project_activity, sessions, settings
        )
        started = time.perf_counter()
        async with asyncio.timeout(DEADLINE_SECONDS):
            while event_consumer.stats.handled < EVENTS:
                await event_consumer.run_once(timeout_seconds=1.0)
        consume_seconds = time.perf_counter() - started

        # Deliver every event a second time: the inbox must reject all of them.
        async with sessions() as session:
            await session.execute(
                update(OutboxEvent).where(OutboxEvent.topic == topic).values(published_at=None)
            )
            await session.commit()
        republished = 0
        while republished < EVENTS:
            republished += (await relay.publish_batch()).published
        async with asyncio.timeout(DEADLINE_SECONDS):
            while event_consumer.stats.duplicates < EVENTS:
                await event_consumer.run_once(timeout_seconds=1.0)
        async with sessions() as session:
            rows = await session.scalar(
                select(func.count())
                .select_from(WorkspaceActivity)
                .where(WorkspaceActivity.workspace_id == ws_id)
            )
    finally:
        if kafka_consumer is not None:
            await kafka_consumer.stop()
        if kafka_producer is not None:
            await kafka_producer.stop()
        await _cleanup(sessions, topic, name, user_id, org_id, ws_id)
        await engine.dispose()

    return {
        "benchmark": "event_pipeline",
        "broker": "kafka" if bootstrap else "in-memory (PostgreSQL side only)",
        "dataset": f"synthetic: {EVENTS} vote.changed events, one workspace, one partition",
        "relay": {
            "seconds": round(relay_seconds, 3),
            "events_per_second": round(EVENTS / relay_seconds),
            "batch_size": settings.outbox_batch_size,
        },
        "consumer": {
            "seconds": round(consume_seconds, 3),
            "events_per_second": round(EVENTS / consume_seconds),
        },
        "redelivery": {
            "events_delivered_twice": EVENTS,
            "duplicates_rejected": event_consumer.stats.duplicates,
            "activity_rows": rows,
        },
    }


def main() -> None:
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
    result = asyncio.run(run(os.environ["DATABASE_URL"], bootstrap)) | {
        "commit": _commit(),
        "python": platform.python_version(),
        "machine": platform.machine(),
    }
    out = Path(__file__).parent / "results" / "event_pipeline.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
