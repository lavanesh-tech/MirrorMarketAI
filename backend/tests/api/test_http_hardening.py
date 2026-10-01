"""Security headers and the request body limit (no database needed)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI, Request

from app.core.http_hardening import BodySizeLimitMiddleware
from app.main import create_app
from tests.conftest import SettingsFactory

pytestmark = pytest.mark.api


async def get(app: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        return await client.get(path)


async def test_security_headers_on_api_responses(make_settings: SettingsFactory) -> None:
    response = await get(create_app(make_settings()), "/api/v1/health")
    headers = response.headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"
    assert "default-src 'none'" in headers["content-security-policy"]
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "strict-transport-security" not in headers  # plain HTTP in local/test
    missing = await get(create_app(make_settings()), "/nope")
    assert (missing.status_code, missing.headers["x-frame-options"]) == (404, "DENY")


async def test_docs_page_is_exempt_from_the_api_csp(make_settings: SettingsFactory) -> None:
    response = await get(create_app(make_settings()), "/api/v1/docs")
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_hsts_outside_local_and_headers_can_be_disabled(
    make_settings: SettingsFactory,
) -> None:
    staging = make_settings(app_env="staging", jwt_secret_key="s" * 48)
    response = await get(create_app(staging), "/api/v1/health")
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    off = await get(create_app(make_settings(security_headers_enabled=False)), "/api/v1/health")
    assert "x-frame-options" not in off.headers


def _echo_app(max_bytes: int) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=max_bytes)

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    return app


async def test_body_limit_by_content_length_and_by_streaming() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_echo_app(100)), base_url="http://t"
    ) as client:
        assert (await client.post("/echo", content=b"x" * 100)).json() == {"bytes": 100}
        declared = await client.post("/echo", content=b"x" * 101)
        assert declared.status_code == 413
        assert declared.json()["error"]["code"] == "request_too_large"

        async def chunks() -> AsyncIterator[bytes]:  # no Content-Length header
            for _ in range(5):
                yield b"y" * 30

        streamed = await client.post("/echo", content=chunks())
        assert "content-length" not in streamed.request.headers
        assert streamed.status_code == 413


async def test_real_app_rejects_oversized_requests(make_settings: SettingsFactory) -> None:
    app = create_app(make_settings(max_request_body_bytes=2048, ingestion_max_bytes=1024))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post("/api/v1/auth/login", content=b"{" + b" " * 4096 + b"}")
    assert response.status_code == 413
    assert response.json()["error"]["request_id"]
    assert response.headers["x-frame-options"] == "DENY"
