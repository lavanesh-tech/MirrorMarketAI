"""PostgreSQL fixtures for database, migration and readiness tests.

Where the database comes from:
- If TEST_DATABASE_URL is set (postgresql+asyncpg://...), use that server.
  Its user must be allowed to CREATE DATABASE.
- Otherwise start a throwaway pgvector container with Testcontainers
  (requires Docker Desktop to be running). This is the default on the Mac and in CI.

Isolation:
- Migration tests each get a brand-new database (`fresh_database_url`).
- Everything else shares one database migrated to head once per session, and
  each test runs inside a transaction that is rolled back afterwards.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator

import asyncpg
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.core.migrations import alembic_config

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg17-trixie"


def _asyncpg_dsn(sqlalchemy_url: str) -> str:
    """asyncpg wants plain postgresql:// without the +asyncpg driver suffix."""
    return make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(False)


def _with_database(sqlalchemy_url: str, database: str) -> str:
    return make_url(sqlalchemy_url).set(database=database).render_as_string(False)


async def execute_sql(database_url: str, sql: str) -> None:
    """Run raw SQL on the given database outside SQLAlchemy (setup/teardown only)."""
    connection = await asyncpg.connect(_asyncpg_dsn(database_url))
    try:
        await connection.execute(sql)
    finally:
        await connection.close()


async def _admin_execute(server_url: str, sql: str) -> None:
    """CREATE/DROP DATABASE must run while connected to a different database."""
    await execute_sql(_with_database(server_url, "postgres"), sql)


def migrate(database_url: str, revision: str = "head") -> None:
    config = alembic_config(database_url)
    config.attributes["skip_logging_config"] = True
    command.upgrade(config, revision)


def downgrade(database_url: str, revision: str) -> None:
    config = alembic_config(database_url)
    config.attributes["skip_logging_config"] = True
    command.downgrade(config, revision)


@pytest.fixture(scope="session")
def server_url() -> Iterator[str]:
    """SQLAlchemy URL of a PostgreSQL server the tests may create databases on."""
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        yield explicit
        return

    from testcontainers.community.postgres import PostgresContainer  # noqa: PLC0415

    with PostgresContainer(PGVECTOR_IMAGE, driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture
def fresh_database_url(server_url: str) -> Iterator[Callable[[], str]]:
    """Factory for empty, unmigrated databases; all are dropped after the test."""
    created: list[str] = []

    def _create() -> str:
        name = f"mm_test_{uuid.uuid4().hex[:12]}"
        asyncio.run(_admin_execute(server_url, f'CREATE DATABASE "{name}"'))
        created.append(name)
        return _with_database(server_url, name)

    yield _create
    for name in created:
        asyncio.run(_admin_execute(server_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def migrated_database_url(server_url: str) -> Iterator[str]:
    """One database, migrated to head, shared by non-migration DB tests."""
    name = f"mm_test_shared_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin_execute(server_url, f'CREATE DATABASE "{name}"'))
    url = _with_database(server_url, name)
    migrate(url)
    yield url
    asyncio.run(_admin_execute(server_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
async def engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A connection inside an outer transaction that is always rolled back."""
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            yield conn
        finally:
            await transaction.rollback()


@pytest.fixture
async def db_session(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """Session bound to the rolled-back connection.

    `create_savepoint` turns the code-under-test's commit()/rollback() into
    SAVEPOINT operations, so services can commit normally and the test still
    leaves no trace in the database.
    """
    session = AsyncSession(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        await session.close()
