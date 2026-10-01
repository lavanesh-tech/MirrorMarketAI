from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.events.broker import InMemoryBroker
from app.events.envelope import TOPIC_EVENTS, Envelope, new_event
from app.events.handlers import summarize
from app.events.kafka import KafkaConsumer, KafkaProducer
from app.events.relay import backoff_seconds
from app.workers import event_worker
from tests.conftest import SettingsFactory


def test_backoff_is_exponential_and_capped() -> None:
    assert [backoff_seconds(n, 1.0, 10.0) for n in (0, 1, 2, 3, 4, 5, 50)] == [
        1.0,
        1.0,
        2.0,
        4.0,
        8.0,
        10.0,
        10.0,
    ]


def test_new_event_key_and_envelope() -> None:
    ws, product = uuid.uuid4(), uuid.uuid4()
    by_workspace = new_event("a.b", {"x": 1}, workspace_id=ws, product_id=product)
    assert (by_workspace.key, by_workspace.topic) == (str(ws), TOPIC_EVENTS)
    assert new_event("a.b", {}, product_id=product).key == str(product)
    lonely = new_event("a.b", {}, topic="other")
    assert (lonely.key, lonely.topic) == (str(lonely.id), "other")
    envelope = Envelope.model_validate(by_workspace.payload | {"added_later": True})
    assert (envelope.id, envelope.data, envelope.workspace_id) == (by_workspace.id, {"x": 1}, ws)
    assert envelope.occurred_at.tzinfo is not None


@pytest.mark.parametrize(
    ("event_type", "data", "expected"),
    [
        ("comment.created", {"excerpt": "Hi"}, "commented: Hi"),
        ("comment.created", {"excerpt": "Hi", "parent_id": "x"}, "replied: Hi"),
        ("comment.deleted", {}, "deleted a comment"),
        ("vote.changed", {"value": 1}, "voted up"),
        ("vote.changed", {"value": 0}, "removed their vote"),
        ("vote.changed", {"value": 7}, "voted"),
        ("agent_run.completed", {"agent": "risk", "status": "FAILED"}, "ran risk (failed)"),
        ("prices.recorded", {}, None),
    ],
)
def test_summaries(event_type: str, data: dict[str, object], expected: str | None) -> None:
    envelope = Envelope(
        id=uuid.uuid4(), type=event_type, version=1, occurred_at=datetime.now(UTC), data=data
    )
    assert summarize(envelope) == expected


async def test_in_memory_broker_offsets_and_rewind() -> None:
    broker = InMemoryBroker()
    producer = broker.producer()
    for i in range(3):
        await producer.send("t", None, str(i).encode(), {})
    consumer = broker.consumer("g", "t", "empty")
    first = await consumer.poll(0, 2)
    assert [(m.value, m.offset) for m in first] == [(b"0", 0), (b"1", 1)]
    await consumer.commit(first[0])
    await consumer.rewind(first[1])
    assert [m.value for m in await consumer.poll(0, 10)] == [b"1", b"2"]
    assert [m.value for m in await broker.consumer("g", "t").poll(0, 10)] == [b"1", b"2"]
    assert [m.value for m in await broker.consumer("other", "t").poll(0, 1)] == [b"0"]


async def test_worker_refuses_to_start_when_kafka_is_disabled(
    make_settings: SettingsFactory,
) -> None:
    with pytest.raises(SystemExit):
        await event_worker.run_worker(make_settings(kafka_enabled=False), "all")


async def test_kafka_clients_fail_fast_without_a_broker(make_settings: SettingsFactory) -> None:
    settings: Settings = make_settings(
        kafka_bootstrap_servers="127.0.0.1:1", kafka_request_timeout_seconds=1
    )
    producer = KafkaProducer(settings)
    with pytest.raises(Exception, match=r"(?i)connect|bootstrap"):
        async with asyncio.timeout(20):
            await producer.start()
    await producer.stop()
    consumer = KafkaConsumer(settings, "g", "t")
    with pytest.raises(Exception, match=r"(?i)connect|bootstrap"):
        async with asyncio.timeout(20):
            await consumer.start()
    await consumer.stop()
