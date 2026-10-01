"""Outbox relay: move committed events from PostgreSQL to the broker.

Why an outbox: "save the comment" and "publish the event" cannot be one atomic
step across two systems. Writing the event to a table in the same transaction
and publishing it afterwards guarantees no lost events (at-least-once); a crash
between publish and "mark published" only causes a duplicate, which consumers
ignore by event id.

Safe to run several relays: rows are claimed with FOR UPDATE SKIP LOCKED.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.core.config import Settings
from app.core.database import SessionFactory
from app.events.broker import Producer
from app.models.events import OutboxEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RelayResult:
    published: int
    failed: int  # sends that raised (will be retried, or were given up on)


def backoff_seconds(attempts: int, base: float, cap: float) -> float:
    """Exponential: base, 2*base, 4*base ... capped."""
    return float(min(cap, base * (2 ** max(0, attempts - 1))))


class OutboxRelay:
    def __init__(self, sessions: SessionFactory, producer: Producer, settings: Settings) -> None:
        self._sessions = sessions
        self._producer = producer
        self._settings = settings

    async def publish_batch(self) -> RelayResult:
        """Publish due events in insert order. Stops at the first failure so events
        that share a key are never published out of order."""
        settings = self._settings
        published = failed = 0
        async with self._sessions() as session:
            now = datetime.now(UTC)
            events = list(
                await session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.published_at.is_(None),
                        OutboxEvent.failed_at.is_(None),
                        OutboxEvent.next_attempt_at <= now,
                    )
                    .order_by(OutboxEvent.sequence)
                    .limit(settings.outbox_batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            for event in events:
                try:
                    await self._producer.send(
                        event.topic,
                        event.key.encode(),
                        json.dumps(event.payload, separators=(",", ":")).encode(),
                        {"event_id": str(event.id), "event_type": event.event_type},
                    )
                except Exception as exc:
                    failed += 1
                    event.attempts += 1
                    event.last_error = f"{type(exc).__name__}: {exc}"[:500]
                    if event.attempts >= settings.outbox_max_attempts:
                        event.failed_at = now
                        logger.error("outbox event given up", extra={"event_id": str(event.id)})
                    else:
                        event.next_attempt_at = now + timedelta(
                            seconds=backoff_seconds(
                                event.attempts,
                                settings.outbox_backoff_seconds,
                                settings.outbox_backoff_max_seconds,
                            )
                        )
                    break
                event.published_at = datetime.now(UTC)
                published += 1
            await session.commit()
        return RelayResult(published, failed)

    async def purge(self) -> int:
        """Delete events published longer ago than the retention window."""
        cutoff = datetime.now(UTC) - timedelta(hours=self._settings.outbox_retention_hours)
        async with self._sessions() as session:
            result = await session.execute(
                delete(OutboxEvent)
                .where(OutboxEvent.published_at < cutoff)
                .returning(OutboxEvent.id)
            )
            removed = len(result.all())
            await session.commit()
        return removed
