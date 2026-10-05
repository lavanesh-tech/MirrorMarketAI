"""Outbox -> relay -> broker -> idempotent consumer -> activity feed (in-memory broker)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import SessionFactory
from app.events.broker import InMemoryBroker, Message
from app.events.consumer import EventConsumer, Handler
from app.events.envelope import DLQ_SUFFIX, TOPIC_EVENTS, Envelope, new_event
from app.events.handlers import ACTIVITY_CONSUMER, project_activity
from app.events.relay import OutboxRelay
from app.models.events import OutboxEvent, ProcessedEvent, WorkspaceActivity
from app.models.identity import ComparisonWorkspace, Organization, User
from app.telemetry import metrics
from app.workers import event_worker
from tests.db.conftest import StackFactory, register_user, seed

pytestmark = [pytest.mark.db, pytest.mark.api]

DLQ = TOPIC_EVENTS + DLQ_SUFFIX


@pytest.fixture
def cfg(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(kafka_consumer_backoff_seconds=0, outbox_backoff_seconds=30)


def consumer_for(
    broker: InMemoryBroker,
    sessions: SessionFactory,
    settings: Settings,
    handler: Handler = project_activity,
) -> EventConsumer:
    return EventConsumer(
        ACTIVITY_CONSUMER,
        broker.consumer(ACTIVITY_CONSUMER, TOPIC_EVENTS),
        broker.producer(),
        handler,
        sessions,
        settings,
    )


async def pending(session: AsyncSession) -> list[OutboxEvent]:
    rows = await session.scalars(
        select(OutboxEvent).where(OutboxEvent.published_at.is_(None)).order_by(OutboxEvent.sequence)
    )
    return list(rows)


async def test_changes_write_outbox_events_in_the_same_transaction(
    stack: StackFactory, db_session: AsyncSession
) -> None:
    async with stack() as s:
        owner, ws_id, pid = await seed(s.http)
        base = f"/api/v1/workspaces/{ws_id}"
        comment = await s.http.post(
            f"{base}/comments", json={"body": "x" * 200, "product_id": pid}, headers=owner.headers
        )
        rejected = await s.http.post(
            f"{base}/comments",
            json={"body": "y", "parent_id": str(uuid.uuid4())},
            headers=owner.headers,
        )
        assert rejected.status_code == 404
        await s.http.put(f"{base}/products/{pid}/vote", json={"value": 1}, headers=owner.headers)
        await s.http.delete(f"{base}/comments/{comment.json()['id']}", headers=owner.headers)
        await s.http.delete(f"{base}/comments/{comment.json()['id']}", headers=owner.headers)
        await s.http.post(f"{base}/ask", json={"question": "Is it loud?"}, headers=owner.headers)
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        price = {"retailer": "A", "amount": "10.00", "currency": "USD", "observed_at": now}
        for _ in range(2):  # the second batch inserts nothing, so it emits nothing
            await s.http.post(
                f"/api/v1/products/{pid}/prices",
                json={"observations": [price]},
                headers=owner.headers,
            )

    events = await pending(db_session)
    assert [e.event_type for e in events] == [
        "comment.created",
        "vote.changed",
        "comment.deleted",
        "agent_run.completed",
        "prices.recorded",
    ]
    created = events[0]
    envelope = Envelope.model_validate(created.payload)
    assert (created.topic, created.key) == (TOPIC_EVENTS, ws_id)
    assert (envelope.id, envelope.version, str(envelope.actor_id)) == (created.id, 1, owner.id)
    assert (str(envelope.workspace_id), str(envelope.product_id)) == (ws_id, pid)
    assert envelope.data == {
        "comment_id": comment.json()["id"],
        "parent_id": None,
        "excerpt": "x" * 120,
    }
    assert events[4].key == pid  # no workspace: ordered per product


async def test_relay_publishes_in_order_once(
    db_session: AsyncSession, session_scope: SessionFactory, cfg: Settings
) -> None:
    key = uuid.uuid4()
    for i in range(3):
        db_session.add(new_event("test.event", {"i": i}, workspace_id=key))
        await db_session.flush()
    broker = InMemoryBroker()
    relay = OutboxRelay(session_scope, broker.producer(), cfg)

    result = await relay.publish_batch()
    assert (result.published, result.failed) == (3, 0)
    log = broker.log(TOPIC_EVENTS)
    assert [json.loads(m.value)["data"]["i"] for m in log] == [0, 1, 2]
    assert {m.key for m in log} == {str(key).encode()}
    assert log[0].headers["event_type"] == "test.event"
    assert uuid.UUID(log[0].headers["event_id"]) == uuid.UUID(json.loads(log[0].value)["id"])
    assert await pending(db_session) == []
    assert (await relay.publish_batch()).published == 0
    assert len(broker.log(TOPIC_EVENTS)) == 3


async def test_backlog_is_measured_for_alerting(
    db_session: AsyncSession, session_scope: SessionFactory, cfg: Settings
) -> None:
    """Pending events and the age of the oldest are what the backlog alert watches."""
    await db_session.execute(update(OutboxEvent).values(published_at=datetime.now(UTC)))
    relay = OutboxRelay(session_scope, InMemoryBroker().producer(), cfg)
    assert await relay.measure_backlog() == (0, 0.0)
    assert metrics.REGISTRY.get_sample_value("mm_outbox_pending_events") == 0

    for i in range(2):
        db_session.add(new_event("test.event", {"i": i}, workspace_id=uuid.uuid4()))
    await db_session.flush()
    await db_session.execute(
        update(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None))
        .values(created_at=datetime.now(UTC) - timedelta(seconds=90))
    )
    pending_count, age = await relay.measure_backlog()
    assert pending_count == 2
    assert 90 <= age < 120
    assert metrics.REGISTRY.get_sample_value("mm_outbox_pending_events") == 2
    oldest = metrics.REGISTRY.get_sample_value("mm_outbox_oldest_pending_seconds")
    assert oldest is not None
    assert 90 <= oldest < 120

    published = metrics.REGISTRY.get_sample_value(
        "mm_events_relayed_total", {"result": "published"}
    )
    await relay.publish_batch()
    assert await relay.measure_backlog() == (0, 0.0)
    assert (
        metrics.REGISTRY.get_sample_value("mm_events_relayed_total", {"result": "published"})
        == (published or 0) + 2
    )


async def test_relay_retries_with_backoff_and_keeps_order(
    db_session: AsyncSession, session_scope: SessionFactory, cfg: Settings
) -> None:
    for i in range(2):
        db_session.add(new_event("test.event", {"i": i}, workspace_id=uuid.uuid4()))
        await db_session.flush()
    broker = InMemoryBroker()
    broker.fail_next_sends = 1
    relay = OutboxRelay(session_scope, broker.producer(), cfg)

    first = await relay.publish_batch()
    assert (first.published, first.failed) == (0, 1)  # stopped at the failure
    head, tail = await pending(db_session)
    assert (head.attempts, tail.attempts) == (1, 0)
    assert head.last_error == "ConnectionError: broker unavailable"
    wait = (head.next_attempt_at - datetime.now(UTC)).total_seconds()
    assert 25 < wait <= 30

    # The head event is backing off, so the next one may go first (a different key).
    assert (await relay.publish_batch()).published == 1
    await db_session.execute(update(OutboxEvent).values(next_attempt_at=datetime.now(UTC)))
    assert (await relay.publish_batch()).published == 1
    assert [json.loads(m.value)["data"]["i"] for m in broker.log(TOPIC_EVENTS)] == [1, 0]


async def test_relay_gives_up_after_max_attempts_and_purges(
    db_session: AsyncSession, session_scope: SessionFactory, make_settings: Callable[..., Settings]
) -> None:
    settings = make_settings(outbox_max_attempts=2, outbox_backoff_seconds=0)
    db_session.add(new_event("test.poison", {}, workspace_id=uuid.uuid4()))
    await db_session.flush()
    db_session.add(new_event("test.fine", {}, workspace_id=uuid.uuid4()))
    await db_session.flush()
    broker = InMemoryBroker()
    broker.fail_next_sends = 2
    relay = OutboxRelay(session_scope, broker.producer(), settings)
    await relay.publish_batch()
    await relay.publish_batch()
    poison = (await pending(db_session))[0]
    assert (poison.attempts, poison.failed_at is not None) == (2, True)
    assert (await relay.publish_batch()).published == 1  # the queue is not blocked
    assert [m.headers["event_type"] for m in broker.log(TOPIC_EVENTS)] == ["test.fine"]

    assert await relay.purge() == 0  # published a moment ago: inside retention
    old = datetime.now(UTC) - timedelta(hours=settings.outbox_retention_hours + 1)
    await db_session.execute(
        update(OutboxEvent).where(OutboxEvent.published_at.is_not(None)).values(published_at=old)
    )
    assert await relay.purge() == 1
    remaining = await db_session.scalar(select(func.count()).select_from(OutboxEvent))
    assert remaining == 1  # the given-up event is kept for inspection


async def test_end_to_end_activity_feed(
    stack: StackFactory, db_session: AsyncSession, session_scope: SessionFactory, cfg: Settings
) -> None:
    broker = InMemoryBroker()
    async with stack() as s:
        owner, ws_id, pid = await seed(s.http)
        stranger = await register_user(s.http)
        base = f"/api/v1/workspaces/{ws_id}"
        top = await s.http.post(f"{base}/comments", json={"body": "Hello"}, headers=owner.headers)
        await s.http.post(
            f"{base}/comments",
            json={"body": "Reply", "parent_id": top.json()["id"]},
            headers=owner.headers,
        )
        await s.http.put(f"{base}/products/{pid}/vote", json={"value": -1}, headers=owner.headers)
        await s.http.post(f"{base}/ask", json={"question": "Is it loud?"}, headers=owner.headers)
        await s.http.delete(f"{base}/comments/{top.json()['id']}", headers=owner.headers)

        before = await s.http.get(f"{base}/activity", headers=owner.headers)
        assert before.json()["items"] == []  # nothing consumed yet: eventually consistent

        await OutboxRelay(session_scope, broker.producer(), cfg).publish_batch()
        consumer = consumer_for(broker, session_scope, cfg)
        assert await consumer.run_once() == 5
        assert (consumer.stats.handled, consumer.stats.duplicates) == (5, 0)

        feed = (await s.http.get(f"{base}/activity", headers=owner.headers)).json()
        assert feed["page"]["total"] == 5
        assert sorted(i["summary"] for i in feed["items"]) == [
            "commented: Hello",
            "deleted a comment",
            "ran ask (succeeded)",
            "replied: Reply",
            "voted down",
        ]
        times = [i["occurred_at"] for i in feed["items"]]
        assert times == sorted(times, reverse=True)
        vote = next(i for i in feed["items"] if i["event_type"] == "vote.changed")
        assert (vote["actor_id"], vote["product_id"], vote["data"]) == (
            owner.id,
            pid,
            {"value": -1},
        )
        paged = await s.http.get(
            f"{base}/activity", params={"limit": 2, "offset": 4}, headers=owner.headers
        )
        assert len(paged.json()["items"]) == 1
        assert (await s.http.get(f"{base}/activity", headers=stranger.headers)).status_code == 404


async def test_redelivery_is_idempotent(
    stack: StackFactory, db_session: AsyncSession, session_scope: SessionFactory, cfg: Settings
) -> None:
    broker = InMemoryBroker()
    async with stack() as s:
        owner, ws_id, _ = await seed(s.http)
        await s.http.post(
            f"/api/v1/workspaces/{ws_id}/comments", json={"body": "Hi"}, headers=owner.headers
        )
    relay = OutboxRelay(session_scope, broker.producer(), cfg)
    await relay.publish_batch()
    # The relay crashed after sending but before marking the row: it sends again.
    await db_session.execute(update(OutboxEvent).values(published_at=None))
    await relay.publish_batch()
    assert len(broker.log(TOPIC_EVENTS)) == 2

    consumer = consumer_for(broker, session_scope, cfg)
    await consumer.run_once()
    assert (consumer.stats.handled, consumer.stats.duplicates) == (1, 1)

    # A consumer that died before committing its offset starts over from the last commit.
    broker.committed.clear()
    restarted = consumer_for(broker, session_scope, cfg)
    await restarted.run_once()
    assert (restarted.stats.handled, restarted.stats.duplicates) == (0, 2)
    rows = await db_session.scalar(select(func.count()).select_from(WorkspaceActivity))
    assert rows == 1


async def test_handler_retry_then_dead_letter(
    db_session: AsyncSession, session_scope: SessionFactory, cfg: Settings
) -> None:
    broker = InMemoryBroker()
    producer = broker.producer()
    flaky, poison = uuid.uuid4(), uuid.uuid4()
    for event_id in (flaky, poison):
        body = {
            "id": str(event_id),
            "type": "t",
            "version": 1,
            "occurred_at": "2026-10-01T00:00:00Z",
        }
        await producer.send(
            TOPIC_EVENTS, b"k", json.dumps(body).encode(), {"event_id": str(event_id)}
        )
    await producer.send(TOPIC_EVENTS, b"k", b"{not json", {})
    calls: dict[uuid.UUID, int] = {}

    async def handler(session: AsyncSession, envelope: Envelope) -> None:
        calls[envelope.id] = calls.get(envelope.id, 0) + 1
        if envelope.id == poison or calls[envelope.id] < 3:
            raise RuntimeError("boom")

    consumer = consumer_for(broker, session_scope, cfg, handler)
    assert await consumer.run_once() == 3
    stats = consumer.stats
    assert (stats.handled, stats.retries, stats.dead_lettered) == (1, 4, 2)
    assert calls == {flaky: 3, poison: 3}

    dead = broker.log(DLQ)
    assert [m.headers["dlq_attempts"] for m in dead] == ["3", "0"]
    assert dead[0].headers["dlq_error"] == "RuntimeError: boom"
    assert dead[0].headers["dlq_source"] == f"{TOPIC_EVENTS}:0:1"
    assert dead[0].headers["dlq_consumer"] == ACTIVITY_CONSUMER
    assert dead[0].headers["event_id"] == str(poison)  # original headers are kept
    assert dead[1].headers["dlq_error"].startswith("invalid_envelope")
    assert dead[1].value == b"{not json"

    # Offsets moved past the dead letters, and only the success is marked as processed.
    assert await consumer.run_once() == 0
    processed = list(await db_session.scalars(select(ProcessedEvent.event_id)))
    assert processed == [flaky]


async def test_message_is_redelivered_when_the_dead_letter_topic_is_down(
    session_scope: SessionFactory, cfg: Settings
) -> None:
    broker = InMemoryBroker()
    await broker.producer().send(TOPIC_EVENTS, None, b"garbage", {})
    await broker.producer().send(TOPIC_EVENTS, None, b"garbage too", {})
    consumer = consumer_for(broker, session_scope, cfg)
    broker.fail_next_sends = 1
    with pytest.raises(ConnectionError):
        await consumer.run_once()
    assert broker.committed == {}
    assert await consumer.run_once() == 2  # both come back
    assert [m.value for m in broker.log(DLQ)] == [b"garbage", b"garbage too"]
    assert broker.committed == {(ACTIVITY_CONSUMER, TOPIC_EVENTS): 2}


async def test_events_outside_the_feed_are_consumed_without_a_row(
    db_session: AsyncSession, session_scope: SessionFactory, cfg: Settings
) -> None:
    broker = InMemoryBroker()
    db_session.add(new_event("prices.recorded", {"inserted": 1}, product_id=uuid.uuid4()))
    db_session.add(new_event("something.new", {}, workspace_id=uuid.uuid4()))
    await db_session.flush()
    await OutboxRelay(session_scope, broker.producer(), cfg).publish_batch()
    consumer = consumer_for(broker, session_scope, cfg)
    await consumer.run_once()
    assert consumer.stats.handled == 2
    assert await db_session.scalar(select(func.count()).select_from(WorkspaceActivity)) == 0


def test_message_defaults() -> None:
    assert Message("t", None, b"v") == Message("t", None, b"v", {}, 0, 0)


async def test_worker_loops_run_until_stopped(
    db_session: AsyncSession,
    session_scope: SessionFactory,
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(event_worker, "HEARTBEAT_FILE", tmp_path / "beat")
    monkeypatch.setattr(event_worker, "ERROR_PAUSE_SECONDS", 0.01)
    settings = make_settings(
        outbox_batch_size=2, outbox_poll_interval_seconds=0.01, kafka_consumer_backoff_seconds=0
    )
    ws_id = uuid.UUID(await _workspace_id(db_session))
    for i in range(5):
        db_session.add(new_event("vote.changed", {"value": 1, "i": i}, workspace_id=ws_id))
        await db_session.flush()
    broker = InMemoryBroker()
    broker.fail_next_sends = 1  # the first relay iteration hits a broker error and recovers
    stop = asyncio.Event()
    relay = OutboxRelay(session_scope, broker.producer(), settings)
    relay_task = asyncio.create_task(event_worker.relay_loop(relay, settings, stop))
    async with asyncio.timeout(5):
        while len(broker.log(TOPIC_EVENTS)) < 4:  # noqa: ASYNC110 - polling a plain list
            await asyncio.sleep(0.01)
    stop.set()
    await relay_task
    assert (tmp_path / "beat").exists()

    failures = 0

    async def flaky_poll(timeout_seconds: float, max_messages: int) -> list[Message]:
        nonlocal failures
        if failures == 0:
            failures += 1
            raise ConnectionError("broker restarting")
        return await real_poll(timeout_seconds, max_messages)

    stop = asyncio.Event()
    consumer = consumer_for(broker, session_scope, settings)
    real_poll = consumer._consumer.poll
    monkeypatch.setattr(consumer._consumer, "poll", flaky_poll)
    consumer_task = asyncio.create_task(event_worker.consumer_loop(consumer, stop))
    async with asyncio.timeout(5):
        while consumer.stats.handled < 4:  # noqa: ASYNC110 - polling a counter
            await asyncio.sleep(0.01)
    stop.set()
    await consumer_task
    assert failures == 1


async def _workspace_id(session: AsyncSession) -> str:
    """A real workspace row, for events whose handler writes a foreign key to it."""
    user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash="x", display_name="U")
    session.add(user)
    await session.flush()
    org = Organization(name="O", created_by_id=user.id)
    session.add(org)
    await session.flush()
    ws = ComparisonWorkspace(organization_id=org.id, created_by_id=user.id, name="W")
    session.add(ws)
    await session.flush()
    return str(ws.id)
