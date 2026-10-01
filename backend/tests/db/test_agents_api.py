"""Product Research Agent over HTTP: rules engine, LLM engine, degradation, access."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_llm
from app.domain.roles import WorkspaceRole
from app.providers.llm import OpenAIChatClient
from app.services.workspaces import WorkspaceService
from tests.conftest import SettingsFactory
from tests.db.conftest import ApiUser, register_user

pytestmark = [pytest.mark.db, pytest.mark.api]

DOCS = [
    "The Acme L14 battery lasts up to 18 hours of video playback.",
    "Memory on the Acme L14 is 16 GB LPDDR5, soldered to the board.",
    "The Acme L14 weighs 1.45 kg and has a 14 inch OLED display.",
    "Ports include two Thunderbolt 4 USB-C connectors and HDMI 2.1.",
]
BRIEF = (
    "Laptop with at least 16 GB RAM. Must weigh under 1.3 kg. Needs Thunderbolt. "
    "At least 1 TB storage. Battery 15 hours or more."
)


class Setup:
    def __init__(self, user: ApiUser, ws: str, pid: str) -> None:
        self.user, self.ws, self.pid = user, ws, pid

    @property
    def research(self) -> str:
        return f"/api/v1/workspaces/{self.ws}/products/{self.pid}/research"


async def _setup(api: httpx.AsyncClient, *, requirements: bool = True) -> Setup:
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
    for text in DOCS:
        upload = await api.post(
            f"/api/v1/products/{pid}/sources/upload",
            files={"file": ("d.txt", text.encode(), "text/plain")},
            data={"source_type": "SPECIFICATION_SHEET"},
            headers=user.headers,
        )
        await api.post(
            f"/api/v1/sources/{upload.json()['source']['id']}/embed", headers=user.headers
        )
    await api.put(
        f"/api/v1/products/{pid}/specifications",
        json={"specifications": [{"key": "storage_gb", "value_number": "512", "unit": "GB"}]},
        headers=user.headers,
    )
    if requirements:
        saved = await api.put(
            f"/api/v1/workspaces/{ws}/requirements",
            json={"expected_version": 0, "text": BRIEF},
            headers=user.headers,
        )
        assert saved.status_code == 201, saved.text
    return Setup(user, ws, pid)


def _facts(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["key"]: f for f in run["output"]["facts"]}


async def test_rules_engine_researches_requirements(api: httpx.AsyncClient) -> None:
    s = await _setup(api)
    response = await api.post(s.research, headers=s.user.headers)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["agent"] == "product_research"
    assert run["engine"] == "rules-v1"
    assert run["status"] == "SUCCEEDED"
    assert run["requirement_version"] == 1
    assert run["evidence_pack_id"] is not None

    facts = _facts(run)
    assert facts["ram_gb"]["status"] == "MET"
    assert facts["ram_gb"]["value_number"] == "16"
    assert facts["weight_kg"]["status"] == "UNMET"
    assert facts["weight_kg"]["value_number"] == "1.45"
    assert facts["has_thunderbolt"]["status"] == "MET"
    assert facts["storage_gb"] | {"citations": []} == facts["storage_gb"]
    assert (facts["storage_gb"]["source"], facts["storage_gb"]["status"]) == ("catalog", "UNMET")
    assert facts["battery_life_hours"]["status"] == "MET"
    assert run["output"]["found"] == run["output"]["total"] == 5

    assert run["validation"]["valid"] is True, run["validation"]
    pack = (
        await api.get(
            f"/api/v1/workspaces/{s.ws}/evidence-packs/{run['evidence_pack_id']}",
            headers=s.user.headers,
        )
    ).json()
    cited = facts["ram_gb"]["citations"][0]
    item = next(i for i in pack["items"] if i["marker"] == cited)
    assert "16 GB" in item["text"]

    listed = await api.get(
        f"/api/v1/workspaces/{s.ws}/agent-runs?agent=product_research&product_id={s.pid}",
        headers=s.user.headers,
    )
    assert listed.json()["page"]["total"] == 1
    fetched = await api.get(
        f"/api/v1/workspaces/{s.ws}/agent-runs/{run['id']}", headers=s.user.headers
    )
    assert fetched.json() == run


async def test_without_requirements_uses_category_defaults(api: httpx.AsyncClient) -> None:
    s = await _setup(api, requirements=False)
    run = (await api.post(s.research, headers=s.user.headers)).json()
    facts = _facts(run)
    assert list(facts) == [
        "ram_gb",
        "storage_gb",
        "battery_life_hours",
        "weight_kg",
        "screen_size_in",
    ]
    assert run["requirement_version"] is None
    assert {f["status"] for f in facts.values()} == {"NO_REQUIREMENT"}
    assert facts["screen_size_in"]["value_number"] == "14"


def _llm_override(api_app: FastAPI, make_settings: SettingsFactory, handler: Any) -> None:
    settings = make_settings(agent_engine="openai", openai_api_key="sk-test-not-real")
    client = OpenAIChatClient(settings, transport=httpx.MockTransport(handler))
    api_app.dependency_overrides[get_llm] = lambda: client


async def test_llm_engine_and_hallucination_guard(
    api_app: FastAPI, api: httpx.AsyncClient, make_settings: SettingsFactory
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["messages"][1]["content"]
        marker = next(line.split("]")[0][1:] for line in prompt.splitlines() if "16 GB" in line)
        content = {
            "facts": [
                {
                    "key": "ram_gb",
                    "value_number": 16,
                    "value_text": None,
                    "unit": "GB",
                    "citations": [marker],
                },
                {
                    "key": "weight_kg",
                    "value_number": 1.1,
                    "value_text": None,
                    "unit": "kg",
                    "citations": ["E99"],
                },
            ],
            "summary": f"Memory is 16 GB [{marker}]. It weighs only 1.1 kg [{marker}].",
        }
        body = {
            "choices": [{"message": {"content": json.dumps(content)}}],
            "usage": {"total_tokens": 77},
        }
        return httpx.Response(200, json=body)

    _llm_override(api_app, make_settings, handler)
    try:
        s = await _setup(api)
        run = (await api.post(s.research, headers=s.user.headers)).json()
    finally:
        api_app.dependency_overrides.pop(get_llm)
    assert run["engine"] == "openai:gpt-4.1-mini"
    assert run["tokens_used"] == 77
    facts = _facts(run)
    assert facts["ram_gb"]["status"] == "MET"
    assert facts["weight_kg"]["status"] == "UNKNOWN"  # its only citation did not exist
    assert run["validation"]["valid"] is False
    assert [i["code"] for i in run["validation"]["issues"]] == ["unsupported_number"]


async def test_llm_failure_degrades_to_rules(
    api_app: FastAPI, api: httpx.AsyncClient, make_settings: SettingsFactory
) -> None:
    _llm_override(api_app, make_settings, lambda _: httpx.Response(400))
    try:
        s = await _setup(api)
        run = (await api.post(s.research, headers=s.user.headers)).json()
    finally:
        api_app.dependency_overrides.pop(get_llm)
    assert (run["engine"], run["degraded"]) == ("rules-v1", True)
    assert _facts(run)["ram_gb"]["status"] == "MET"


async def test_no_evidence_still_reports_catalog_facts(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    pid = (
        await api.post(
            "/api/v1/products",
            json={"brand": f"Sono-{uuid.uuid4().hex[:6]}", "name": "H9", "category": "headphones"},
            headers=user.headers,
        )
    ).json()["id"]
    ws = (await api.post("/api/v1/workspaces", json={"name": "W"}, headers=user.headers)).json()[
        "id"
    ]
    await api.post(
        f"/api/v1/workspaces/{ws}/products", json={"product_id": pid}, headers=user.headers
    )
    await api.put(
        f"/api/v1/products/{pid}/specifications",
        json={"specifications": [{"key": "weight_kg", "value_number": "0.25", "unit": "kg"}]},
        headers=user.headers,
    )
    run = (
        await api.post(f"/api/v1/workspaces/{ws}/products/{pid}/research", headers=user.headers)
    ).json()
    assert run["evidence_pack_id"] is None
    assert _facts(run)["weight_kg"]["source"] == "catalog"
    assert run["output"]["summary"] == ""
    assert run["validation"]["valid"] is True


async def test_access_rules(api: httpx.AsyncClient, db_session: AsyncSession) -> None:
    s = await _setup(api, requirements=False)
    viewer = await register_user(api)
    await WorkspaceService(db_session).add_member(
        uuid.UUID(s.ws), uuid.UUID(viewer.id), WorkspaceRole.VIEWER
    )
    assert (await api.post(s.research, headers=viewer.headers)).status_code == 403
    outsider = await register_user(api)
    assert (await api.post(s.research, headers=outsider.headers)).status_code == 404

    stray = (
        await api.post(
            "/api/v1/products",
            json={"brand": f"X-{uuid.uuid4().hex[:6]}", "name": "Y", "category": "laptop"},
            headers=s.user.headers,
        )
    ).json()["id"]
    missing = await api.post(
        f"/api/v1/workspaces/{s.ws}/products/{stray}/research", headers=s.user.headers
    )
    assert missing.json()["error"]["code"] == "workspace_product_not_found"
    unknown = await api.get(
        f"/api/v1/workspaces/{s.ws}/agent-runs/{uuid.uuid4()}", headers=viewer.headers
    )
    assert unknown.json()["error"]["code"] == "agent_run_not_found"
