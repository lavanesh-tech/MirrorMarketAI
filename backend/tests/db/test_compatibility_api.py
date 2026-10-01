"""Compatibility Agent over HTTP."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.api.deps import get_llm
from app.providers.llm import OpenAIChatClient
from tests.conftest import SettingsFactory
from tests.db.conftest import ApiUser, register_user

pytestmark = [pytest.mark.db, pytest.mark.api]

DOCS = [
    "Ports include two Thunderbolt 4 USB-C connectors and a headphone jack.",
    "There is no HDMI port, so TVs need an adapter.",
    "Ships with Windows 11 Home and Wi-Fi 7.",
]


async def _setup(api: httpx.AsyncClient, brief: str | None = None) -> tuple[ApiUser, str, str]:
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


def _url(ws: str, pid: str) -> str:
    return f"/api/v1/workspaces/{ws}/products/{pid}/compatibility"


async def test_uses_requirement_owned_devices(api: httpx.AsyncClient) -> None:
    user, ws, pid = await _setup(
        api, "Laptop. I have a USB-C dock and wired headphones. Must work with my Sony TV."
    )
    response = await api.post(_url(ws, pid), headers=user.headers)
    assert response.status_code == 201, response.text
    run = response.json()
    assert (run["agent"], run["requirement_version"]) == ("compatibility", 1)
    out = run["output"]
    checks = {c["capability"]: c for c in out["checks"]}
    assert checks["usb_c"]["support"] == "SUPPORTED"
    assert checks["headphone_jack"]["support"] == "SUPPORTED"
    assert checks["hdmi"]["support"] == "NOT_SUPPORTED"
    assert checks["hdmi"]["devices"] == ["Sony TV"]
    assert out["verdict"] == "INCOMPATIBLE"
    assert run["validation"]["valid"] is True


async def test_request_body_overrides_and_unknowns(api: httpx.AsyncClient) -> None:
    user, ws, pid = await _setup(api)
    run = (
        await api.post(
            _url(ws, pid),
            json={"owned_devices": ["Windows PC games", "Wi-Fi 7 router", "Ethernet cable"]},
            headers=user.headers,
        )
    ).json()
    out = run["output"]
    checks = {c["capability"]: c["support"] for c in out["checks"]}
    assert checks == {"windows": "SUPPORTED", "wifi": "SUPPORTED", "ethernet": "UNKNOWN"}
    assert out["verdict"] == "UNCERTAIN"


async def test_nothing_to_check_is_422(api: httpx.AsyncClient) -> None:
    user, ws, pid = await _setup(api)
    response = await api.post(_url(ws, pid), headers=user.headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_compatibility_targets"


async def test_llm_engine_and_failure(
    api_app: FastAPI, api: httpx.AsyncClient, make_settings: SettingsFactory
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["messages"][1]["content"]
        line = next(line for line in prompt.splitlines() if "no HDMI" in line)
        marker = line.split("]")[0][1:]
        checks = [
            {
                "capability": "hdmi",
                "support": "NOT_SUPPORTED",
                "marker": marker,
                "quote": "There is no HDMI port, so TVs need an adapter.",
            },
            {
                "capability": "usb_c",
                "support": "SUPPORTED",
                "marker": marker,
                "quote": "USB-C everywhere.",
            },
        ]
        body = {"choices": [{"message": {"content": json.dumps({"checks": checks})}}]}
        return httpx.Response(200, json=body)

    settings = make_settings(agent_engine="openai", openai_api_key="sk-test-not-real")
    good = OpenAIChatClient(settings, transport=httpx.MockTransport(handler))
    broken = OpenAIChatClient(
        settings, transport=httpx.MockTransport(lambda _: httpx.Response(400))
    )
    user, ws, pid = await _setup(api)
    body = {"owned_devices": ["USB-C dock", "TV"]}
    try:
        api_app.dependency_overrides[get_llm] = lambda: good
        run = (await api.post(_url(ws, pid), json=body, headers=user.headers)).json()
        api_app.dependency_overrides[get_llm] = lambda: broken
        fallback = (await api.post(_url(ws, pid), json=body, headers=user.headers)).json()
    finally:
        api_app.dependency_overrides.pop(get_llm)
    checks = {c["capability"]: c["support"] for c in run["output"]["checks"]}
    assert checks == {"usb_c": "UNKNOWN", "hdmi": "NOT_SUPPORTED"}
    assert run["engine"] == "openai:gpt-4.1-mini"
    assert (fallback["engine"], fallback["degraded"]) == ("rules-v1", True)
    assert {c["capability"]: c["support"] for c in fallback["output"]["checks"]}["usb_c"] == (
        "SUPPORTED"
    )
