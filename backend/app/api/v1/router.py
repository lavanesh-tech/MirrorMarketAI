"""Aggregates every /api/v1 router. New feature routers are included here."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    agents,
    auth,
    collaboration,
    embeddings,
    evidence,
    health,
    prices,
    products,
    realtime,
    requirements,
    search,
    sources,
    workspace_products,
    workspaces,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(workspaces.router)
api_router.include_router(workspace_products.router)
api_router.include_router(products.router)
api_router.include_router(prices.router)
api_router.include_router(sources.router)
api_router.include_router(embeddings.router)
api_router.include_router(search.router)
api_router.include_router(requirements.router)
api_router.include_router(evidence.router)
api_router.include_router(agents.router)
api_router.include_router(collaboration.router)
api_router.include_router(realtime.router)
