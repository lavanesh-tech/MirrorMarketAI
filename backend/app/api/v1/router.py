"""Aggregates every /api/v1 router. New feature routers are included here."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import auth, health, workspaces

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(workspaces.router)
