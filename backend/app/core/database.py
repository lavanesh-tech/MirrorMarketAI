"""Async PostgreSQL connectivity (SQLAlchemy 2.x + asyncpg).

One `Database` object per application process owns the connection pool. It is
created in the FastAPI lifespan, stored on `app.state.database`, and disposed on
shutdown. Creating it does NOT connect: the pool opens connections lazily, so the
API can start (and report "not ready") while PostgreSQL is still booting.

Session rules used throughout the codebase:
- One `AsyncSession` per request (see `app.api.deps.get_db_session`).
- Repositories only `add`/`flush`/query. They never commit.
- The service layer decides the transaction boundary and calls `commit()`.
- `expire_on_commit=False` so returned ORM objects stay readable after commit
  (async code cannot lazily reload attributes).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

APPLICATION_NAME = "mirrormarket-api"

# "Give me a short-lived session": used by WebSockets and background workers,
# which must not hold one session (and its pooled connection) for their lifetime.
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(),
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=settings.db_pool_recycle_seconds,
        # Validate pooled connections before use: survives DB restarts/failovers.
        pool_pre_ping=True,
        connect_args={
            "timeout": settings.db_connect_timeout_seconds,
            "server_settings": {
                "application_name": APPLICATION_NAME,  # visible in pg_stat_activity
                "statement_timeout": str(settings.db_statement_timeout_ms),
            },
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@dataclass(frozen=True, slots=True)
class PingResult:
    latency_ms: float


class Database:
    """Owns the engine and session factory for the lifetime of the app."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.session_factory = create_session_factory(engine)

    @classmethod
    def from_settings(cls, settings: Settings) -> Database:
        return cls(create_engine(settings))

    async def ping(self) -> PingResult:
        """Run a trivial query; raises if the database is unreachable."""
        started = time.perf_counter()
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return PingResult(latency_ms=round((time.perf_counter() - started) * 1000, 2))

    async def current_revision(self) -> str | None:
        """Return the Alembic revision recorded in the database, or None if unmigrated."""
        async with self.engine.connect() as connection:
            exists = await connection.scalar(text("SELECT to_regclass('public.alembic_version')"))
            if exists is None:
                return None
            revision: str | None = await connection.scalar(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            return revision

    async def dispose(self) -> None:
        await self.engine.dispose()
