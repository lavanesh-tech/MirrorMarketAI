"""Tests for the Database wrapper and connection settings against real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.core.database import APPLICATION_NAME, Database
from app.core.migrations import expected_head_revision
from tests.conftest import SettingsFactory

pytestmark = pytest.mark.db


@pytest.fixture
async def database(
    make_settings: SettingsFactory, migrated_database_url: str
) -> AsyncIterator[Database]:
    settings: Settings = make_settings(
        database_url=migrated_database_url, db_statement_timeout_ms=1234
    )
    db = Database.from_settings(settings)
    yield db
    await db.dispose()


@pytest.fixture
def unmigrated_database_url(fresh_database_url: Callable[[], str]) -> str:
    # Sync fixture: the factory uses asyncio.run, which can't run inside a test's loop.
    return fresh_database_url()


async def test_ping_reports_latency(database: Database) -> None:
    result = await database.ping()
    assert result.latency_ms >= 0


async def test_current_revision_is_head(database: Database) -> None:
    assert await database.current_revision() == expected_head_revision()


async def test_connections_carry_server_settings(database: Database) -> None:
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SHOW statement_timeout")) == "1234ms"
        assert await connection.scalar(text("SHOW application_name")) == APPLICATION_NAME


async def test_statement_timeout_cancels_slow_queries(database: Database) -> None:
    async with database.engine.connect() as connection:
        with pytest.raises(Exception, match="statement timeout"):
            await connection.execute(text("SELECT pg_sleep(3)"))


async def test_session_factory_does_not_expire_on_commit(database: Database) -> None:
    async with database.session_factory() as session:
        assert session.sync_session.expire_on_commit is False


async def test_pgvector_extension_is_usable(database: Database) -> None:
    async with database.engine.connect() as connection:
        distance = await connection.scalar(text("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector"))
        assert distance == pytest.approx(1.0)


async def test_current_revision_is_none_for_unmigrated_database(
    make_settings: SettingsFactory, unmigrated_database_url: str
) -> None:
    db = Database.from_settings(make_settings(database_url=unmigrated_database_url))
    try:
        assert await db.current_revision() is None
    finally:
        await db.dispose()
