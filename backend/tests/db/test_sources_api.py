"""Sources, safe URL ingestion, uploads and document access over HTTP."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_fetcher
from app.domain.roles import WorkspaceRole
from app.models.sources import SourceSnapshot
from app.services.workspaces import WorkspaceService
from tests.conftest import SettingsFactory
from tests.db.conftest import ApiUser, register_user
from tests.support.documents import SPEC_PAGE_HTML, make_pdf
from tests.support.fake_web import FakeWeb

pytestmark = [pytest.mark.db, pytest.mark.api]

SPEC_URL = "https://acme.example/laptop-14/specs"


@pytest.fixture
def web(api_app: FastAPI, make_settings: SettingsFactory) -> FakeWeb:
    web = FakeWeb()
    web.host("acme.example", "93.184.216.34")
    web.page("acme.example", "/laptop-14/specs", SPEC_PAGE_HTML)
    fetcher = web.fetcher(make_settings())
    api_app.dependency_overrides[get_fetcher] = lambda: fetcher
    return web


async def _product(api: httpx.AsyncClient, user: ApiUser) -> str:
    response = await api.post(
        "/api/v1/products",
        json={"brand": f"Acme-{uuid.uuid4().hex[:6]}", "name": "Laptop 14", "category": "laptop"},
        headers=user.headers,
    )
    product_id: str = response.json()["id"]
    return product_id


async def _source(
    api: httpx.AsyncClient, user: ApiUser, product_id: str, **overrides: Any
) -> httpx.Response:
    body = {
        "source_type": "MANUFACTURER_PAGE",
        "title": "Official specs",
        "url": SPEC_URL,
        "authority": "OFFICIAL",
        **overrides,
    }
    return await api.post(f"/api/v1/products/{product_id}/sources", json=body, headers=user.headers)


async def test_register_and_ingest_url_source(api: httpx.AsyncClient, web: FakeWeb) -> None:
    user = await register_user(api)
    pid = await _product(api, user)
    created = await _source(api, user, pid)
    assert created.status_code == 201
    source = created.json()
    assert source["status"] == "PENDING"

    ingested = await api.post(f"/api/v1/sources/{source['id']}/ingest", headers=user.headers)
    assert ingested.status_code == 200, ingested.text
    body = ingested.json()
    assert body["unchanged"] is False
    assert body["source"]["status"] == "INGESTED"
    assert body["snapshot"]["content_type"] == "text/html"
    assert body["snapshot"]["final_url"] == SPEC_URL
    assert len(body["snapshot"]["sha256"]) == 64
    assert body["document"]["parser"] == "html"

    document = await api.get(f"/api/v1/sources/{source['id']}/document", headers=user.headers)
    text = document.json()["text"]
    assert "Memory: 16 GB LPDDR5" in text
    assert "document.cookie" not in text

    detail = await api.get(f"/api/v1/sources/{source['id']}", headers=user.headers)
    assert detail.json()["latest_document"]["id"] == body["document"]["id"]


async def test_reingesting_unchanged_content_reuses_snapshot(
    api: httpx.AsyncClient, web: FakeWeb, db_session: AsyncSession
) -> None:
    user = await register_user(api)
    pid = await _product(api, user)
    sid = (await _source(api, user, pid)).json()["id"]

    first = (await api.post(f"/api/v1/sources/{sid}/ingest", headers=user.headers)).json()
    second = (await api.post(f"/api/v1/sources/{sid}/ingest", headers=user.headers)).json()
    assert second["unchanged"] is True
    assert second["snapshot"]["id"] == first["snapshot"]["id"]

    web.page("acme.example", "/laptop-14/specs", "<html><body>Memory: 32 GB</body></html>")
    third = (await api.post(f"/api/v1/sources/{sid}/ingest", headers=user.headers)).json()
    assert third["unchanged"] is False
    count = await db_session.scalar(
        select(func.count())
        .select_from(SourceSnapshot)
        .where(SourceSnapshot.source_id == uuid.UUID(sid))
    )
    assert count == 2
    latest = await api.get(f"/api/v1/sources/{sid}/document", headers=user.headers)
    assert "32 GB" in latest.json()["text"]

    # Content reverts to the original: the old snapshot is reused AND becomes latest.
    web.page("acme.example", "/laptop-14/specs", SPEC_PAGE_HTML)
    fourth = (await api.post(f"/api/v1/sources/{sid}/ingest", headers=user.headers)).json()
    assert fourth["unchanged"] is True
    assert fourth["snapshot"]["id"] == first["snapshot"]["id"]
    reverted = await api.get(f"/api/v1/sources/{sid}/document", headers=user.headers)
    assert "16 GB" in reverted.json()["text"]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8000/api/v1/health",
        "http://10.0.0.1/",
        "http://acme.example:2375/containers/json",
    ],
)
async def test_unsafe_urls_are_rejected_at_registration(
    api: httpx.AsyncClient, web: FakeWeb, url: str
) -> None:
    user = await register_user(api)
    pid = await _product(api, user)
    response = await _source(api, user, pid, url=url)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsafe_url"


async def test_dns_to_private_address_is_blocked_at_fetch_time(
    api: httpx.AsyncClient, web: FakeWeb
) -> None:
    web.host("sneaky.example", "192.168.0.10")
    user = await register_user(api)
    pid = await _product(api, user)
    sid = (await _source(api, user, pid, url="https://sneaky.example/specs")).json()["id"]

    response = await api.post(f"/api/v1/sources/{sid}/ingest", headers=user.headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsafe_url"
    assert "192.168" not in response.json()["error"]["message"]
    detail = (await api.get(f"/api/v1/sources/{sid}", headers=user.headers)).json()
    assert detail["status"] == "FAILED"
    assert web.requests == []


async def test_upstream_failure_is_502_and_recorded(api: httpx.AsyncClient, web: FakeWeb) -> None:
    user = await register_user(api)
    pid = await _product(api, user)
    sid = (await _source(api, user, pid, url="https://acme.example/missing")).json()["id"]
    response = await api.post(f"/api/v1/sources/{sid}/ingest", headers=user.headers)
    assert response.status_code == 502
    detail = (await api.get(f"/api/v1/sources/{sid}", headers=user.headers)).json()
    assert detail["status"] == "FAILED"
    assert "404" in detail["last_error"]


async def test_source_without_url_cannot_be_ingested(api: httpx.AsyncClient, web: FakeWeb) -> None:
    user = await register_user(api)
    pid = await _product(api, user)
    sid = (await _source(api, user, pid, url=None)).json()["id"]
    response = await api.post(f"/api/v1/sources/{sid}/ingest", headers=user.headers)
    assert response.status_code == 409
    missing = await api.get(f"/api/v1/sources/{sid}/document", headers=user.headers)
    assert missing.status_code == 404


async def test_upload_pdf_text_and_html(api: httpx.AsyncClient, web: FakeWeb) -> None:
    user = await register_user(api)
    pid = await _product(api, user)
    files = [
        ("warranty.pdf", make_pdf(["Warranty: 2 years"]), "application/pdf", "pdf"),
        ("notes.md", b"# Notes\nFan noise under load", "text/markdown", "text"),
        ("page.html", SPEC_PAGE_HTML.encode(), "text/html", "html"),
    ]
    for name, content, mime, parser in files:
        response = await api.post(
            f"/api/v1/products/{pid}/sources/upload",
            files={"file": (name, content, mime)},
            data={"source_type": "WARRANTY"},
            headers=user.headers,
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["document"]["parser"] == parser
        assert body["source"]["authority"] == "USER"  # uploads are never official
        assert body["snapshot"]["original_filename"] == name


async def test_upload_lies_about_type_are_caught(api: httpx.AsyncClient, web: FakeWeb) -> None:
    user = await register_user(api)
    pid = await _product(api, user)
    binary = await api.post(
        f"/api/v1/products/{pid}/sources/upload",
        files={"file": ("evil.pdf", b"MZ\x90\x00\x03\x00\x00\x00binary", "application/pdf")},
        headers=user.headers,
    )
    assert binary.status_code == 422
    assert binary.json()["error"]["code"] == "unparseable_source"


async def test_upload_too_large_is_413(
    api_app: FastAPI, api: httpx.AsyncClient, web: FakeWeb, make_settings: SettingsFactory
) -> None:
    api_app.state.settings = make_settings(
        database_url=api_app.state.settings.database_url.get_secret_value(),
        ingestion_max_bytes=1024,
    )
    user = await register_user(api)
    pid = await _product(api, user)
    response = await api.post(
        f"/api/v1/products/{pid}/sources/upload",
        files={"file": ("big.txt", b"a" * 5000, "text/plain")},
        headers=user.headers,
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


async def test_shared_sources_are_creator_only(api: httpx.AsyncClient, web: FakeWeb) -> None:
    creator, other = await register_user(api), await register_user(api)
    pid = await _product(api, creator)
    assert (await _source(api, other, pid)).status_code == 403
    sid = (await _source(api, creator, pid)).json()["id"]
    assert (
        await api.post(f"/api/v1/sources/{sid}/ingest", headers=other.headers)
    ).status_code == 403
    # ...but anyone signed in can read shared sources.
    assert (await api.get(f"/api/v1/sources/{sid}", headers=other.headers)).status_code == 200


async def test_workspace_sources_are_private_to_members(
    api: httpx.AsyncClient, web: FakeWeb, db_session: AsyncSession
) -> None:
    owner, viewer, outsider = (
        await register_user(api),
        await register_user(api),
        await register_user(api),
    )
    pid = await _product(api, owner)
    ws = (await api.post("/api/v1/workspaces", json={"name": "W"}, headers=owner.headers)).json()[
        "id"
    ]
    await WorkspaceService(db_session).add_member(
        uuid.UUID(ws), uuid.UUID(viewer.id), WorkspaceRole.VIEWER
    )

    private = await _source(api, owner, pid, workspace_id=ws, title="Team notes", url=None)
    assert private.status_code == 201
    sid = private.json()["id"]
    shared_sid = (await _source(api, owner, pid)).json()["id"]

    # Viewer can read but not add; outsider can't even see it.
    assert (await api.get(f"/api/v1/sources/{sid}", headers=viewer.headers)).status_code == 200
    assert (await _source(api, viewer, pid, workspace_id=ws)).status_code == 403
    assert (await api.get(f"/api/v1/sources/{sid}", headers=outsider.headers)).status_code == 404
    assert (await _source(api, outsider, pid, workspace_id=ws)).status_code == 404

    outsider_list = await api.get(f"/api/v1/products/{pid}/sources", headers=outsider.headers)
    assert [s["id"] for s in outsider_list.json()] == [shared_sid]
    owner_list = await api.get(f"/api/v1/products/{pid}/sources", headers=owner.headers)
    assert {s["id"] for s in owner_list.json()} == {sid, shared_sid}


async def test_unknown_product_and_source_are_404(api: httpx.AsyncClient, web: FakeWeb) -> None:
    user = await register_user(api)
    assert (await _source(api, user, str(uuid.uuid4()))).status_code == 404
    assert (
        await api.post(f"/api/v1/sources/{uuid.uuid4()}/ingest", headers=user.headers)
    ).status_code == 404
