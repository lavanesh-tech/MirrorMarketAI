"""Evidence packs over HTTP: freeze, fetch, list, validate, access, stability."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.roles import WorkspaceRole
from app.models.retrieval import DocumentChunk
from app.models.sources import ProductSource
from app.services.workspaces import WorkspaceService
from tests.db.conftest import ApiUser, register_user

pytestmark = [pytest.mark.db, pytest.mark.api]

DOCS = [
    "The Acme L14 battery is rated 70 Wh and lasts up to 18 hours of video playback.",
    "Memory on the Acme L14 is 16 GB LPDDR5, soldered to the board and not upgradeable.",
]


async def _setup(api: httpx.AsyncClient) -> tuple[ApiUser, str, list[str]]:
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
    sources = []
    for text in DOCS:
        upload = await api.post(
            f"/api/v1/products/{pid}/sources/upload",
            files={"file": ("d.txt", text.encode(), "text/plain")},
            data={"source_type": "SPECIFICATION_SHEET"},
            headers=user.headers,
        )
        sid = upload.json()["source"]["id"]
        await api.post(f"/api/v1/sources/{sid}/embed", headers=user.headers)
        sources.append(sid)
    return user, ws, sources


def _url(ws: str, suffix: str = "") -> str:
    return f"/api/v1/workspaces/{ws}/evidence-packs{suffix}"


async def _pack(api: httpx.AsyncClient, user: ApiUser, ws: str, **body: Any) -> httpx.Response:
    return await api.post(_url(ws), json={"query": "battery memory", **body}, headers=user.headers)


async def test_create_get_list_and_validate(api: httpx.AsyncClient) -> None:
    user, ws, sources = await _setup(api)
    await api.put(
        f"/api/v1/workspaces/{ws}/requirements",
        json={"expected_version": 0, "text": "laptop with at least 16 GB RAM"},
        headers=user.headers,
    )
    created = await _pack(api, user, ws, limit=5)
    assert created.status_code == 201, created.text
    pack = created.json()
    assert pack["requirement_version"] == 1
    assert pack["embedding_model"] == "hashing-v1"
    assert [i["marker"] for i in pack["items"]] == ["E1", "E2"]
    assert {i["source_id"] for i in pack["items"]} == set(sources)
    assert all(i["authority"] == "USER" and len(i["content_hash"]) == 64 for i in pack["items"])

    fetched = (await api.get(_url(ws, f"/{pack['id']}"), headers=user.headers)).json()
    assert fetched == pack
    listed = (await api.get(_url(ws), headers=user.headers)).json()
    assert listed["page"]["total"] == 1
    assert listed["items"][0]["id"] == pack["id"]

    battery = next(i["marker"] for i in pack["items"] if "battery" in i["text"])
    good = await api.post(
        _url(ws, f"/{pack['id']}/validate"),
        json={"text": f"The battery lasts up to 18 hours of playback [{battery}]."},
        headers=user.headers,
    )
    assert good.json()["valid"] is True
    bad = (
        await api.post(
            _url(ws, f"/{pack['id']}/validate"),
            json={"text": f"The battery lasts up to 25 hours of playback [{battery}] [E9]."},
            headers=user.headers,
        )
    ).json()
    assert bad["valid"] is False
    assert {i["code"] for i in bad["issues"]} == {"unsupported_number", "unknown_citation"}


async def test_items_are_snapshots(api: httpx.AsyncClient, db_session: AsyncSession) -> None:
    user, ws, sources = await _setup(api)
    pack = (await _pack(api, user, ws)).json()
    source = await db_session.get(ProductSource, uuid.UUID(sources[0]))
    await db_session.delete(source)
    await db_session.flush()
    assert (
        await db_session.scalar(
            select(DocumentChunk).where(DocumentChunk.source_id == uuid.UUID(sources[0]))
        )
        is None
    )

    fetched = (await api.get(_url(ws, f"/{pack['id']}"), headers=user.headers)).json()
    assert len(fetched["items"]) == 2
    orphan = next(i for i in fetched["items"] if i["source_id"] is None)
    assert orphan["chunk_id"] is None
    assert orphan["text"] in DOCS


async def test_errors_and_access(api: httpx.AsyncClient, db_session: AsyncSession) -> None:
    user, ws, _ = await _setup(api)
    none = await _pack(api, user, ws, query="zeppelin", mode="lexical")
    assert none.status_code == 422
    assert none.json()["error"]["code"] == "no_evidence_found"
    assert (await _pack(api, user, ws, limit=21)).status_code == 422
    missing = await api.get(_url(ws, f"/{uuid.uuid4()}"), headers=user.headers)
    assert missing.json()["error"]["code"] == "evidence_pack_not_found"

    pack = (await _pack(api, user, ws)).json()
    viewer = await register_user(api)
    await WorkspaceService(db_session).add_member(
        uuid.UUID(ws), uuid.UUID(viewer.id), WorkspaceRole.VIEWER
    )
    assert (await _pack(api, viewer, ws)).status_code == 403
    assert (await api.get(_url(ws, f"/{pack['id']}"), headers=viewer.headers)).status_code == 200
    validated = await api.post(
        _url(ws, f"/{pack['id']}/validate"), json={"text": "x"}, headers=viewer.headers
    )
    assert validated.status_code == 200

    outsider = await register_user(api)
    for response in (
        await api.get(_url(ws, f"/{pack['id']}"), headers=outsider.headers),
        await api.get(_url(ws), headers=outsider.headers),
        await _pack(api, outsider, ws),
    ):
        assert response.status_code == 404

    other_ws = (
        await api.post("/api/v1/workspaces", json={"name": "Other"}, headers=user.headers)
    ).json()["id"]
    cross = await api.get(_url(other_ws, f"/{pack['id']}"), headers=user.headers)
    assert cross.json()["error"]["code"] == "evidence_pack_not_found"
