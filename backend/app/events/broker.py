"""Broker ports (what the relay and consumers need) and an in-memory implementation.

The relay and consumer code depends only on these two small protocols. Production
uses the Kafka adapters in `app.events.kafka`; unit and database tests use
`InMemoryBroker`, which keeps the semantics that matter: an append-only log per
topic, and a committed offset per consumer group that only moves on `commit`, so
anything not committed is delivered again.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Message:
    topic: str
    key: bytes | None
    value: bytes
    headers: dict[str, str] = field(default_factory=dict)
    partition: int = 0
    offset: int = 0


class Producer(Protocol):
    async def send(
        self, topic: str, key: bytes | None, value: bytes, headers: dict[str, str]
    ) -> None:
        """Returns once the broker has acknowledged the message; raises otherwise."""


class Consumer(Protocol):
    async def poll(self, timeout_seconds: float, max_messages: int) -> list[Message]: ...

    async def commit(self, message: Message) -> None:
        """Mark everything up to and including `message` (in its partition) as done."""

    async def rewind(self, message: Message) -> None:
        """Make the next poll return `message` again (it was fetched but not finished)."""


class InMemoryBroker:
    def __init__(self) -> None:
        self.topics: dict[str, list[Message]] = {}
        self.committed: dict[tuple[str, str], int] = {}  # (group, topic) -> next offset
        self.fail_next_sends = 0  # test hook: make the next N sends raise

    def log(self, topic: str) -> list[Message]:
        return self.topics.get(topic, [])

    def producer(self) -> InMemoryProducer:
        return InMemoryProducer(self)

    def consumer(self, group: str, *topics: str) -> InMemoryConsumer:
        return InMemoryConsumer(self, group, topics)


class InMemoryProducer:
    def __init__(self, broker: InMemoryBroker) -> None:
        self._broker = broker

    async def send(
        self, topic: str, key: bytes | None, value: bytes, headers: dict[str, str]
    ) -> None:
        await asyncio.sleep(0)
        if self._broker.fail_next_sends > 0:
            self._broker.fail_next_sends -= 1
            raise ConnectionError("broker unavailable")
        log = self._broker.topics.setdefault(topic, [])
        log.append(Message(topic, key, value, dict(headers), 0, len(log)))


class InMemoryConsumer:
    """Starts from the group's committed offset, like a restarted Kafka consumer."""

    def __init__(self, broker: InMemoryBroker, group: str, topics: tuple[str, ...]) -> None:
        self._broker = broker
        self._group = group
        self._position = {t: broker.committed.get((group, t), 0) for t in topics}

    async def poll(self, timeout_seconds: float, max_messages: int) -> list[Message]:
        await asyncio.sleep(0)
        batch: list[Message] = []
        for topic, position in self._position.items():
            take = self._broker.log(topic)[position : position + max_messages - len(batch)]
            batch.extend(take)
            self._position[topic] = position + len(take)
        return batch

    async def commit(self, message: Message) -> None:
        self._broker.committed[self._group, message.topic] = message.offset + 1

    async def rewind(self, message: Message) -> None:
        self._position[message.topic] = min(self._position[message.topic], message.offset)
