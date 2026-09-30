"""FastAPI application factory.

`create_app()` builds a fresh, fully configured application. Using a factory
(instead of a module-level app with global state) lets tests create isolated
apps with their own Settings, and keeps startup order explicit:

    settings -> logging -> FastAPI instance -> middleware -> routers

Run locally:
    uv run uvicorn app.main:create_app --factory --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import Database
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.ingestion.safe_fetch import SafeFetcher
from app.providers.embeddings import create_embedding_provider
from app.providers.extraction import create_requirement_extractor

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hook: owns every long-lived resource.

    The database pool is created here (not at import time) and disposed after
    `yield`, so connections are tied to the application's lifetime. Creating the
    pool does not connect; `/api/v1/ready` reports whether PostgreSQL is usable.
    """
    settings: Settings = app.state.settings
    database = Database.from_settings(settings)
    app.state.database = database
    fetcher = SafeFetcher(settings)
    app.state.fetcher = fetcher
    embedder = create_embedding_provider(settings)
    app.state.embedder = embedder
    extractor = create_requirement_extractor(settings)
    app.state.extractor = extractor
    logger.info(
        "application startup",
        extra={
            "app_env": settings.app_env.value,
            "version": __version__,
            "openai_configured": settings.openai_configured,
            "kafka_enabled": settings.kafka_enabled,
        },
    )
    try:
        yield
    finally:
        await extractor.aclose()
        await embedder.aclose()
        await fetcher.aclose()
        await database.dispose()
        logger.info("application shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        debug=settings.debug,
        lifespan=lifespan,
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url=f"{settings.api_v1_prefix}/docs",
        redoc_url=None,
    )
    app.state.settings = settings

    # Middleware added LAST runs FIRST. RequestContextMiddleware is outermost so
    # the request ID exists for everything else, including CORS rejections.
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
        )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app
