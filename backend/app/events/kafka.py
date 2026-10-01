"""Kafka adapters (aiokafka) for the broker ports.

Producer: `acks=all` + idempotence, so a retried send cannot duplicate or reorder
within a partition. Consumer: auto-commit is OFF; offsets are committed only
after the handler's database transaction committed (at-least-once delivery).
"""

from __future__ import annotations

import contextlib
import logging

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from app.core.config import Settings
from app.events.broker import Message

logger = logging.getLogger(__name__)


async def ensure_topics(settings: Settings, topics: list[str]) -> None:
    """Create topics explicitly (never rely on broker auto-creation defaults)."""
    admin = AIOKafkaAdminClient(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        client_id=f"{settings.kafka_client_id}-admin",
    )
    await admin.start()
    try:
        existing = set(await admin.list_topics())
        missing = [
            NewTopic(name=t, num_partitions=settings.kafka_topic_partitions, replication_factor=1)
            for t in topics
            if t not in existing
        ]
        if missing:
            with contextlib.suppress(TopicAlreadyExistsError):  # another worker won the race
                await admin.create_topics(missing)
            logger.info("kafka topics ensured", extra={"topics": [t.name for t in missing]})
    finally:
        await admin.close()


class KafkaProducer:
    def __init__(self, settings: Settings) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            client_id=settings.kafka_client_id,
            acks="all",
            enable_idempotence=True,
            request_timeout_ms=int(settings.kafka_request_timeout_seconds * 1000),
        )

    async def start(self) -> None:
        await self._producer.start()

    async def stop(self) -> None:
        await self._producer.stop()

    async def send(
        self, topic: str, key: bytes | None, value: bytes, headers: dict[str, str]
    ) -> None:
        await self._producer.send_and_wait(
            topic, value=value, key=key, headers=[(k, v.encode()) for k, v in headers.items()]
        )


class KafkaConsumer:
    def __init__(self, settings: Settings, group: str, *topics: str) -> None:
        self._consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            client_id=settings.kafka_client_id,
            group_id=group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )

    async def start(self) -> None:
        await self._consumer.start()

    async def stop(self) -> None:
        await self._consumer.stop()

    async def poll(self, timeout_seconds: float, max_messages: int) -> list[Message]:
        batches = await self._consumer.getmany(
            timeout_ms=int(timeout_seconds * 1000), max_records=max_messages
        )
        return [
            Message(
                topic=record.topic,
                key=record.key,
                value=record.value or b"",
                headers={k: v.decode(errors="replace") for k, v in record.headers},
                partition=record.partition,
                offset=record.offset,
            )
            for records in batches.values()
            for record in records
        ]

    async def commit(self, message: Message) -> None:
        # Kafka stores the NEXT offset to read, hence +1.
        await self._consumer.commit(
            {TopicPartition(message.topic, message.partition): message.offset + 1}
        )

    async def rewind(self, message: Message) -> None:
        self._consumer.seek(TopicPartition(message.topic, message.partition), message.offset)
