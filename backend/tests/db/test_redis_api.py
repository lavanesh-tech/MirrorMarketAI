"""Redis features through the real app: rate limits, price cache, idempotency, readiness."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.coordination.rate_limit import RateLimiter
from app.core.config import Settings
from app.main import create_app
from app.models.identity import ComparisonWorkspace
from tests.db.conftest import register_user

pytestmark = [pytest.mark.db, pytest.mark.api, pytest.mark.redis]

LIMIT = 3


@pytest.fixture
async def rapi(
    make_settings: Callable[..., Settings],
    migrated_database_url: str,
    redis_url: str,
    db_session: AsyncSession,
) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(
        make_settings(
            database_url=migrated_database_url,
            redis_url=redis_url,
            rate_limit_auth_per_minute=LIMIT,
            rate_limit_agents_per_minute=2,
        )
    )

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = _session
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield client


async def test_login_is_limited_per_ip_with_retry_after(rapi: httpx.AsyncClient) -> None:
    user = await register_user(rapi)  # 1 register + 1 login from this IP
    good = {"email": user.email, "password": "correct-horse-battery"}
    ok = await rapi.post("/api/v1/auth/login", json=good)
    assert ok.status_code == 200
    assert (ok.headers["x-ratelimit-limit"], ok.headers["x-ratelimit-remaining"]) == ("3", "1")
    bad = {"email": "nobody@example.com", "password": "wrong-password-1"}
    attempts = [await rapi.post("/api/v1/auth/login", json=bad) for _ in range(2)]
    assert [a.status_code for a in attempts] == [401, 429]
    blocked = attempts[1]
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(blocked.headers["retry-after"]) <= 20
    assert blocked.headers["x-ratelimit-limit"] == str(LIMIT)


async def test_login_is_limited_per_email_across_ips(
    redis_url: str, rapi: httpx.AsyncClient
) -> None:

    user = await register_user(rapi)
    async with Redis.from_url(redis_url) as client:
        limiter = RateLimiter(client, "mm:")
        for _ in range(LIMIT - 1):  # the same account guessed from other IPs
            await limiter.hit("auth:login-email", user.email, LIMIT, 60)
    good = {"email": user.email.upper(), "password": "correct-horse-battery"}
    response = await rapi.post("/api/v1/auth/login", json=good)
    assert response.status_code == 429


async def test_agent_runs_are_limited_per_user_reads_are_not(rapi: httpx.AsyncClient) -> None:
    user = await register_user(rapi)
    ws = (await rapi.post("/api/v1/workspaces", json={"name": "W"}, headers=user.headers)).json()
    compare = f"/api/v1/workspaces/{ws['id']}/compare"
    codes = [
        (await rapi.post(compare, json={}, headers=user.headers)).status_code for _ in range(3)
    ]
    assert codes == [422, 422, 429]
    runs = f"/api/v1/workspaces/{ws['id']}/agent-runs"
    for _ in range(3):
        assert (await rapi.get(runs, headers=user.headers)).status_code == 200


async def test_price_history_is_cached_and_invalidated_on_write(rapi: httpx.AsyncClient) -> None:
    user = await register_user(rapi)
    product = await rapi.post(
        "/api/v1/products",
        json={"brand": f"Acme-{uuid.uuid4().hex[:6]}", "name": "L14", "category": "laptop"},
        headers=user.headers,
    )
    url = f"/api/v1/products/{product.json()['id']}/prices"
    now = datetime.now(UTC).replace(microsecond=0)

    def batch(amount: str, days_ago: int) -> dict[str, object]:
        at = (now - timedelta(days=days_ago)).isoformat()
        return {
            "observations": [
                {"retailer": "A", "amount": amount, "currency": "USD", "observed_at": at}
            ]
        }

    await rapi.post(url, json=batch("100.00", 2), headers=user.headers)
    first = await rapi.get(url, headers=user.headers)
    second = await rapi.get(url, headers=user.headers)
    assert (first.headers["x-cache"], second.headers["x-cache"]) == ("MISS", "HIT")
    assert first.json() == second.json()
    assert (await rapi.get(url, params={"bucket": "week"}, headers=user.headers)).headers[
        "x-cache"
    ] == "MISS"

    duplicate = await rapi.post(url, json=batch("100.00", 2), headers=user.headers)
    assert duplicate.json()["inserted"] == 0
    assert (await rapi.get(url, headers=user.headers)).headers["x-cache"] == "HIT"

    await rapi.post(url, json=batch("80.00", 1), headers=user.headers)
    fresh = await rapi.get(url, headers=user.headers)
    assert fresh.headers["x-cache"] == "MISS"
    assert fresh.json()["stats"]["lowest_current"]["amount"] == "80.00"


async def test_idempotent_workspace_creation(
    rapi: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await register_user(rapi)
    headers = {**user.headers, "Idempotency-Key": "create-ws-1"}
    first = await rapi.post("/api/v1/workspaces", json={"name": "Trip"}, headers=headers)
    retry = await rapi.post("/api/v1/workspaces", json={"name": "Trip"}, headers=headers)
    assert first.status_code == retry.status_code == 201
    assert retry.json()["id"] == first.json()["id"]
    assert retry.headers["idempotent-replayed"] == "true"
    assert retry.headers["x-request-id"] != first.headers["x-request-id"]
    count = await db_session.scalar(
        select(func.count())
        .select_from(ComparisonWorkspace)
        .where(ComparisonWorkspace.name == "Trip")
    )
    assert count == 1


async def test_readiness_reports_redis_without_gating(rapi: httpx.AsyncClient) -> None:
    body = (await rapi.get("/api/v1/ready")).json()
    assert body["status"] == "ready"
    assert body["checks"]["redis"]["status"] == "ok"
    assert body["checks"]["redis"]["required"] is False
