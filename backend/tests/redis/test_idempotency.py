from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.coordination.idempotency import IdempotencyMiddleware

pytestmark = pytest.mark.redis


MakeClient = Callable[[FastAPI], httpx.AsyncClient]


class BoomError(Exception):
    pass


def build_app(redis: Redis | None, max_body_bytes: int = 1024) -> tuple[FastAPI, list[int]]:
    calls: list[int] = []
    app = FastAPI()
    app.state.redis = redis
    app.add_middleware(
        IdempotencyMiddleware,
        prefix="t:",
        ttl_seconds=60,
        lock_seconds=30,
        max_body_bytes=max_body_bytes,
    )

    @app.post("/things", status_code=201)
    async def create(request: Request) -> JSONResponse:
        calls.append(1)
        body = await request.json()
        return JSONResponse(
            {"n": len(calls), "echo": body}, status_code=201, headers={"Location": "/things/1"}
        )

    @app.post("/fail")
    async def fail() -> JSONResponse:
        calls.append(1)
        return JSONResponse({"error": "x"}, status_code=503)

    @app.post("/explode")
    async def explode() -> None:
        calls.append(1)
        raise BoomError

    @app.post("/bad")
    async def bad() -> JSONResponse:
        calls.append(1)
        return JSONResponse({"error": "invalid"}, status_code=422)

    @app.get("/things")
    async def read() -> dict[str, int]:
        calls.append(1)
        return {"n": len(calls)}

    return app, calls


@pytest.fixture
async def make_client() -> AsyncIterator[MakeClient]:
    clients: list[httpx.AsyncClient] = []

    def _make(app: FastAPI) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://t",
        )
        clients.append(client)
        return client

    yield _make
    for client in clients:
        await client.aclose()


def key(value: str, auth: str = "Bearer a") -> dict[str, str]:
    return {"Idempotency-Key": value, "Authorization": auth}


async def test_retry_replays_stored_response_without_rerunning(
    redis: Redis, make_client: MakeClient
) -> None:
    app, calls = build_app(redis)
    client = make_client(app)
    first = await client.post("/things", json={"a": 1}, headers=key("k1"))
    again = await client.post("/things", json={"a": 1}, headers=key("k1"))
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json() == {"n": 1, "echo": {"a": 1}}
    assert again.headers["location"] == "/things/1"
    assert again.headers["idempotent-replayed"] == "true"
    assert "idempotent-replayed" not in first.headers
    assert len(calls) == 1


async def test_same_key_different_body_is_rejected(redis: Redis, make_client: MakeClient) -> None:
    app, calls = build_app(redis)
    client = make_client(app)
    await client.post("/things", json={"a": 1}, headers=key("k1"))
    reused = await client.post("/things", json={"a": 2}, headers=key("k1"))
    assert reused.status_code == 422
    assert reused.json()["error"]["code"] == "idempotency_key_reused"
    assert len(calls) == 1


async def test_keys_are_scoped_per_caller_and_path(redis: Redis, make_client: MakeClient) -> None:
    app, calls = build_app(redis)
    client = make_client(app)
    await client.post("/things", json={"a": 1}, headers=key("k1", "Bearer alice"))
    other = await client.post("/things", json={"a": 1}, headers=key("k1", "Bearer bob"))
    assert "idempotent-replayed" not in other.headers
    await client.post("/bad", headers=key("k1", "Bearer alice"))
    assert len(calls) == 3


async def test_concurrent_duplicate_gets_409(redis: Redis, make_client: MakeClient) -> None:
    app, calls = build_app(redis)
    client = make_client(app)
    await client.post("/things", json={"a": 1}, headers=key("k1"))
    (stored,) = await redis.keys("t:idem:*")
    record = json.loads(await redis.get(stored) or b"{}")
    await redis.set(stored, json.dumps({"state": "processing", "fp": record["fp"]}))
    busy = await client.post("/things", json={"a": 1}, headers=key("k1"))
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "idempotency_in_progress"
    assert busy.headers["retry-after"] == "1"
    assert len(calls) == 1


async def test_client_errors_are_stored_server_errors_are_not(
    redis: Redis, make_client: MakeClient
) -> None:
    app, calls = build_app(redis)
    client = make_client(app)
    for _ in range(2):
        assert (await client.post("/bad", headers=key("k-bad"))).status_code == 422
    for _ in range(2):
        assert (await client.post("/fail", headers=key("k-fail"))).status_code == 503
    assert len(calls) == 3  # /bad once (replayed), /fail twice (released)


async def test_exception_releases_the_key(redis: Redis, make_client: MakeClient) -> None:
    app, calls = build_app(redis)
    client = make_client(app)
    for _ in range(2):
        assert (await client.post("/explode", headers=key("k1"))).status_code == 500
    assert len(calls) == 2
    assert await redis.keys("t:idem:*") == []


async def test_stored_record_expires(redis: Redis, make_client: MakeClient) -> None:
    app, _ = build_app(redis)
    await make_client(app).post("/things", json={"a": 1}, headers=key("k1"))
    (stored,) = await redis.keys("t:idem:*")
    assert 30 < await redis.ttl(stored) <= 60


@pytest.mark.parametrize("bad_key", ["", "x" * 256, "has space", "ünï"])
async def test_invalid_key_is_400(redis: Redis, make_client: MakeClient, bad_key: str) -> None:
    app, calls = build_app(redis)
    response = await make_client(app).post(
        "/things", json={}, headers={b"Idempotency-Key": bad_key.encode()}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_idempotency_key"
    assert calls == []


async def test_body_over_limit_is_413(redis: Redis, make_client: MakeClient) -> None:
    app, calls = build_app(redis, max_body_bytes=1024)
    response = await make_client(app).post("/things", json={"a": "x" * 2000}, headers=key("k1"))
    assert response.status_code == 413
    assert calls == []


async def test_passthrough_cases(redis: Redis, dead_redis: Redis, make_client: MakeClient) -> None:
    for backend in (redis, None, dead_redis):
        app, calls = build_app(backend)
        client = make_client(app)
        for _ in range(2):
            await client.post("/things", json={"a": 1})  # no header
            await client.get("/things", headers=key("k-get"))  # safe method
        if backend is not redis:
            for _ in range(2):
                await client.post("/things", json={"a": 1}, headers=key("k1"))
            assert len(calls) == 6
        else:
            assert len(calls) == 4
