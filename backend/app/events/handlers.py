"""Consumer handlers. Each runs inside the consumer's transaction and must not commit."""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.envelope import (
    AGENT_RUN_COMPLETED,
    COMMENT_CREATED,
    COMMENT_DELETED,
    VOTE_CHANGED,
    Envelope,
)
from app.models.events import WorkspaceActivity

ACTIVITY_CONSUMER = "activity-projector"
SUMMARY_MAX = 300
_VOTE_WORDS = {1: "voted up", -1: "voted down", 0: "removed their vote"}


def summarize(envelope: Envelope) -> str | None:
    """One line for the activity feed, or None for events the feed does not show."""
    data = envelope.data
    if envelope.type == COMMENT_CREATED:
        verb = "replied" if data.get("parent_id") else "commented"
        return f"{verb}: {data.get('excerpt', '')}"
    if envelope.type == COMMENT_DELETED:
        return "deleted a comment"
    if envelope.type == VOTE_CHANGED:
        return _VOTE_WORDS.get(int(data.get("value", 0)), "voted")
    if envelope.type == AGENT_RUN_COMPLETED:
        return f"ran {data.get('agent', 'an agent')} ({str(data.get('status', '')).lower()})"
    return None


async def project_activity(session: AsyncSession, envelope: Envelope) -> None:
    summary = summarize(envelope)
    if summary is None or envelope.workspace_id is None:
        return
    # ON CONFLICT is a second line of defence behind processed_events.
    await session.execute(
        insert(WorkspaceActivity)
        .values(
            workspace_id=envelope.workspace_id,
            event_id=envelope.id,
            event_type=envelope.type,
            actor_id=envelope.actor_id,
            product_id=envelope.product_id,
            summary=summary[:SUMMARY_MAX],
            data=envelope.data,
            occurred_at=envelope.occurred_at,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
