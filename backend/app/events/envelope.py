"""The event contract shared by producers and consumers.

Every event is one JSON object (the "envelope"). `id` is what consumers
deduplicate on; `version` lets the payload evolve; `key` decides the Kafka
partition, so all events for one workspace (or one product) stay in order.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.events import OutboxEvent

TOPIC_EVENTS = "mirrormarket.events.v1"
DLQ_SUFFIX = ".dlq"
SCHEMA_VERSION = 1

COMMENT_CREATED = "comment.created"
COMMENT_DELETED = "comment.deleted"
VOTE_CHANGED = "vote.changed"
AGENT_RUN_COMPLETED = "agent_run.completed"
PRICES_RECORDED = "prices.recorded"


class Envelope(BaseModel):
    model_config = ConfigDict(extra="ignore")  # newer producers may add fields

    id: uuid.UUID
    type: str
    version: int
    occurred_at: datetime
    workspace_id: uuid.UUID | None = None
    actor_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    data: dict[str, Any] = {}


def new_event(
    event_type: str,
    data: dict[str, Any],
    *,
    workspace_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    topic: str = TOPIC_EVENTS,
) -> OutboxEvent:
    """An outbox row to `session.add()` in the same transaction as the change it describes."""
    event_id = uuid.uuid4()
    key = workspace_id or product_id or event_id
    envelope = Envelope(
        id=event_id,
        type=event_type,
        version=SCHEMA_VERSION,
        occurred_at=datetime.now(UTC),
        workspace_id=workspace_id,
        actor_id=actor_id,
        product_id=product_id,
        data=data,
    )
    return OutboxEvent(
        id=event_id,
        topic=topic,
        key=str(key),
        event_type=event_type,
        payload=envelope.model_dump(mode="json"),
    )
