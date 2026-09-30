"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import Database
from app.core.errors import AuthenticationError
from app.ingestion.safe_fetch import SafeFetcher
from app.models.identity import User
from app.providers.embeddings import EmbeddingProvider
from app.repositories.identity import UserRepository
from app.security.tokens import decode_access_token


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


_bearer = HTTPBearer(auto_error=False, description="Access token from POST /api/v1/auth/login")


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Resolve the Bearer token to an active user, or fail with 401."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError
    claims = decode_access_token(credentials.credentials, settings)
    user = await UserRepository(session).get(claims.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid token.")
    return user


def get_fetcher(request: Request) -> SafeFetcher:
    fetcher: SafeFetcher = request.app.state.fetcher
    return fetcher


def get_embedder(request: Request) -> EmbeddingProvider:
    embedder: EmbeddingProvider = request.app.state.embedder
    return embedder


FetcherDep = Annotated[SafeFetcher, Depends(get_fetcher)]
EmbedderDep = Annotated[EmbeddingProvider, Depends(get_embedder)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
CurrentUser = Annotated[User, Depends(get_current_user)]
