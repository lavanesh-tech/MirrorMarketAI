"""Idempotent consumer with retries and a dead-letter topic.

For each message:
1. Parse the envelope. Unparseable messages can never succeed, so they go
   straight to the dead-letter topic.
2. In ONE database transaction: insert (consumer, event_id) into
   `processed_events`, run the handler, commit. If the row already exists the
   event was handled before (a redelivery) and is skipped. Because the marker and
   the side effects commit together, a crash can never leave "done but unmarked".
3. On handler failure: roll back and retry with backoff. After the last attempt
   the original message is copied to `<topic>.dlq` with the error in headers.
4. Only then commit the Kafka offset. A poison message therefore never blocks
   its partition, and nothing is skipped silently.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import SessionFactory
from app.events.broker import Consumer, Message, Producer
from app.events.envelope import DLQ_SUFFIX, Envelope
from app.events.relay import backoff_seconds
from app.models.events import ProcessedEvent
from app.telemetry import metrics

logger = logging.getLogger(__name__)

Handler = Callable[[AsyncSession, Envelope], Awaitable[None]]


@dataclass(slots=True)
class ConsumerStats:
    handled: int = 0
    duplicates: int = 0
    dead_lettered: int = 0
    retries: int = 0


class EventConsumer:
    def __init__(
        self,
        name: str,
        consumer: Consumer,
        dead_letters: Producer,
        handler: Handler,
        sessions: SessionFactory,
        settings: Settings,
    ) -> None:
        self.name = name
        self._consumer = consumer
        self._dead_letters = dead_letters
        self._handler = handler
        self._sessions = sessions
        self._settings = settings
        self.stats = ConsumerStats()

    async def run_once(self, timeout_seconds: float = 1.0) -> int:
        """Poll once and fully process what arrived. Returns the number of messages."""
        messages = await self._consumer.poll(
            timeout_seconds, self._settings.kafka_consumer_batch_size
        )
        for index, message in enumerate(messages):
            try:
                await self._process(message)
                await self._consumer.commit(message)
            except BaseException:
                # Fetched but unfinished messages must come back on the next poll.
                # Reversed, so each partition ends at its earliest unfinished offset.
                for unfinished in reversed(messages[index:]):
                    await self._consumer.rewind(unfinished)
                raise
        return len(messages)

    async def _process(self, message: Message) -> None:
        try:
            envelope = Envelope.model_validate_json(message.value)
        except ValidationError as exc:
            await self._dead_letter(message, f"invalid_envelope: {exc.error_count()} error(s)", 0)
            return

        attempts = self._settings.kafka_consumer_max_attempts
        for attempt in range(1, attempts + 1):
            try:
                await self._handle(envelope)
            except Exception as exc:
                if attempt == attempts:
                    logger.exception("handler failed; dead-lettering", extra=self._extra(envelope))
                    await self._dead_letter(message, f"{type(exc).__name__}: {exc}", attempt)
                    return
                self.stats.retries += 1
                metrics.EVENTS_CONSUMED.labels(consumer=self.name, result="retry").inc()
                await asyncio.sleep(
                    backoff_seconds(
                        attempt,
                        self._settings.kafka_consumer_backoff_seconds,
                        self._settings.kafka_consumer_backoff_seconds * 8,
                    )
                )
            else:
                return

    async def _handle(self, envelope: Envelope) -> None:
        async with self._sessions() as session:
            try:
                claimed = await session.scalar(
                    insert(ProcessedEvent)
                    .values(consumer=self.name, event_id=envelope.id)
                    .on_conflict_do_nothing()
                    .returning(ProcessedEvent.event_id)
                )
                if claimed is None:
                    self.stats.duplicates += 1
                    metrics.EVENTS_CONSUMED.labels(consumer=self.name, result="duplicate").inc()
                    await session.rollback()
                    return
                await self._handler(session, envelope)
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
        self.stats.handled += 1
        metrics.EVENTS_CONSUMED.labels(consumer=self.name, result="handled").inc()

    async def _dead_letter(self, message: Message, error: str, attempts: int) -> None:
        # If this send fails the exception propagates, the offset is not committed,
        # and the message is delivered again later: never dropped.
        await self._dead_letters.send(
            message.topic + DLQ_SUFFIX,
            message.key,
            message.value,
            {
                **message.headers,
                "dlq_consumer": self.name,
                "dlq_error": error[:500],
                "dlq_attempts": str(attempts),
                "dlq_source": f"{message.topic}:{message.partition}:{message.offset}",
            },
        )
        self.stats.dead_lettered += 1
        metrics.EVENTS_CONSUMED.labels(consumer=self.name, result="dead_letter").inc()

    def _extra(self, envelope: Envelope) -> dict[str, str]:
        return {"consumer": self.name, "event_id": str(envelope.id), "event_type": envelope.type}
