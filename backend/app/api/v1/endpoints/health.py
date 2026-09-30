"""Operational health endpoints: liveness (/health) and readiness (/ready)."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app import __version__
from app.api.deps import get_app_settings, get_database
from app.core.config import Settings
from app.core.database import Database
from app.core.migrations import expected_head_revision
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


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Returns 200 only when PostgreSQL is reachable and its schema is at the migration "
        "revision this build expects; otherwise 503. Load balancers should route traffic "
        "only to ready instances."
    ),
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(
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

    checks = {"database": db_check, "migrations": schema_check}
    is_ready = all(check.status == "ok" for check in checks.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ready" if is_ready else "not_ready", checks=checks)
