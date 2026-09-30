from __future__ import annotations

import httpx
import pytest

from app import __version__

pytestmark = pytest.mark.api


async def test_health_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "status": "ok",
        "service": "mirrormarket-api",
        "version": __version__,
        "environment": "test",
    }


async def test_health_rejects_non_get_methods(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/health")
    assert response.status_code == 405


async def test_health_is_not_served_outside_api_prefix(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 404


async def test_openapi_documents_health_endpoint(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["version"] == __version__
    operation = schema["paths"]["/api/v1/health"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HealthResponse"
    }
