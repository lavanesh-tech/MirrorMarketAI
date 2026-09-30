"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import Database


def get_app_settings(request: Request) -> Settings:
    """Return the Settings instance the running app was created with.

    Reading from `app.state` (instead of calling `get_settings()` directly) means
    a test can build an app with custom Settings and every endpoint sees them.
    """
    settings: Settings = request.app.state.settings
    return settings


def get_database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One AsyncSession per request.

    The session is always closed (returning its connection to the pool). If the
    request fails, anything not yet committed is rolled back. Committing is the
    service layer's job, never this dependency's: a request must not persist
    half-finished work just because the handler returned.
    """
    database = get_database(request)
    async with database.session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
