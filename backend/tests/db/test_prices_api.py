"""Price history over HTTP, and the Value Agent preferring fresh snapshots."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from tests.db.conftest import ApiUser, register_user

pytestmark = [pytest.mark.db, pytest.mark.api]

NOW = datetime.now(UTC).replace(microsecond=0)


async def _product(api: httpx.AsyncClient, user: ApiUser) -> str:
    pid: str = (
        await api.post(
            "/api/v1/products",
            json={"brand": f"Acme-{uuid.uuid4().hex[:6]}", "name": "L14", "category": "laptop"},
            headers=user.headers,
        )
    ).json()["id"]
    return pid


def _obs(retailer: str, amount: str, days_ago: float, **extra: Any) -> dict[str, Any]:
    return {
        "retailer": retailer,
        "amount": amount,
        "currency": "USD",
        "observed_at": (NOW - timedelta(days=days_ago)).isoformat(),
        **extra,
    }


async def test_record_is_idempotent_and_history_is_bucketed(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    pid = await _product(api, user)
    url = f"/api/v1/products/{pid}/prices"
    batch = {
        "observations": [
            _obs("Shop A", "1499.00", 40),
            _obs("Shop A", "1399.00", 10),
            _obs("Shop A", "1349.00", 10.01),
            _obs("Shop B", "1299.00", 1, in_stock=True),
            {**_obs("Shop C", "99.00", 2), "currency": "EUR"},
        ]
    }
    first = await api.post(url, json=batch, headers=user.headers)
    assert first.status_code == 201, first.text
    assert first.json() == {"received": 5, "inserted": 5, "duplicates": 0}
    again = (await api.post(url, json=batch, headers=user.headers)).json()
    assert again == {"received": 5, "inserted": 0, "duplicates": 5}

    history = (await api.get(url, headers=user.headers)).json()
    assert history["currency"] == "USD"  # most frequent currency by default
    series = {s["retailer"]: s["points"] for s in history["series"]}
    assert set(series) == {"Shop A", "Shop B"}
    assert len(series["Shop A"]) == 2  # two observations on the same day share a bucket
    assert sorted(p["low"] for p in series["Shop A"]) == ["1349.00", "1499.00"]
    stats = history["stats"]
    assert stats["lowest_current"]["retailer"] == "Shop B"
    assert stats["all_time_low"] == "1299.00"
    assert stats["lowest_in_window"] is True
    assert stats["change_pct"] == round((1299 - 1499) / 1499 * 100, 2)

    eur = (await api.get(f"{url}?currency=EUR&bucket=month", headers=user.headers)).json()
    assert eur["stats"]["observations"] == 1
    since = (NOW - timedelta(days=5)).isoformat().replace("+00:00", "Z")
    recent = (await api.get(url, params={"since": since}, headers=user.headers)).json()
    assert [s["retailer"] for s in recent["series"]] == ["Shop B"]


async def test_errors(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    missing = await api.get(f"/api/v1/products/{uuid.uuid4()}/prices", headers=user.headers)
    assert missing.json()["error"]["code"] == "product_not_found"
    pid = await _product(api, user)
    url = f"/api/v1/products/{pid}/prices"
    assert (await api.post(url, json={"observations": []}, headers=user.headers)).status_code == 422
    assert (await api.get(f"{url}?bucket=year", headers=user.headers)).status_code == 422
    assert (await api.get(url)).status_code == 401


async def test_value_agent_prefers_fresh_snapshots(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    pid = await _product(api, user)
    ws = (await api.post("/api/v1/workspaces", json={"name": "W"}, headers=user.headers)).json()[
        "id"
    ]
    await api.post(
        f"/api/v1/workspaces/{ws}/products", json={"product_id": pid}, headers=user.headers
    )
    await api.put(
        f"/api/v1/workspaces/{ws}/requirements",
        json={"expected_version": 0, "text": "Laptop under $1,500."},
        headers=user.headers,
    )
    await api.post(
        f"/api/v1/products/{pid}/prices",
        json={
            "observations": [
                _obs("Old Shop", "999.00", 90),
                _obs("Shop A", "1399.00", 3),
                _obs("Shop B", "1199.00", 2, in_stock=False),
                _obs("Shop B", "1249.00", 1, in_stock=False),
            ]
        },
        headers=user.headers,
    )
    out = (
        await api.post(f"/api/v1/workspaces/{ws}/products/{pid}/value", headers=user.headers)
    ).json()["output"]
    price = out["price"]
    assert (price["source"], price["retailer"], price["amount"]) == (
        "price_history",
        "Shop A",
        "1399.00",
    )
    assert out["budget_fit"] == "WITHIN"
