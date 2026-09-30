"""Workspace CRUD, membership-based isolation and role enforcement over HTTP."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.roles import WorkspaceRole
from app.models.identity import WorkspaceMember
from app.services.workspaces import WorkspaceService
from tests.db.conftest import ApiUser, register_user

pytestmark = [pytest.mark.db, pytest.mark.api]

BASE = "/api/v1/workspaces"


async def _create(client: httpx.AsyncClient, user: ApiUser, name: str = "Laptop hunt") -> str:
    response = await client.post(
        BASE, json={"name": name, "description": "Under $1,500"}, headers=user.headers
    )
    assert response.status_code == 201, response.text
    workspace_id: str = response.json()["id"]
    return workspace_id


async def _add_member(
    session: AsyncSession, workspace_id: str, user: ApiUser, role: WorkspaceRole
) -> None:
    await WorkspaceService(session).add_member(uuid.UUID(workspace_id), uuid.UUID(user.id), role)


async def test_create_makes_creator_owner(api: httpx.AsyncClient) -> None:
    owner = await register_user(api)
    response = await api.post(BASE, json={"name": "  Laptop hunt  "}, headers=owner.headers)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Laptop hunt"
    assert body["my_role"] == "OWNER"
    assert body["created_by_id"] == owner.id
    assert body["description"] is None


async def test_all_workspace_routes_require_authentication(api: httpx.AsyncClient) -> None:
    some_id = uuid.uuid4()
    for method, path in [
        ("POST", BASE),
        ("GET", BASE),
        ("GET", f"{BASE}/{some_id}"),
        ("PATCH", f"{BASE}/{some_id}"),
        ("GET", f"{BASE}/{some_id}/members"),
    ]:
        response = await api.request(method, path, json={"name": "x"})
        assert response.status_code == 401, (method, path)


async def test_list_returns_only_my_workspaces_with_pagination(api: httpx.AsyncClient) -> None:
    alice, bob = await register_user(api), await register_user(api)
    for index in range(3):
        await _create(api, alice, f"alice-{index}")
    await _create(api, bob, "bob-only")

    first = (await api.get(f"{BASE}?limit=2&offset=0", headers=alice.headers)).json()
    second = (await api.get(f"{BASE}?limit=2&offset=2", headers=alice.headers)).json()

    assert first["page"] == {"total": 3, "limit": 2, "offset": 0}
    names = [w["name"] for w in first["items"] + second["items"]]
    assert sorted(names) == ["alice-0", "alice-1", "alice-2"]
    assert "bob-only" not in names


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=abc"])
async def test_list_rejects_invalid_pagination(api: httpx.AsyncClient, query: str) -> None:
    user = await register_user(api)
    response = await api.get(f"{BASE}?{query}", headers=user.headers)
    assert response.status_code == 422


async def test_non_member_gets_404_not_403(api: httpx.AsyncClient) -> None:
    """IDOR defence: outsiders can't even learn that the workspace exists."""
    owner, outsider = await register_user(api), await register_user(api)
    workspace_id = await _create(api, owner)

    for method, path in [
        ("GET", f"{BASE}/{workspace_id}"),
        ("PATCH", f"{BASE}/{workspace_id}"),
        ("GET", f"{BASE}/{workspace_id}/members"),
    ]:
        response = await api.request(
            method, path, json={"name": "hijack"}, headers=outsider.headers
        )
        assert response.status_code == 404, (method, path)
        assert response.json()["error"]["code"] == "workspace_not_found"

    unknown = await api.get(f"{BASE}/{uuid.uuid4()}", headers=owner.headers)
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "workspace_not_found"


async def test_malformed_workspace_id_is_422(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    response = await api.get(f"{BASE}/not-a-uuid", headers=user.headers)
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("role", "can_edit"),
    [
        (WorkspaceRole.OWNER, True),
        (WorkspaceRole.EDITOR, True),
        (WorkspaceRole.MEMBER, False),
        (WorkspaceRole.VIEWER, False),
    ],
)
async def test_patch_is_limited_to_owner_and_editor(
    api: httpx.AsyncClient, db_session: AsyncSession, role: WorkspaceRole, can_edit: bool
) -> None:
    owner, collaborator = await register_user(api), await register_user(api)
    workspace_id = await _create(api, owner)
    if role is not WorkspaceRole.OWNER:
        await _add_member(db_session, workspace_id, collaborator, role)
    actor = owner if role is WorkspaceRole.OWNER else collaborator

    viewed = await api.get(f"{BASE}/{workspace_id}", headers=actor.headers)
    assert viewed.status_code == 200
    assert viewed.json()["my_role"] == role.value

    response = await api.patch(
        f"{BASE}/{workspace_id}", json={"name": "Renamed"}, headers=actor.headers
    )
    if can_edit:
        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"
    else:
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "permission_denied"


async def test_patch_is_partial(api: httpx.AsyncClient) -> None:
    owner = await register_user(api)
    workspace_id = await _create(api, owner)

    cleared = await api.patch(
        f"{BASE}/{workspace_id}", json={"description": None}, headers=owner.headers
    )
    assert cleared.status_code == 200
    assert cleared.json()["name"] == "Laptop hunt"  # untouched
    assert cleared.json()["description"] is None


@pytest.mark.parametrize(
    "payload", [{}, {"name": None}, {"name": ""}, {"name": "x" * 121}, {"owner": "me"}]
)
async def test_patch_validates_input(api: httpx.AsyncClient, payload: dict[str, object]) -> None:
    owner = await register_user(api)
    workspace_id = await _create(api, owner)
    response = await api.patch(f"{BASE}/{workspace_id}", json=payload, headers=owner.headers)
    assert response.status_code == 422


async def test_members_endpoint_lists_roles(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await register_user(api, display_name="Owner")
    viewer = await register_user(api, display_name="Viewer")
    workspace_id = await _create(api, owner)
    await _add_member(db_session, workspace_id, viewer, WorkspaceRole.VIEWER)

    response = await api.get(f"{BASE}/{workspace_id}/members", headers=viewer.headers)
    assert response.status_code == 200
    roles = {m["display_name"]: m["role"] for m in response.json()}
    assert roles == {"Owner": "OWNER", "Viewer": "VIEWER"}


async def test_membership_is_unique_per_user(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await register_user(api)
    workspace_id = await _create(api, owner)
    db_session.add(
        WorkspaceMember(
            workspace_id=uuid.UUID(workspace_id),
            user_id=uuid.UUID(owner.id),
            role=WorkspaceRole.VIEWER,
        )
    )
    with pytest.raises(IntegrityError, match="uq_workspace_members_workspace_id_user_id"):
        await db_session.flush()
    await db_session.rollback()


async def test_database_rejects_unknown_role(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    from sqlalchemy import text  # noqa: PLC0415

    owner = await register_user(api)
    workspace_id = await _create(api, owner)
    with pytest.raises(IntegrityError, match="ck_workspace_members_role_valid"):
        await db_session.execute(
            text("UPDATE workspace_members SET role = 'SUPERUSER' WHERE workspace_id = :id"),
            {"id": workspace_id},
        )
    await db_session.rollback()
