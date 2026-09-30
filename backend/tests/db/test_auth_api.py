"""Registration, login and /auth/me over HTTP against real PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import Organization, OrganizationMember, User
from app.security.tokens import create_access_token
from tests.conftest import SettingsFactory
from tests.db.conftest import register_user

pytestmark = [pytest.mark.db, pytest.mark.api]

PASSWORD = "correct-horse-battery"


async def test_register_returns_public_profile_only(api: httpx.AsyncClient) -> None:
    response = await api.post(
        "/api/v1/auth/register",
        json={"email": "Ada@Example.com", "password": PASSWORD, "display_name": " Ada "},
    )
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "email", "display_name", "created_at"}
    assert body["email"] == "ada@example.com"  # normalised
    assert body["display_name"] == "Ada"
    assert PASSWORD not in response.text


async def test_register_stores_argon2_hash_and_personal_org(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await register_user(api, email="grace@example.com")
    stored = await db_session.scalar(select(User).where(User.email == "grace@example.com"))
    assert stored is not None
    assert stored.password_hash.startswith("$argon2id$")

    org = await db_session.scalar(
        select(Organization)
        .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
        .where(OrganizationMember.user_id == stored.id)
    )
    assert org is not None
    assert org.is_personal
    assert str(stored.id) == user.id


async def test_duplicate_email_is_rejected_case_insensitively(api: httpx.AsyncClient) -> None:
    await register_user(api, email="dup@example.com")
    response = await api.post(
        "/api/v1/auth/register",
        json={"email": "DUP@example.com", "password": PASSWORD, "display_name": "Dup"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_already_registered"


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": PASSWORD, "display_name": "X"},
        {"email": "a@example.com", "password": "short", "display_name": "X"},
        {"email": "a@example.com", "password": "x" * 129, "display_name": "X"},
        {"email": "a@example.com", "password": PASSWORD, "display_name": "   "},
        {"email": "a@example.com", "password": PASSWORD, "display_name": "X", "is_admin": True},
    ],
)
async def test_register_validates_input(api: httpx.AsyncClient, payload: dict[str, object]) -> None:
    response = await api.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["request_id"]
    assert PASSWORD not in response.text  # submitted values are never echoed


async def test_login_success_returns_bearer_token(api: httpx.AsyncClient) -> None:
    await register_user(api, email="login@example.com")
    response = await api.post(
        "/api/v1/auth/login", json={"email": "LOGIN@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"].count(".") == 2


async def test_login_failures_are_indistinguishable(api: httpx.AsyncClient) -> None:
    await register_user(api, email="known@example.com")
    wrong_password = await api.post(
        "/api/v1/auth/login", json={"email": "known@example.com", "password": "nope-nope-nope"}
    )
    unknown_user = await api.post(
        "/api/v1/auth/login", json={"email": "ghost@example.com", "password": PASSWORD}
    )
    for response in (wrong_password, unknown_user):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json()["error"]["code"] == "invalid_credentials"
    assert wrong_password.json()["error"]["message"] == unknown_user.json()["error"]["message"]


async def test_inactive_user_cannot_log_in(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await register_user(api, email="inactive@example.com")
    stored = await db_session.get(User, uuid.UUID(user.id))
    assert stored is not None
    stored.is_active = False
    await db_session.flush()

    login = await api.post(
        "/api/v1/auth/login", json={"email": "inactive@example.com", "password": PASSWORD}
    )
    assert login.status_code == 401
    me = await api.get("/api/v1/auth/me", headers=user.headers)
    assert me.status_code == 401


async def test_me_returns_current_user(api: httpx.AsyncClient) -> None:
    user = await register_user(api, email="me@example.com", display_name="Me")
    response = await api.get("/api/v1/auth/me", headers=user.headers)
    assert response.status_code == 200
    assert response.json()["email"] == "me@example.com"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer not.a.jwt"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
    ],
)
async def test_me_requires_valid_bearer_token(
    api: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await api.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "not_authenticated"


async def test_expired_token_is_rejected(
    api: httpx.AsyncClient, make_settings: SettingsFactory
) -> None:
    user = await register_user(api)
    expired = create_access_token(
        uuid.UUID(user.id),
        make_settings(),
        now=datetime.now(UTC) - timedelta(hours=2),
    )
    response = await api.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired.token}"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Token has expired."


async def test_token_for_deleted_user_is_rejected(
    api: httpx.AsyncClient, make_settings: SettingsFactory
) -> None:
    ghost = create_access_token(uuid.uuid4(), make_settings())
    response = await api.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {ghost.token}"})
    assert response.status_code == 401
