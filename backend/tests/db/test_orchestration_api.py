"""Bounded orchestration over HTTP: ranking, failure isolation, budgets, access."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_llm
from app.domain.roles import WorkspaceRole
from app.providers.llm import OpenAIChatClient
from app.services.agents import AgentService
from app.services.workspaces import WorkspaceService
from tests.conftest import SettingsFactory
from tests.db.conftest import ApiUser, register_user

pytestmark = [pytest.mark.db, pytest.mark.api]

BRIEF = "Laptop under $1,500 with at least 16 GB RAM. I have a USB-C dock."
PRODUCTS: dict[str, list[tuple[str, str]]] = {
    "Good": [
        ("Memory is 16 GB LPDDR5. Two USB-C ports. It retails for $1,199.", "MANUFACTURER_PAGE"),
        ("The battery is excellent and the screen is bright.", "REVIEW"),
    ],
    "Weak": [
        ("Memory is 8 GB, soldered. One USB-C port. It retails for $899.", "MANUFACTURER_PAGE"),
    ],
    "Pricey": [
        ("Memory is 32 GB. Thunderbolt USB-C ports. It retails for $2,499.", "MANUFACTURER_PAGE"),
    ],
}


class World:
    def __init__(self, user: ApiUser, ws: str, ids: dict[str, str]) -> None:
        self.user, self.ws, self.ids = user, ws, ids

    @property
    def analyze(self) -> str:
        return f"/api/v1/workspaces/{self.ws}/analyze"


async def _world(api: httpx.AsyncClient) -> World:
    user = await register_user(api)
    ws = (await api.post("/api/v1/workspaces", json={"name": "W"}, headers=user.headers)).json()[
        "id"
    ]
    ids: dict[str, str] = {}
    for name, docs in PRODUCTS.items():
        pid = (
            await api.post(
                "/api/v1/products",
                json={"brand": f"{name}-{uuid.uuid4().hex[:6]}", "name": "X", "category": "laptop"},
                headers=user.headers,
            )
        ).json()["id"]
        ids[name] = pid
        await api.post(
            f"/api/v1/workspaces/{ws}/products", json={"product_id": pid}, headers=user.headers
        )
        for text, kind in docs:
            upload = await api.post(
                f"/api/v1/products/{pid}/sources/upload",
                files={"file": ("d.txt", text.encode(), "text/plain")},
                data={"source_type": kind},
                headers=user.headers,
            )
            sid = upload.json()["source"]["id"]
            assert (await api.post(f"/api/v1/sources/{sid}/embed", headers=user.headers)).is_success
    saved = await api.put(
        f"/api/v1/workspaces/{ws}/requirements",
        json={"expected_version": 0, "text": BRIEF},
        headers=user.headers,
    )
    assert saved.status_code == 201
    return World(user, ws, ids)


def _product(out: dict[str, Any], pid: str) -> dict[str, Any]:
    return next(p for p in out["products"] if p["product_id"] == pid)


async def test_analyze_runs_pipeline_and_ranks(api: httpx.AsyncClient) -> None:
    w = await _world(api)
    response = await api.post(w.analyze, headers=w.user.headers)
    assert response.status_code == 201, response.text
    run = response.json()
    assert (run["agent"], run["product_id"]) == ("orchestration", None)
    out = run["output"]
    names = {pid: name for name, pid in w.ids.items()}
    verdicts = {names[s["product_id"]]: s["verdict"] for s in out["ranking"]}
    assert verdicts == {"Good": "RECOMMENDED", "Pricey": "CONSIDER", "Weak": "NOT_RECOMMENDED"}
    assert [names[s["product_id"]] for s in out["ranking"]] == ["Good", "Pricey", "Weak"]
    assert out["recommended_product_id"] == w.ids["Good"]
    good_steps = {s["agent"]: s["status"] for s in _product(out, w.ids["Good"])["steps"]}
    assert good_steps == {
        "product_research": "SUCCEEDED",
        "review_intelligence": "SUCCEEDED",
        "compatibility": "SUCCEEDED",
        "value": "SUCCEEDED",
        "risk": "SUCCEEDED",
        "synthesis": "SUCCEEDED",
    }
    assert (out["time_budget_exhausted"], out["token_budget_exhausted"]) == (False, False)
    weak = next(s for s in out["ranking"] if s["product_id"] == w.ids["Weak"])
    assert weak["blockers"] == ["product_research: required Memory not met"]

    single = await api.post(
        f"/api/v1/workspaces/{w.ws}/products/{w.ids['Good']}/synthesize", headers=w.user.headers
    )
    assert single.json()["output"]["verdict"] == "RECOMMENDED"


async def test_failing_agent_is_isolated(
    api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = await _world(api)

    async def boom(*_: object) -> None:
        raise RuntimeError("secret detail must not leak")

    monkeypatch.setattr(AgentService, "analyze_reviews", boom)
    out = (
        await api.post(w.analyze, json={"product_ids": [w.ids["Good"]]}, headers=w.user.headers)
    ).json()["output"]
    steps = {s["agent"]: s for s in _product(out, w.ids["Good"])["steps"]}
    assert steps["review_intelligence"]["status"] == "FAILED"
    assert steps["review_intelligence"]["reason"] == "RuntimeError"
    assert steps["risk"]["status"] == "SUCCEEDED"
    assert out["ranking"][0]["verdict"] == "RECOMMENDED"
    failed = (
        await api.get(
            f"/api/v1/workspaces/{w.ws}/agent-runs/{steps['review_intelligence']['run_id']}",
            headers=w.user.headers,
        )
    ).json()
    assert (failed["status"], failed["error"]) == ("FAILED", "RuntimeError")


async def test_time_budget_and_product_cap(
    api_app: FastAPI, api: httpx.AsyncClient, make_settings: SettingsFactory
) -> None:
    w = await _world(api)
    tight = make_settings(orchestration_time_budget_seconds=1e-9, orchestration_max_products=1)
    api_app.dependency_overrides[get_app_settings] = lambda: tight
    try:
        out = (await api.post(w.analyze, headers=w.user.headers)).json()["output"]
    finally:
        api_app.dependency_overrides.pop(get_app_settings)
    assert out["time_budget_exhausted"] is True
    assert len(out["products"]) == 1
    assert len(out["products_skipped"]) == 2
    steps = out["products"][0]["steps"]
    assert {s["status"] for s in steps[:-1]} == {"SKIPPED"}
    assert out["ranking"][0]["verdict"] == "INSUFFICIENT_DATA"


async def test_token_budget_switches_to_rules(
    api_app: FastAPI, api: httpx.AsyncClient, make_settings: SettingsFactory
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        name = json.loads(request.content)["response_format"]["json_schema"]["name"]
        content = {"facts": [], "summary": ""} if name == "product_research" else {"opinions": []}
        body = {
            "choices": [{"message": {"content": json.dumps(content)}}],
            "usage": {"total_tokens": 80},
        }
        return httpx.Response(200, json=body)

    settings = make_settings(agent_engine="openai", openai_api_key="sk-test-not-real")
    client = OpenAIChatClient(settings, transport=httpx.MockTransport(handler))
    budget = make_settings(orchestration_token_budget=50)
    api_app.dependency_overrides[get_llm] = lambda: client
    api_app.dependency_overrides[get_app_settings] = lambda: budget
    try:
        w = await _world(api)
        out = (
            await api.post(w.analyze, json={"product_ids": [w.ids["Good"]]}, headers=w.user.headers)
        ).json()["output"]
    finally:
        api_app.dependency_overrides.pop(get_llm)
        api_app.dependency_overrides.pop(get_app_settings)
    steps = {s["agent"]: s for s in out["products"][0]["steps"]}
    assert steps["product_research"]["engine"] == "openai:gpt-4.1-mini"
    assert steps["review_intelligence"]["engine"] == "rules-v1"
    assert (out["token_budget_exhausted"], out["tokens_used"]) == (True, 80)


async def test_access_and_unknown_products(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    w = await _world(api)
    viewer = await register_user(api)
    await WorkspaceService(db_session).add_member(
        uuid.UUID(w.ws), uuid.UUID(viewer.id), WorkspaceRole.VIEWER
    )
    assert (await api.post(w.analyze, headers=viewer.headers)).status_code == 403
    unknown = await api.post(
        w.analyze, json={"product_ids": [str(uuid.uuid4())]}, headers=w.user.headers
    )
    assert unknown.json()["error"]["code"] == "workspace_product_not_found"
