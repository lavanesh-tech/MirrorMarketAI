"""Readiness failure modes that need no running database."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.core.database import Database, PingResult
from app.main import create_app
from tests.conftest import SettingsFactory

pytestmark = pytest.mark.api

# Nothing listens on port 1; the connection is refused immediately.
_UNREACHABLE_URL = "postgresql+asyncpg://mm:secret-pw@127.0.0.1:1/mirrormarket"


async def _get_ready(app: FastAPI) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        return await client.get("/api/v1/ready")


async def test_not_ready_when_database_unreachable(make_settings: SettingsFactory) -> None:
    app = create_app(make_settings(database_url=_UNREACHABLE_URL, db_connect_timeout_seconds=1))
    response = await _get_ready(app)

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "database": {"status": "fail", "latency_ms": None, "reason": "unreachable"},
            "migrations": {"status": "fail", "latency_ms": None, "reason": "unreachable"},
        },
    }
    # Connection details must never leak to clients.
    assert "127.0.0.1" not in response.text
    assert "secret-pw" not in response.text


async def test_not_ready_when_database_check_times_out(
    make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def slow_ping(self: Database) -> PingResult:
        await asyncio.sleep(5)
        return PingResult(latency_ms=5000)

    monkeypatch.setattr(Database, "ping", slow_ping)
    app = create_app(make_settings(readiness_timeout_seconds=0.05))
    response = await _get_ready(app)

    assert response.status_code == 503
    assert response.json()["checks"]["database"]["reason"] == "timeout"


async def test_health_is_ok_even_when_database_is_down(make_settings: SettingsFactory) -> None:
    app = create_app(make_settings(database_url=_UNREACHABLE_URL))
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        assert (await client.get("/api/v1/health")).status_code == 200


async def test_openapi_documents_ready_endpoint(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/api/v1/openapi.json")).json()
    responses = schema["paths"]["/api/v1/ready"]["get"]["responses"]
    assert {"200", "503"} <= set(responses)
