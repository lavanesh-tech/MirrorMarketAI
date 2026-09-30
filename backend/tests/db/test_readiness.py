"""/api/v1/ready against real PostgreSQL databases in different states."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from fastapi import FastAPI

from app.main import create_app
from tests.conftest import SettingsFactory
from tests.db.conftest import execute_sql

pytestmark = [pytest.mark.db, pytest.mark.api]


async def _get_ready(app: FastAPI) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        return await client.get("/api/v1/ready")


async def test_ready_when_database_is_migrated(
    make_settings: SettingsFactory, migrated_database_url: str
) -> None:
    response = await _get_ready(create_app(make_settings(database_url=migrated_database_url)))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["database"]["latency_ms"] >= 0
    assert body["checks"]["migrations"] == {"status": "ok", "latency_ms": None, "reason": None}


@pytest.fixture
def unmigrated_url(fresh_database_url: Callable[[], str]) -> str:
    return fresh_database_url()


async def test_not_ready_when_database_is_not_migrated(
    make_settings: SettingsFactory, unmigrated_url: str
) -> None:
    response = await _get_ready(create_app(make_settings(database_url=unmigrated_url)))

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["migrations"]["reason"] == "not_migrated"


@pytest.fixture
def wrong_revision_url(fresh_database_url: Callable[[], str]) -> str:
    url = fresh_database_url()
    asyncio.run(
        execute_sql(
            url,
            "CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY);"
            "INSERT INTO alembic_version VALUES ('not-a-real-revision');",
        )
    )
    return url


async def test_not_ready_when_schema_revision_mismatches(
    make_settings: SettingsFactory, wrong_revision_url: str
) -> None:
    response = await _get_ready(create_app(make_settings(database_url=wrong_revision_url)))

    assert response.status_code == 503
    assert response.json()["checks"]["migrations"]["reason"] == "schema_mismatch"


@pytest.fixture
async def db_client(
    make_settings: SettingsFactory, migrated_database_url: str
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(make_settings(database_url=migrated_database_url))
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        yield client


async def test_health_stays_ok_independently_of_readiness(db_client: httpx.AsyncClient) -> None:
    assert (await db_client.get("/api/v1/health")).status_code == 200
    assert (await db_client.get("/api/v1/ready")).status_code == 200
