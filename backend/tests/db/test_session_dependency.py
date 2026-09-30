"""The per-request session dependency against real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.main import create_app
from tests.conftest import SettingsFactory

pytestmark = [pytest.mark.db, pytest.mark.api]

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


@pytest.fixture
async def client(
    make_settings: SettingsFactory, migrated_database_url: str
) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(make_settings(database_url=migrated_database_url))

    @app.get("/api/v1/_db/value")
    async def value(session: SessionDep) -> dict[str, int]:
        return {"value": int(await session.scalar(text("SELECT 41 + 1")) or 0)}

    @app.get("/api/v1/_db/fail")
    async def fail(session: SessionDep) -> None:
        await session.execute(text("SELECT 1"))
        raise RuntimeError("handler failed after using the session")

    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client,
    ):
        yield http_client


async def test_session_executes_queries(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/_db/value")
    assert response.status_code == 200
    assert response.json() == {"value": 42}


async def test_failing_handler_rolls_back_and_releases_connection(
    client: httpx.AsyncClient,
) -> None:
    for _ in range(3):
        response = await client.get("/api/v1/_db/fail")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "internal_error"
    # The pool is still healthy afterwards (connections were returned, not leaked).
    assert (await client.get("/api/v1/_db/value")).status_code == 200
