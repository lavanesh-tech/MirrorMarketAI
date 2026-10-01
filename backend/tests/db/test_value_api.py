"""Value Agent over HTTP."""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.db.conftest import ApiUser, register_user

pytestmark = [pytest.mark.db, pytest.mark.api]

DOCS = [
    "Memory on the Acme L14 is 16 GB LPDDR5.",
    "The Acme L14 retails for $1,299 in the US, down from $1,499.",
]


async def _setup(
    api: httpx.AsyncClient, *, docs: list[str], brief: str | None
) -> tuple[ApiUser, str, str]:
    user = await register_user(api)
    pid = (
        await api.post(
            "/api/v1/products",
            json={"brand": f"Acme-{uuid.uuid4().hex[:6]}", "name": "L14", "category": "laptop"},
            headers=user.headers,
        )
    ).json()["id"]
    ws = (await api.post("/api/v1/workspaces", json={"name": "W"}, headers=user.headers)).json()[
        "id"
    ]
    await api.post(
        f"/api/v1/workspaces/{ws}/products", json={"product_id": pid}, headers=user.headers
    )
    for text in docs:
        upload = await api.post(
            f"/api/v1/products/{pid}/sources/upload",
            files={"file": ("d.txt", text.encode(), "text/plain")},
            data={"source_type": "MANUFACTURER_PAGE"},
            headers=user.headers,
        )
        sid = upload.json()["source"]["id"]
        assert (await api.post(f"/api/v1/sources/{sid}/embed", headers=user.headers)).is_success
    if brief:
        saved = await api.put(
            f"/api/v1/workspaces/{ws}/requirements",
            json={"expected_version": 0, "text": brief},
            headers=user.headers,
        )
        assert saved.status_code == 201
    return user, ws, pid


def _url(ws: str, pid: str, action: str = "value") -> str:
    return f"/api/v1/workspaces/{ws}/products/{pid}/{action}"


async def test_value_uses_evidence_price_budget_and_research(api: httpx.AsyncClient) -> None:
    user, ws, pid = await _setup(
        api, docs=DOCS, brief="Laptop under $1,500 with at least 16 GB RAM."
    )
    before = (await api.post(_url(ws, pid), headers=user.headers)).json()["output"]
    assert before["requirement_fit"] is None  # no research run yet

    research = (await api.post(_url(ws, pid, "research"), headers=user.headers)).json()
    response = await api.post(_url(ws, pid), headers=user.headers)
    assert response.status_code == 201, response.text
    run = response.json()
    out = run["output"]
    assert (run["agent"], run["engine"]) == ("value", "rules-v1")
    assert out["price"]["amount"] == "1299.00"
    assert out["price"]["source"] == "evidence"
    assert out["budget_fit"] == "WITHIN"
    assert out["requirement_fit"] == 1.0
    assert out["research_run_id"] == research["id"]
    assert out["value_index"] == round(1 / (1299 / 1500), 4)
    assert out["cost_per_unit"] == {"usd_per_ram_gb": "81.19"}
    assert out["summary"].startswith("Price: $1,299 [E")
    assert run["validation"]["valid"] is True


async def test_catalog_fallback_and_over_budget(api: httpx.AsyncClient) -> None:
    user, ws, pid = await _setup(api, docs=[DOCS[0]], brief="Laptop under $1,000.")
    await api.put(
        f"/api/v1/products/{pid}/specifications",
        json={"specifications": [{"key": "price", "value_number": "1099", "unit": "USD"}]},
        headers=user.headers,
    )
    out = (await api.post(_url(ws, pid), headers=user.headers)).json()["output"]
    assert out["price"]["source"] == "catalog"
    assert (out["budget_fit"], out["over_budget_by"]) == ("OVER", "99.00")
    assert out["summary"] == ""


async def test_no_price_and_no_budget(api: httpx.AsyncClient) -> None:
    user, ws, pid = await _setup(api, docs=[DOCS[0]], brief=None)
    out = (await api.post(_url(ws, pid), headers=user.headers)).json()["output"]
    assert out["price"] is None
    assert out["budget_fit"] == "NO_BUDGET"
    user2, ws2, pid2 = await _setup(api, docs=[DOCS[0]], brief="Laptop under £900.")
    out2 = (await api.post(_url(ws2, pid2), headers=user2.headers)).json()["output"]
    assert out2["budget_fit"] == "UNKNOWN_PRICE"
