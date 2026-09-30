"""Aggregates every /api/v1 router. New feature routers are included here."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import health

api_router = APIRouter()
api_router.include_router(health.router)
