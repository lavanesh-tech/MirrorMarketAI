"""Event plumbing tables: transactional outbox, consumer inbox, activity projection.

- `outbox_events`: written in the SAME transaction as the business change, so an
  event exists if and only if the change committed. A relay publishes them to Kafka.
- `processed_events`: one row per (consumer, event id). A consumer inserts it in
  the same transaction as its side effects, which makes redelivery harmless.
- `workspace_activity`: a read model built by a Kafka consumer (the activity feed).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class OutboxEvent(UUIDPrimaryKeyMixin, Base):
    """`id` is the event id that consumers deduplicate on."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index(
            "ix_outbox_events_pending",
            "next_attempt_at",
            "sequence",
            postgresql_where=text("published_at IS NULL AND failed_at IS NULL"),
        ),
    )

    # Monotonic insert order; the relay publishes in this order.
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    topic: Mapped[str] = mapped_column(String(120))
    key: Mapped[str] = mapped_column(String(64))  # Kafka partition key (ordering scope)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessedEvent(Base):
    __tablename__ = "processed_events"
    __table_args__ = (PrimaryKeyConstraint("consumer", "event_id"),)

    consumer: Mapped[str] = mapped_column(String(64))
    event_id: Mapped[uuid.UUID] = mapped_column()
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WorkspaceActivity(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "workspace_activity"
    __table_args__ = (Index("ix_workspace_activity_feed", "workspace_id", "occurred_at"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comparison_workspaces.id", ondelete="CASCADE")
    )
    event_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    event_type: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL")
    )
    summary: Mapped[str] = mapped_column(String(300))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
