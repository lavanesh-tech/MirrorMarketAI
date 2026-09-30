"""Purchase requirements over HTTP: extraction, versioning, locking, history, access."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_extractor
from app.core.database import Database
from app.core.errors import RequirementVersionConflictError
from app.domain.roles import WorkspaceRole
from app.models.identity import ComparisonWorkspace, User
from app.models.requirements import RequirementVersion
from app.providers.extraction import Extraction, ExtractionError, RuleBasedExtractor
from app.services.auth import AuthService
from app.services.requirements import RequirementService
from app.services.workspaces import WorkspaceService
from tests.conftest import SettingsFactory
from tests.db.conftest import ApiUser, migrate, register_user

pytestmark = [pytest.mark.db, pytest.mark.api]

BRIEF = (
    "Need a laptop under $1,500 for programming and travel. Must have at least 16 GB RAM, "
    "ideally under 1.4 kg. No touchscreen. Avoid Dell."
)


async def _workspace(api: httpx.AsyncClient, user: ApiUser) -> str:
    response = await api.post("/api/v1/workspaces", json={"name": "Laptop"}, headers=user.headers)
    ws: str = response.json()["id"]
    return ws


def _url(ws: str, suffix: str = "") -> str:
    return f"/api/v1/workspaces/{ws}/requirements{suffix}"


async def _save(
    api: httpx.AsyncClient, user: ApiUser, ws: str, expected: int, **body: Any
) -> httpx.Response:
    return await api.put(
        _url(ws), json={"expected_version": expected, **body}, headers=user.headers
    )


async def test_extract_previews_without_saving(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await register_user(api)
    ws = await _workspace(api, user)
    response = await api.post(_url(ws, "/extract"), json={"text": BRIEF}, headers=user.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extractor"] == "rules-v1"
    assert body["degraded"] is False
    assert body["spec"]["category"] == "laptop"
    assert body["spec"]["budget"] == {"min_amount": None, "max_amount": "1500", "currency": "USD"}
    assert {c["key"] for c in body["spec"]["criteria"]} == {
        "ram_gb",
        "weight_kg",
        "has_touchscreen",
    }
    assert await db_session.scalar(select(func.count()).select_from(RequirementVersion)) == 0
    assert (await api.get(_url(ws), headers=user.headers)).json()["error"]["code"] == (
        "requirement_not_found"
    )


async def test_save_versions_idempotency_and_history(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    ws = await _workspace(api, user)

    first = await _save(api, user, ws, 0, text=BRIEF, change_note="initial brief")
    assert first.status_code == 201, first.text
    v1 = first.json()
    assert v1["current_version"] == 1
    assert v1["current"]["version"] == 1
    assert v1["current"]["extractor"] == "rules-v1"
    assert v1["current"]["raw_text"] == BRIEF
    assert v1["current"]["change_note"] == "initial brief"
    assert v1["current"]["created_by_id"] == user.id

    same = await _save(api, user, ws, 1, text=BRIEF)
    assert same.status_code == 200
    assert same.json()["current"]["id"] == v1["current"]["id"]

    edited = v1["current"]["spec"]
    edited["budget"]["max_amount"] = "1800"
    edited["criteria"] = [c for c in edited["criteria"] if c["key"] != "weight_kg"]
    edited["criteria"].append(
        {"key": "has_oled_display", "operator": "=", "value_text": "yes", "priority": "SHOULD"}
    )
    second = await _save(api, user, ws, 1, spec=edited, text=BRIEF, change_note="more budget")
    assert second.status_code == 201, second.text
    assert second.json()["current"]["extractor"] == "manual"
    assert second.json()["current_version"] == 2

    current = (await api.get(_url(ws), headers=user.headers)).json()
    assert current["current_version"] == 2
    assert current["current"]["spec"]["budget"]["max_amount"] == "1800"

    versions = (await api.get(_url(ws, "/versions"), headers=user.headers)).json()
    assert [v["version"] for v in versions["items"]] == [2, 1]
    assert versions["page"]["total"] == 2

    old = await api.get(_url(ws, "/versions/1"), headers=user.headers)
    assert old.json()["spec"]["budget"]["max_amount"] == "1500"
    missing = await api.get(_url(ws, "/versions/9"), headers=user.headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "requirement_version_not_found"

    diff = (await api.get(_url(ws, "/diff?from=1&to=2"), headers=user.headers)).json()
    assert [c["field"] for c in diff["changes"]] == ["budget"]
    assert [c["key"] for c in diff["criteria_added"]] == ["has_oled_display"]
    assert [c["key"] for c in diff["criteria_removed"]] == ["weight_kg"]
    assert diff["criteria_changed"] == []


async def test_stale_expected_version_is_409(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    ws = await _workspace(api, user)
    assert (await _save(api, user, ws, 0, text=BRIEF)).status_code == 201
    stale = await _save(api, user, ws, 0, text="A phone with 256 GB storage")
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "requirement_version_conflict"
    ahead = await _save(api, user, ws, 5, text="A phone with 256 GB storage")
    assert ahead.status_code == 409


@pytest.fixture
def isolated_db_url(fresh_database_url: Callable[[], str]) -> str:
    url = fresh_database_url()
    migrate(url)
    return url


async def test_concurrent_saves_one_wins(
    isolated_db_url: str, make_settings: SettingsFactory
) -> None:
    """Real commits on separate connections: the row lock / unique key lets one writer win."""
    settings = make_settings(database_url=isolated_db_url)
    database = Database.from_settings(settings)
    extractor = RuleBasedExtractor()
    try:
        async with database.session_factory() as session:
            user = await AuthService(session, settings).register(
                email="race@example.com", password="correct-horse-battery", display_name="R"
            )
            workspace, _ = await WorkspaceService(session).create(user, name="W", description=None)
            user_id, workspace_id = user.id, workspace.id

        async def save(text: str, expected: int) -> str:
            async with database.session_factory() as session:
                me = await session.get(User, user_id)
                assert me is not None
                try:
                    await RequirementService(session, settings, extractor).save(
                        workspace_id,
                        me,
                        text=text,
                        spec=None,
                        expected_version=expected,
                        change_note=None,
                    )
                    await session.commit()
                except RequirementVersionConflictError:
                    return "conflict"
                return "saved"

        for expected in (0, 1):  # first save (row creation race), then an update race
            outcomes = await asyncio.gather(
                save(f"A tablet with at least {expected + 8} GB RAM", expected),
                save(f"A phone with {expected + 256} GB storage", expected),
            )
            assert sorted(outcomes) == ["conflict", "saved"], expected

        async with database.session_factory() as session:
            versions = await session.scalars(select(RequirementVersion.version))
            assert sorted(versions) == [1, 2]
    finally:
        await database.dispose()


async def test_request_validation(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    ws = await _workspace(api, user)
    nothing = await _save(api, user, ws, 0)
    assert nothing.status_code == 422
    bad_spec = await _save(
        api, user, ws, 0, spec={"criteria": [{"key": "ram_gb", "operator": ">="}]}
    )
    assert bad_spec.status_code == 422
    too_long = await api.post(_url(ws, "/extract"), json={"text": "x" * 4001}, headers=user.headers)
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "requirement_text_too_long"
    assert (await api.get(_url(ws, "/versions/0"), headers=user.headers)).status_code == 422


async def test_roles_and_isolation(api: httpx.AsyncClient, db_session: AsyncSession) -> None:
    owner = await register_user(api)
    viewer = await register_user(api)
    outsider = await register_user(api)
    ws = await _workspace(api, owner)
    await WorkspaceService(db_session).add_member(
        uuid.UUID(ws), uuid.UUID(viewer.id), WorkspaceRole.VIEWER
    )
    assert (await _save(api, owner, ws, 0, text=BRIEF)).status_code == 201

    assert (await api.get(_url(ws), headers=viewer.headers)).status_code == 200
    assert (await api.get(_url(ws, "/versions"), headers=viewer.headers)).status_code == 200
    denied = await _save(api, viewer, ws, 1, text="anything")
    assert denied.status_code == 403
    preview = await api.post(_url(ws, "/extract"), json={"text": "x"}, headers=viewer.headers)
    assert preview.status_code == 403

    for response in (
        await api.get(_url(ws), headers=outsider.headers),
        await api.get(_url(ws, "/versions/1"), headers=outsider.headers),
        await api.get(_url(ws, "/diff?from=1&to=1"), headers=outsider.headers),
        await _save(api, outsider, ws, 1, text="anything"),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "workspace_not_found"


async def test_failed_llm_extraction_degrades_to_rules(
    api_app: FastAPI, api: httpx.AsyncClient
) -> None:
    class BrokenLLM:
        name = "openai:test"

        async def extract(self, text: str) -> Extraction:
            raise ExtractionError("provider down")

        async def aclose(self) -> None:
            return None

    api_app.dependency_overrides[get_extractor] = BrokenLLM
    try:
        user = await register_user(api)
        ws = await _workspace(api, user)
        preview = (
            await api.post(_url(ws, "/extract"), json={"text": BRIEF}, headers=user.headers)
        ).json()
        assert preview["degraded"] is True
        assert preview["extractor"] == "rules-v1"
        saved = await _save(api, user, ws, 0, text=BRIEF)
        assert saved.status_code == 201
        assert saved.json()["degraded"] is True
        assert saved.json()["current"]["extractor"] == "rules-v1"
    finally:
        api_app.dependency_overrides.pop(get_extractor)


async def test_deleting_workspace_cascades(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await register_user(api)
    ws = await _workspace(api, user)
    assert (await _save(api, user, ws, 0, text=BRIEF)).status_code == 201
    workspace = await db_session.get(ComparisonWorkspace, uuid.UUID(ws))
    await db_session.delete(workspace)
    await db_session.flush()
    assert await db_session.scalar(select(func.count()).select_from(RequirementVersion)) == 0
