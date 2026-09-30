"""Operational health endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app import __version__
from app.api.deps import get_app_settings
from app.core.config import Settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])

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
