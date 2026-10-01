"""A real Kafka broker for adapter and pipeline tests.

TEST_KAFKA_BOOTSTRAP (host:port) if set, otherwise a throwaway single-node KRaft
broker started with Testcontainers (needs Docker; the default on the Mac and in CI).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import pytest

from app.core.config import Settings

KAFKA_IMAGE = "confluentinc/cp-kafka:7.6.0"
STARTUP_TIMEOUT_SECONDS = 180


@pytest.fixture(scope="session")
def kafka_bootstrap() -> Iterator[str]:
    explicit = os.environ.get("TEST_KAFKA_BOOTSTRAP")
    if explicit:
        yield explicit
        return

    from testcontainers.community.kafka import KafkaContainer  # noqa: PLC0415

    container = KafkaContainer(KAFKA_IMAGE).with_kraft()
    container.start(timeout=STARTUP_TIMEOUT_SECONDS)
    try:
        yield container.get_bootstrap_server()
    finally:
        container.stop()


@pytest.fixture
def kafka_settings(make_settings: Callable[..., Settings], kafka_bootstrap: str) -> Settings:
    return make_settings(
        kafka_enabled=True,
        kafka_bootstrap_servers=kafka_bootstrap,
        kafka_consumer_backoff_seconds=0,
    )
