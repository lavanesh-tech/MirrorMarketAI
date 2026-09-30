"""Migration tests: fresh upgrade, round trip, schema drift, single head.

These are synchronous tests because Alembic's env.py drives its own event loop.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Callable
from contextlib import redirect_stdout
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.migrations import alembic_config, expected_head_revision
from app.models import Base
from tests.db.conftest import downgrade, migrate

pytestmark = pytest.mark.db


async def _query(url: str, sql: str) -> Any:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(sql))
    finally:
        await engine.dispose()


async def _schema_diff(url: str) -> list[Any]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(
                        sync_conn, opts={"compare_type": True, "compare_server_default": True}
                    ),
                    Base.metadata,
                )
            )
    finally:
        await engine.dispose()


def _revision(url: str) -> str | None:
    exists = asyncio.run(_query(url, "SELECT to_regclass('public.alembic_version')::text"))
    if exists is None:
        return None
    result: str | None = asyncio.run(_query(url, "SELECT version_num FROM alembic_version"))
    return result


def _vector_installed(url: str) -> bool:
    count = asyncio.run(_query(url, "SELECT count(*) FROM pg_extension WHERE extname = 'vector'"))
    return bool(count)


def test_migration_history_has_a_single_head() -> None:
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    assert len(heads) == 1
    assert heads[0] == expected_head_revision()


def test_fresh_database_upgrades_to_head(fresh_database_url: Callable[[], str]) -> None:
    url = fresh_database_url()
    assert _revision(url) is None

    migrate(url)

    assert _revision(url) == expected_head_revision()
    assert _vector_installed(url)


def test_full_downgrade_and_reupgrade_round_trip(fresh_database_url: Callable[[], str]) -> None:
    url = fresh_database_url()
    migrate(url)

    downgrade(url, "base")
    assert _revision(url) is None
    assert not _vector_installed(url)

    migrate(url)
    assert _revision(url) == expected_head_revision()
    assert _vector_installed(url)


def test_upgrade_is_idempotent_at_head(fresh_database_url: Callable[[], str]) -> None:
    url = fresh_database_url()
    migrate(url)
    migrate(url)
    assert _revision(url) == expected_head_revision()


def test_models_and_migrations_have_no_drift(fresh_database_url: Callable[[], str]) -> None:
    """Fails if someone changes a model without generating a migration."""
    url = fresh_database_url()
    migrate(url)
    assert asyncio.run(_schema_diff(url)) == []


def test_offline_sql_generation() -> None:
    """`alembic upgrade head --sql` must work (used to review SQL before production)."""
    config = alembic_config("postgresql+asyncpg://user:pass@localhost:5432/unused")
    config.attributes["skip_logging_config"] = True
    buffer = io.StringIO()
    config.output_buffer = buffer
    with redirect_stdout(buffer):
        command.upgrade(config, "head", sql=True)
    sql = buffer.getvalue()
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "alembic_version" in sql
