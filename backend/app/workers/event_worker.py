"""Event worker: publishes the outbox to Kafka and runs the Kafka consumers.

    python -m app.workers.event_worker                 # relay + consumers
    python -m app.workers.event_worker --role relay    # only publish the outbox
    python -m app.workers.event_worker --role consumer # only consume

Safe to run many copies: the relay claims rows with FOR UPDATE SKIP LOCKED and
consumers in one group share partitions. Requires KAFKA_ENABLED=true.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import time
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.database import Database
from app.core.logging import configure_logging
from app.events.consumer import EventConsumer
from app.events.envelope import DLQ_SUFFIX, TOPIC_EVENTS
from app.events.handlers import ACTIVITY_CONSUMER, project_activity
from app.events.kafka import KafkaConsumer, KafkaProducer, ensure_topics
from app.events.relay import OutboxRelay
from app.workers.embedding_worker import heartbeat

logger = logging.getLogger("app.workers.events")

HEARTBEAT_FILE = Path("/tmp/event-worker.heartbeat")  # noqa: S108 - tmpfs in the container
PURGE_INTERVAL_SECONDS = 3600.0
ERROR_PAUSE_SECONDS = 2.0


async def relay_loop(relay: OutboxRelay, settings: Settings, stop: asyncio.Event) -> None:
    last_purge = 0.0
    while not stop.is_set():
        heartbeat(HEARTBEAT_FILE)
        pause = settings.outbox_poll_interval_seconds
        try:
            result = await relay.publish_batch()
            if result.published == settings.outbox_batch_size:
                pause = 0.0  # a full batch means there is probably more
            if time.monotonic() - last_purge > PURGE_INTERVAL_SECONDS:
                last_purge = time.monotonic()
                removed = await relay.purge()
                if removed:
                    logger.info("outbox purged", extra={"removed": removed})
        except Exception:
            logger.exception("outbox relay iteration failed")
            pause = ERROR_PAUSE_SECONDS
        if pause:
            try:
                await asyncio.wait_for(stop.wait(), timeout=pause)
            except TimeoutError:
                continue


async def consumer_loop(consumer: EventConsumer, stop: asyncio.Event) -> None:
    while not stop.is_set():
        heartbeat(HEARTBEAT_FILE)
        try:
            await consumer.run_once(timeout_seconds=1.0)
        except Exception:
            # Nothing was committed for the failed message, so it is delivered again.
            logger.exception("consumer iteration failed", extra={"consumer": consumer.name})
            await asyncio.sleep(ERROR_PAUSE_SECONDS)


async def run_worker(settings: Settings, role: str) -> None:
    if not settings.kafka_enabled:
        raise SystemExit("KAFKA_ENABLED is false: the event worker has nothing to do.")
    database = Database.from_settings(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await ensure_topics(settings, [TOPIC_EVENTS, TOPIC_EVENTS + DLQ_SUFFIX])
    producer = KafkaProducer(settings)
    await producer.start()
    kafka_consumer = KafkaConsumer(settings, ACTIVITY_CONSUMER, TOPIC_EVENTS)
    tasks: list[asyncio.Task[None]] = []
    logger.info("event worker started", extra={"role": role})
    try:
        if role in ("all", "relay"):
            relay = OutboxRelay(database.session_factory, producer, settings)
            tasks.append(asyncio.create_task(relay_loop(relay, settings, stop)))
        if role in ("all", "consumer"):
            await kafka_consumer.start()
            consumer = EventConsumer(
                ACTIVITY_CONSUMER,
                kafka_consumer,
                producer,
                project_activity,
                database.session_factory,
                settings,
            )
            tasks.append(asyncio.create_task(consumer_loop(consumer, stop)))
        await asyncio.gather(*tasks)
    finally:
        if role in ("all", "consumer"):
            await kafka_consumer.stop()
        await producer.stop()
        await database.dispose()
        logger.info("event worker stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=["all", "relay", "consumer"], default="all")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    asyncio.run(run_worker(settings, args.role))


if __name__ == "__main__":
    main()
