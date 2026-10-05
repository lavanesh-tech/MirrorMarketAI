"""Request correlation, access logging, safe errors and CORS, tested over HTTP."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.api.deps import get_db_session
from app.core.logging import JsonFormatter, RequestIdFilter
from app.main import create_app
from app.telemetry import metrics
from tests.conftest import SettingsFactory

pytestmark = pytest.mark.api

_UUID_HEX = re.compile(r"^[0-9a-f]{32}$")


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[dict[str, object]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


@pytest.fixture
def access_logs() -> Iterator[list[dict[str, object]]]:
    """Capture structured lines from the access logger exactly as they'd be emitted."""
    handler = _ListHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdFilter())
    logger = logging.getLogger("app.access")
    logger.addHandler(handler)
    try:
        yield handler.lines
    finally:
        logger.removeHandler(handler)


async def test_request_id_is_generated_when_absent(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert _UUID_HEX.fullmatch(response.headers["x-request-id"])


async def test_each_request_gets_a_distinct_id(client: httpx.AsyncClient) -> None:
    first = await client.get("/api/v1/health")
    second = await client.get("/api/v1/health")
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_well_formed_request_id_is_propagated(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "upstream-trace-42"})
    assert response.headers["x-request-id"] == "upstream-trace-42"


async def test_malicious_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "x" * 500})
    assert _UUID_HEX.fullmatch(response.headers["x-request-id"])


async def test_request_id_is_present_on_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert "x-request-id" in response.headers


async def test_access_log_line_is_structured(
    client: httpx.AsyncClient, access_logs: list[dict[str, object]]
) -> None:
    response = await client.get(
        "/api/v1/health?token=should-not-be-logged",
        headers={"X-Request-ID": "trace-for-logging"},
    )

    [line] = access_logs
    assert line["message"] == "request completed"
    assert line["request_id"] == response.headers["x-request-id"] == "trace-for-logging"
    assert line["http_method"] == "GET"
    assert line["http_path"] == "/api/v1/health"
    assert line["http_status"] == 200
    assert isinstance(line["duration_ms"], float)
    assert "should-not-be-logged" not in json.dumps(line)


@pytest.fixture
async def failing_client(make_settings: SettingsFactory) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(make_settings())

    @app.get("/api/v1/_boom")
    async def boom() -> None:
        raise RuntimeError("database password is hunter2")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_unhandled_exception_returns_safe_500(
    failing_client: httpx.AsyncClient, access_logs: list[dict[str, object]]
) -> None:
    response = await failing_client.get("/api/v1/_boom")

    assert response.status_code == 500
    request_id = response.headers["x-request-id"]
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred.",
            "request_id": request_id,
        }
    }
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text

    # The exception is logged server-side with the same request ID.
    error_line = next(line for line in access_logs if line["level"] == "ERROR")
    assert error_line["request_id"] == request_id
    assert "RuntimeError" in str(error_line["exception"])
    completed = next(line for line in access_logs if line["message"] == "request completed")
    assert completed["http_status"] == 500


async def _preflight(app: FastAPI, origin: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        return await c.options(
            "/api/v1/health",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )


async def test_cors_allows_configured_origin(make_settings: SettingsFactory) -> None:
    app = create_app(make_settings(cors_allowed_origins=["http://localhost:3000"]))
    response = await _preflight(app, "http://localhost:3000")
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "x-request-id" in response.headers


async def test_cors_rejects_unlisted_origin(make_settings: SettingsFactory) -> None:
    app = create_app(make_settings(cors_allowed_origins=["http://localhost:3000"]))
    response = await _preflight(app, "https://evil.example")
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


async def test_cors_disabled_when_no_origins_configured(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"Origin": "http://localhost:3000"})
    assert "access-control-allow-origin" not in response.headers


async def test_database_pool_exhaustion_is_a_503_not_a_crash(
    app: FastAPI, client: httpx.AsyncClient, access_logs: list[dict[str, object]]
) -> None:
    """Overload must tell clients to back off; it is not an internal error."""

    async def _no_connection_free() -> None:
        raise PoolTimeoutError("QueuePool limit of size 5 overflow 5 reached")

    app.dependency_overrides[get_db_session] = _no_connection_free
    before = metrics.REGISTRY.get_sample_value("mm_db_pool_timeouts_total") or 0.0
    response = await client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "correct-horse-battery"}
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    error = response.json()["error"]
    assert error["code"] == "overloaded"
    assert error["request_id"] == response.headers["x-request-id"]
    assert "QueuePool" not in response.text
    assert metrics.REGISTRY.get_sample_value("mm_db_pool_timeouts_total") == before + 1
    assert not any(
        line["message"] == "unhandled exception while processing request" for line in access_logs
    )
