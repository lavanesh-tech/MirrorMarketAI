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
from app.coordination.cache import JsonCache
from app.coordination.idempotency import IdempotencyMiddleware
from app.coordination.rate_limit import RateLimiter
from app.core import openapi
from app.core.config import Environment, Settings, get_settings
from app.core.database import Database
from app.core.errors import register_exception_handlers
from app.core.http_hardening import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.redis import create_redis, create_subscriber_redis
from app.ingestion.safe_fetch import SafeFetcher
from app.providers.embeddings import create_embedding_provider
from app.providers.extraction import create_requirement_extractor
from app.providers.llm import OpenAIChatClient
from app.realtime.bus import EventBus
from app.realtime.hub import Hub
from app.realtime.presence import Presence

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
    redis = create_redis(settings)  # lazy: connects on first command
    app.state.redis = redis
    app.state.cache = JsonCache(redis, settings.redis_key_prefix, settings.price_cache_ttl_seconds)
    app.state.rate_limiter = RateLimiter(redis, settings.redis_key_prefix)
    hub = Hub()
    subscriber = create_subscriber_redis(settings)
    bus = EventBus(hub, redis, subscriber, settings.redis_key_prefix)
    app.state.hub = hub
    app.state.bus = bus
    app.state.presence = Presence(
        hub, redis, settings.redis_key_prefix, settings.presence_ttl_seconds
    )
    bus.start()
    fetcher = SafeFetcher(settings)
    app.state.fetcher = fetcher
    embedder = create_embedding_provider(settings)
    app.state.embedder = embedder
    extractor = create_requirement_extractor(settings)
    app.state.extractor = extractor
    llm = OpenAIChatClient(settings) if settings.agent_engine == "openai" else None
    app.state.llm = llm
    logger.info(
        "application startup",
        extra={
            "app_env": settings.app_env.value,
            "version": __version__,
            "openai_configured": settings.openai_configured,
            "kafka_enabled": settings.kafka_enabled,
            "redis_configured": redis is not None,
        },
    )
    try:
        yield
    finally:
        if llm is not None:
            await llm.aclose()
        await extractor.aclose()
        await embedder.aclose()
        await fetcher.aclose()
        await bus.stop()
        if subscriber is not None:
            await subscriber.aclose()
        if redis is not None:
            await redis.aclose()
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
        openapi_url=f"{settings.api_v1_prefix}/openapi.json" if settings.docs_enabled else None,
        docs_url=f"{settings.api_v1_prefix}/docs" if settings.docs_enabled else None,
        redoc_url=None,
        generate_unique_id_function=openapi.operation_id,
    )
    app.state.settings = settings

    # Middleware added LAST runs FIRST. RequestContextMiddleware is outermost so
    # the request ID exists for everything else, including CORS rejections.
    # Idempotency is innermost: it replays exactly what the routes produced.
    app.add_middleware(
        IdempotencyMiddleware,
        prefix=settings.redis_key_prefix,
        ttl_seconds=settings.idempotency_ttl_seconds,
        lock_seconds=settings.idempotency_lock_seconds,
        max_body_bytes=settings.idempotency_max_body_bytes,
    )
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
            expose_headers=[
                "X-Request-ID",
                "Retry-After",
                "X-RateLimit-Limit",
                "X-RateLimit-Remaining",
                "X-Cache",
                "Idempotent-Replayed",
            ],
        )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    if settings.security_headers_enabled:
        secure_transport = settings.app_env in (Environment.STAGING, Environment.PRODUCTION)
        app.add_middleware(
            SecurityHeadersMiddleware,
            hsts_max_age=settings.hsts_max_age_seconds if secure_transport else 0,
            docs_prefix=f"{settings.api_v1_prefix}/docs",
        )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    openapi.install(app)
    return app
