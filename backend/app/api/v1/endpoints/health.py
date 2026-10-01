"""Operational health endpoints: liveness (/health) and readiness (/ready)."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from redis.asyncio import Redis

from app import __version__
from app.api.deps import get_app_settings, get_database
from app.core.config import Settings
from app.core.database import Database
from app.core.migrations import expected_head_revision
from app.core.redis import ping
from app.schemas.health import HealthResponse, ReadinessCheck, ReadinessResponse

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

SERVICE_NAME = "mirrormarket-api"


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Returns 200 while the API process is running. Does not check dependencies.",
)
async def health(settings: Annotated[Settings, Depends(get_app_settings)]) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=SERVICE_NAME,
        version=__version__,
        environment=settings.app_env.value,
    )


async def _check_database(database: Database, budget_seconds: float) -> ReadinessCheck:
    try:
        async with asyncio.timeout(budget_seconds):
            result = await database.ping()
    except TimeoutError:
        logger.warning("readiness: database check timed out")
        return ReadinessCheck(status="fail", reason="timeout")
    except Exception:
        logger.warning("readiness: database unreachable", exc_info=True)
        return ReadinessCheck(status="fail", reason="unreachable")
    return ReadinessCheck(status="ok", latency_ms=result.latency_ms)


async def _check_migrations(database: Database, budget_seconds: float) -> ReadinessCheck:
    try:
        async with asyncio.timeout(budget_seconds):
            current = await database.current_revision()
    except TimeoutError:
        return ReadinessCheck(status="fail", reason="timeout")
    except Exception:
        logger.warning("readiness: could not read migration state", exc_info=True)
        return ReadinessCheck(status="fail", reason="unreachable")
    if current is None:
        return ReadinessCheck(status="fail", reason="not_migrated")
    if current != expected_head_revision():
        logger.warning(
            "readiness: schema revision mismatch",
            extra={"db_revision": current, "code_revision": expected_head_revision()},
        )
        return ReadinessCheck(status="fail", reason="schema_mismatch")
    return ReadinessCheck(status="ok")


async def _check_redis(redis: Redis | None, budget_seconds: float) -> ReadinessCheck:
    """Redis features fail open, so Redis is reported but never blocks readiness."""
    if redis is None:
        return ReadinessCheck(status="disabled", required=False)
    try:
        async with asyncio.timeout(budget_seconds):
            latency = await ping(redis)
    except TimeoutError:
        return ReadinessCheck(status="fail", required=False, reason="timeout")
    except Exception:
        logger.warning("readiness: redis unreachable", exc_info=True)
        return ReadinessCheck(status="fail", required=False, reason="unreachable")
    return ReadinessCheck(status="ok", required=False, latency_ms=latency)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Returns 200 only when PostgreSQL is reachable and its schema is at the migration "
        "revision this build expects; otherwise 503. Redis is reported (required=false) "
        "but does not affect readiness: its features fail open. Load balancers should "
        "route traffic only to ready instances."
    ),
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_app_settings)],
    database: Annotated[Database, Depends(get_database)],
) -> ReadinessResponse:
    timeout = settings.readiness_timeout_seconds
    db_check = await _check_database(database, timeout)
    if db_check.status == "ok":
        schema_check = await _check_migrations(database, timeout)
    else:
        schema_check = ReadinessCheck(status="fail", reason="unreachable")

    checks = {
        "database": db_check,
        "migrations": schema_check,
        "redis": await _check_redis(request.app.state.redis, timeout),
    }
    is_ready = all(check.status == "ok" for check in checks.values() if check.required)
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ready" if is_ready else "not_ready", checks=checks)
