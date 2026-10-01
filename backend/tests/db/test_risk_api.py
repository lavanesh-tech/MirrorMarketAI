"""Risk Agent over HTTP, combining evidence with earlier agent runs."""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.db.conftest import register_user

pytestmark = [pytest.mark.db, pytest.mark.api]

DOCS = [
    ("Acme offers only a 90-day warranty on the L14.", "WARRANTY"),
    ("Mine died after three weeks. Support replaced it.", "REVIEW"),
    ("The hinge cracked within a month of light use.", "REVIEW"),
    ("Memory on the Acme L14 is 16 GB, soldered to the board.", "SPECIFICATION_SHEET"),
    ("There is no HDMI port. It retails for $1,299.", "MANUFACTURER_PAGE"),
]


async def test_risk_combines_evidence_and_agent_runs(api: httpx.AsyncClient) -> None:
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
    for text, kind in DOCS:
        upload = await api.post(
            f"/api/v1/products/{pid}/sources/upload",
            files={"file": ("d.txt", text.encode(), "text/plain")},
            data={"source_type": kind},
            headers=user.headers,
        )
        sid = upload.json()["source"]["id"]
        assert (await api.post(f"/api/v1/sources/{sid}/embed", headers=user.headers)).is_success
    await api.put(
        f"/api/v1/workspaces/{ws}/requirements",
        json={
            "expected_version": 0,
            "text": "Laptop under $1,000 with at least 32 GB RAM. Must work with my Sony TV.",
        },
        headers=user.headers,
    )
    base = f"/api/v1/workspaces/{ws}/products/{pid}"

    alone = (await api.post(f"{base}/risk", headers=user.headers)).json()["output"]
    assert {r["source"] for r in alone["risks"]} == {"evidence"}

    for action in ("research", "compatibility", "value"):
        assert (await api.post(f"{base}/{action}", headers=user.headers)).status_code == 201
    response = await api.post(f"{base}/risk", headers=user.headers)
    assert response.status_code == 201, response.text
    run = response.json()
    out = run["output"]
    assert (run["agent"], out["level"]) == ("risk", "HIGH")
    found = {(r["category"], r["severity"], r["source"]) for r in out["risks"]}
    assert ("warranty", "MEDIUM", "evidence") in found
    assert ("reliability", "HIGH", "evidence") in found
    assert ("repairability", "LOW", "evidence") in found
    assert ("compatibility", "HIGH", "compatibility") in found
    assert ("budget", "MEDIUM", "value") in found
    assert ("requirements", "HIGH", "product_research") in found
    assert run["validation"]["valid"] is True
    assert out["summary"].startswith("Reliability failure reported [E")
