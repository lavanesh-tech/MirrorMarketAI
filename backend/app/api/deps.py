"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from app.coordination.cache import JsonCache
from app.coordination.rate_limit import RateLimiter
from app.core.config import Settings
from app.core.database import Database, SessionFactory
from app.core.errors import AuthenticationError
from app.ingestion.safe_fetch import SafeFetcher
from app.models.identity import User
from app.providers.embeddings import EmbeddingProvider
from app.providers.extraction import RequirementExtractor
from app.providers.llm import OpenAIChatClient
from app.realtime.bus import EventBus
from app.realtime.presence import Presence
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


async def resolve_user(session: AsyncSession, token: str, settings: Settings) -> User:
    """Access token -> active user, or AuthenticationError. Shared by HTTP and WebSockets."""
    claims = decode_access_token(token, settings)
    user = await UserRepository(session).get(claims.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid token.")
    # "Log out everywhere" / password change: older tokens die before they expire.
    if claims.token_version != user.token_version:
        raise AuthenticationError("Token has been revoked.")
    return user


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Resolve the Bearer token to an active user, or fail with 401."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError
    return await resolve_user(session, credentials.credentials, settings)


def get_fetcher(request: Request) -> SafeFetcher:
    fetcher: SafeFetcher = request.app.state.fetcher
    return fetcher


def get_extractor(request: Request) -> RequirementExtractor:
    extractor: RequirementExtractor = request.app.state.extractor
    return extractor


def get_llm(request: Request) -> OpenAIChatClient | None:
    llm: OpenAIChatClient | None = request.app.state.llm
    return llm


def get_embedder(request: Request) -> EmbeddingProvider:
    embedder: EmbeddingProvider = request.app.state.embedder
    return embedder


def get_cache(request: Request) -> JsonCache:
    cache: JsonCache = request.app.state.cache
    return cache


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


def get_event_bus(request: Request) -> EventBus:
    bus: EventBus = request.app.state.bus
    return bus


def get_presence(request: Request) -> Presence:
    presence: Presence = request.app.state.presence
    return presence


def get_session_factory(connection: HTTPConnection) -> SessionFactory:
    """For long-lived connections (WebSockets): open a session only while it is needed.

    A WebSocket must not hold a pooled database connection for its whole lifetime,
    so the handler opens short sessions from this factory instead of using
    `get_db_session`.
    """
    database: Database = connection.app.state.database
    return database.session_factory


EventsDep = Annotated[EventBus, Depends(get_event_bus)]
PresenceDep = Annotated[Presence, Depends(get_presence)]
SessionFactoryDep = Annotated[SessionFactory, Depends(get_session_factory)]
CacheDep = Annotated[JsonCache, Depends(get_cache)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
FetcherDep = Annotated[SafeFetcher, Depends(get_fetcher)]
EmbedderDep = Annotated[EmbeddingProvider, Depends(get_embedder)]
ExtractorDep = Annotated[RequirementExtractor, Depends(get_extractor)]
LLMDep = Annotated[OpenAIChatClient | None, Depends(get_llm)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
CurrentUser = Annotated[User, Depends(get_current_user)]
