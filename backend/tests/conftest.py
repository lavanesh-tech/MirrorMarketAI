"""Shared pytest fixtures.

Tests build the app through `create_app(settings)` with explicit Settings, so
they never depend on a developer's local `.env` or shell environment.

Redis is OFF by default (`redis_url=None`), so ordinary tests never share rate
limit counters. Tests that need it use `redis_url`: TEST_REDIS_URL if set,
otherwise a throwaway Testcontainers Redis; the database is flushed after each test.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Iterator

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Environment, Settings
from app.main import create_app

SettingsFactory = Callable[..., Settings]


@pytest.fixture
def make_settings() -> SettingsFactory:
    """Build Settings that ignore any `.env` file; keyword args override fields."""

    def _make(**overrides: object) -> Settings:
        values: dict[str, object] = {
            "app_env": Environment.TEST,
            "log_level": "DEBUG",
            "log_format": "json",
            "redis_url": None,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)  # type: ignore[arg-type]

    return _make


@pytest.fixture
def settings(make_settings: SettingsFactory) -> Settings:
    return make_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """In-process HTTP client. Runs the app's lifespan like a real server would."""
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client,
    ):
        yield http_client


REDIS_IMAGE = "redis:7.4-alpine"


@pytest.fixture(scope="session")
def redis_server_url() -> Iterator[str]:
    explicit = os.environ.get("TEST_REDIS_URL")
    if explicit:
        yield explicit
        return

    from testcontainers.redis import RedisContainer  # noqa: PLC0415

    with RedisContainer(REDIS_IMAGE) as container:
        host = container.get_container_host_ip()
        yield f"redis://{host}:{container.get_exposed_port(6379)}/0"


@pytest.fixture
def redis_url(redis_server_url: str) -> Iterator[str]:
    import redis  # noqa: PLC0415

    yield redis_server_url
    with redis.Redis.from_url(redis_server_url) as sync_client:
        sync_client.flushdb()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Detach app log handlers before interpreter exit.

    Testcontainers stops its reaper in an atexit hook and logs while doing so;
    by then pytest has closed the captured stdout our handler points at.
    """
    import logging  # noqa: PLC0415

    logging.getLogger().handlers.clear()
