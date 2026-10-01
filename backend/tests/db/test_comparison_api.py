"""Comparison matrix over HTTP, using the orchestration test world."""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.db.test_orchestration_api import _world

pytestmark = [pytest.mark.db, pytest.mark.api]


async def test_compare_after_analysis(api: httpx.AsyncClient) -> None:
    w = await _world(api)
    url = f"/api/v1/workspaces/{w.ws}/compare"
    names = {pid: name for name, pid in w.ids.items()}

    before = (await api.post(url, headers=w.user.headers)).json()["output"]
    assert before["winner_product_id"] is None  # no research yet: every MUST is unknown
    assert {p["unknown_hard"][0] for p in before["products"]} == {"ram_gb"}

    assert (await api.post(f"/api/v1/workspaces/{w.ws}/analyze", headers=w.user.headers)).is_success
    response = await api.post(url, headers=w.user.headers)
    assert response.status_code == 201, response.text
    run = response.json()
    out = run["output"]
    assert (run["agent"], run["product_id"]) == ("comparison", None)
    assert [c["key"] for c in out["criteria"]] == ["ram_gb", "has_usb_c", "price"]
    ranked = [(names[p["product_id"]], p["eligible"]) for p in out["products"]]
    assert ranked == [("Good", True), ("Pricey", False), ("Weak", False)]
    pricey = next(p for p in out["products"] if names[p["product_id"]] == "Pricey")
    assert pricey["violations"] == ["price"]
    assert out["winner_product_id"] == w.ids["Good"]
    assert set(out["source_runs"][w.ids["Good"]]) == {"research", "value"}

    soft = (
        await api.post(
            url, json={"budget_is_hard": False, "weights": {"price": 0}}, headers=w.user.headers
        )
    ).json()["output"]
    assert soft["winner_product_id"] == w.ids["Pricey"]  # 32 GB wins once price is ignored

    subset = (
        await api.post(url, json={"product_ids": [w.ids["Weak"]]}, headers=w.user.headers)
    ).json()["output"]
    assert [p["product_id"] for p in subset["products"]] == [w.ids["Weak"]]


async def test_validation_and_errors(api: httpx.AsyncClient) -> None:
    w = await _world(api)
    url = f"/api/v1/workspaces/{w.ws}/compare"
    bad = await api.post(url, json={"weights": {"ram_gb": 99}}, headers=w.user.headers)
    assert bad.status_code == 422
    unknown = await api.post(url, json={"product_ids": [str(uuid.uuid4())]}, headers=w.user.headers)
    assert unknown.json()["error"]["code"] == "workspace_product_not_found"
    empty_ws = (
        await api.post("/api/v1/workspaces", json={"name": "Empty"}, headers=w.user.headers)
    ).json()["id"]
    nothing = await api.post(f"/api/v1/workspaces/{empty_ws}/compare", headers=w.user.headers)
    assert nothing.json()["error"]["code"] == "nothing_to_compare"
