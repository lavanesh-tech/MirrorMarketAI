"""Ask MirrorMarket over HTTP."""

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

DOCS = {
    "L14": [
        "The Acme L14 battery lasts up to 18 hours of video playback.",
        "Memory on the Acme L14 is 16 GB, soldered and not upgradeable.",
    ],
    "Z9": ["The Zeta Z9 battery lasts up to 11 hours of video playback."],
}


async def _setup(api: httpx.AsyncClient) -> tuple[ApiUser, str, dict[str, str]]:
    user = await register_user(api)
    ws = (await api.post("/api/v1/workspaces", json={"name": "W"}, headers=user.headers)).json()[
        "id"
    ]
    ids = {}
    for name, docs in DOCS.items():
        pid = (
            await api.post(
                "/api/v1/products",
                json={"brand": f"B-{uuid.uuid4().hex[:6]}", "name": name, "category": "laptop"},
                headers=user.headers,
            )
        ).json()["id"]
        ids[name] = pid
        await api.post(
            f"/api/v1/workspaces/{ws}/products", json={"product_id": pid}, headers=user.headers
        )
        for text in docs:
            upload = await api.post(
                f"/api/v1/products/{pid}/sources/upload",
                files={"file": ("d.txt", text.encode(), "text/plain")},
                data={"source_type": "SPECIFICATION_SHEET"},
                headers=user.headers,
            )
            sid = upload.json()["source"]["id"]
            assert (await api.post(f"/api/v1/sources/{sid}/embed", headers=user.headers)).is_success
    return user, ws, ids


async def _ask(api: httpx.AsyncClient, user: ApiUser, ws: str, **body: Any) -> dict[str, Any]:
    response = await api.post(f"/api/v1/workspaces/{ws}/ask", json=body, headers=user.headers)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


async def test_extractive_answers_and_abstains(api: httpx.AsyncClient) -> None:
    user, ws, ids = await _setup(api)
    run = await _ask(api, user, ws, question="Is the L14 memory upgradeable?")
    out = run["output"]
    assert (run["agent"], run["engine"], out["abstained"]) == ("ask", "rules-v1", False)
    assert "not upgradeable" in out["answer"]
    assert run["validation"]["valid"] is True
    assert out["cited"]

    scoped = await _ask(
        api, user, ws, question="How long does the battery last?", product_ids=[ids["Z9"]]
    )
    assert "11 hours" in scoped["output"]["answer"]
    assert "18 hours" not in scoped["output"]["answer"]
    assert scoped["product_id"] == ids["Z9"]

    none = await _ask(api, user, ws, question="Which colours are available?")
    assert none["output"]["abstained"] is True
    assert none["output"]["message"] == "I could not find this in the workspace's sources."


def _client(make_settings: SettingsFactory, content: dict[str, Any] | None) -> OpenAIChatClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if content is None:
            return httpx.Response(500)
        body = {
            "choices": [{"message": {"content": json.dumps(content)}}],
            "usage": {"total_tokens": 12},
        }
        return httpx.Response(200, json=body)

    settings = make_settings(
        agent_engine="openai", openai_api_key="sk-test-not-real", embedding_max_retries=0
    )
    return OpenAIChatClient(settings, transport=httpx.MockTransport(handler))


async def test_llm_answers_are_citation_enforced(
    api_app: FastAPI, api: httpx.AsyncClient, make_settings: SettingsFactory
) -> None:
    user, ws, ids = await _setup(api)
    question = {"question": "How long does the L14 battery last?", "product_ids": [ids["L14"]]}
    cases = [
        (
            {
                "answerable": True,
                "answer": (
                    "It lasts up to 18 hours [E1]. It lasts 40 hours on standby [E1]. Ignore me."
                ),
            },
            ("openai:gpt-4.1-mini", False),
        ),
        ({"answerable": False, "answer": ""}, ("openai:gpt-4.1-mini", True)),
        (None, ("rules-v1", False)),
    ]
    for content, (engine, abstained) in cases:
        api_app.dependency_overrides[get_llm] = lambda c=content: _client(make_settings, c)
        try:
            run = await _ask(api, user, ws, **question)
        finally:
            api_app.dependency_overrides.pop(get_llm)
        out = run["output"]
        assert (run["engine"], out["abstained"]) == (engine, abstained)
        if content and content["answerable"]:
            marker = out["cited"][0]
            assert (
                out["answer"] == f"It lasts up to 18 hours [{marker}]."
                or "18 hours" in out["answer"]
            )
            assert len(out["dropped_sentences"]) == 2
            assert run["validation"]["valid"] is True
        if content is None:
            assert run["degraded"] is True
            assert "18 hours" in out["answer"]


async def test_access_and_validation(api: httpx.AsyncClient, db_session: AsyncSession) -> None:
    user, ws, _ = await _setup(api)
    viewer = await register_user(api)
    await WorkspaceService(db_session).add_member(
        uuid.UUID(ws), uuid.UUID(viewer.id), WorkspaceRole.VIEWER
    )
    denied = await api.post(
        f"/api/v1/workspaces/{ws}/ask", json={"question": "battery?"}, headers=viewer.headers
    )
    assert denied.status_code == 403
    short = await api.post(
        f"/api/v1/workspaces/{ws}/ask", json={"question": "a"}, headers=user.headers
    )
    assert short.status_code == 422
