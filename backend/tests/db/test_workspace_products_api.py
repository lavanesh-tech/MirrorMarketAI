"""Products inside workspaces: role enforcement and tenant isolation."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.roles import WorkspaceRole
from app.services.workspaces import WorkspaceService
from tests.db.conftest import ApiUser, register_user

pytestmark = [pytest.mark.db, pytest.mark.api]


async def _setup(api: httpx.AsyncClient) -> tuple[ApiUser, str, str]:
    owner = await register_user(api)
    ws = await api.post("/api/v1/workspaces", json={"name": "Laptops"}, headers=owner.headers)
    product = await api.post(
        "/api/v1/products",
        json={"brand": f"Brand-{uuid.uuid4().hex[:6]}", "name": "Model", "category": "laptop"},
        headers=owner.headers,
    )
    return owner, ws.json()["id"], product.json()["id"]


def _path(workspace_id: str) -> str:
    return f"/api/v1/workspaces/{workspace_id}/products"


async def test_add_list_remove(api: httpx.AsyncClient) -> None:
    owner, ws, pid = await _setup(api)

    added = await api.post(
        _path(ws), json={"product_id": pid, "notes": "strong candidate"}, headers=owner.headers
    )
    assert added.status_code == 201
    assert added.json()["product"]["id"] == pid
    assert added.json()["notes"] == "strong candidate"

    listed = await api.get(_path(ws), headers=owner.headers)
    assert [item["product"]["id"] for item in listed.json()] == [pid]

    dup = await api.post(_path(ws), json={"product_id": pid}, headers=owner.headers)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "product_already_in_workspace"

    removed = await api.delete(f"{_path(ws)}/{pid}", headers=owner.headers)
    assert removed.status_code == 204
    assert (await api.get(_path(ws), headers=owner.headers)).json() == []
    again = await api.delete(f"{_path(ws)}/{pid}", headers=owner.headers)
    assert again.status_code == 404


async def test_add_unknown_product_is_404(api: httpx.AsyncClient) -> None:
    owner, ws, _ = await _setup(api)
    response = await api.post(
        _path(ws), json={"product_id": str(uuid.uuid4())}, headers=owner.headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "product_not_found"


async def test_outsider_cannot_see_or_change_workspace_products(api: httpx.AsyncClient) -> None:
    owner, ws, pid = await _setup(api)
    await api.post(_path(ws), json={"product_id": pid}, headers=owner.headers)
    outsider = await register_user(api)

    assert (await api.get(_path(ws), headers=outsider.headers)).status_code == 404
    assert (
        await api.post(_path(ws), json={"product_id": pid}, headers=outsider.headers)
    ).status_code == 404
    assert (await api.delete(f"{_path(ws)}/{pid}", headers=outsider.headers)).status_code == 404


@pytest.mark.parametrize(
    ("role", "can_modify"),
    [(WorkspaceRole.EDITOR, True), (WorkspaceRole.MEMBER, False), (WorkspaceRole.VIEWER, False)],
)
async def test_roles_for_workspace_products(
    api: httpx.AsyncClient, db_session: AsyncSession, role: WorkspaceRole, can_modify: bool
) -> None:
    _owner, ws, pid = await _setup(api)
    collaborator = await register_user(api)
    await WorkspaceService(db_session).add_member(uuid.UUID(ws), uuid.UUID(collaborator.id), role)

    assert (await api.get(_path(ws), headers=collaborator.headers)).status_code == 200
    added = await api.post(_path(ws), json={"product_id": pid}, headers=collaborator.headers)
    assert added.status_code == (201 if can_modify else 403)


async def test_same_product_in_two_workspaces_is_isolated(api: httpx.AsyncClient) -> None:
    alice, ws_a, pid = await _setup(api)
    bob = await register_user(api)
    ws_b = (await api.post("/api/v1/workspaces", json={"name": "Bob"}, headers=bob.headers)).json()[
        "id"
    ]

    await api.post(
        _path(ws_a), json={"product_id": pid, "notes": "alice note"}, headers=alice.headers
    )
    await api.post(_path(ws_b), json={"product_id": pid, "notes": "bob note"}, headers=bob.headers)

    alice_view = (await api.get(_path(ws_a), headers=alice.headers)).json()
    bob_view = (await api.get(_path(ws_b), headers=bob.headers)).json()
    assert [i["notes"] for i in alice_view] == ["alice note"]
    assert [i["notes"] for i in bob_view] == ["bob note"]

    # Bob removing it from his workspace doesn't touch Alice's.
    await api.delete(f"{_path(ws_b)}/{pid}", headers=bob.headers)
    assert len((await api.get(_path(ws_a), headers=alice.headers)).json()) == 1


async def test_catalog_product_in_use_cannot_be_hard_deleted(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    from sqlalchemy import text  # noqa: PLC0415
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    owner, ws, pid = await _setup(api)
    await api.post(_path(ws), json={"product_id": pid}, headers=owner.headers)
    with pytest.raises(IntegrityError, match="fk_workspace_products_product_id_products"):
        await db_session.execute(text("DELETE FROM products WHERE id = :id"), {"id": pid})
    await db_session.rollback()
