"""Sessions (refresh rotation, reuse detection, revocation), audit trail and upload defences."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.roles import WorkspaceRole
from app.models.security import AuditLog, RefreshToken
from app.security.refresh import hash_refresh_token
from tests.db.conftest import ApiUser, StackFactory, join, register_user, workspace

pytestmark = [pytest.mark.db, pytest.mark.api]

PASSWORD = "correct-horse-battery"
AUTH = "/api/v1/auth"


async def login(api: httpx.AsyncClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    response = await api.post(f"{AUTH}/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    tokens: dict[str, str] = response.json()
    return tokens


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def refresh(api: httpx.AsyncClient, token: str) -> httpx.Response:
    return await api.post(f"{AUTH}/refresh", json={"refresh_token": token})


async def actions(session: AsyncSession, user_id: str) -> list[tuple[str, str]]:
    rows = await session.execute(
        select(AuditLog.action, AuditLog.outcome)
        .where(AuditLog.actor_id == uuid.UUID(user_id))
        .order_by(AuditLog.occurred_at)
    )
    return [(a, o) for a, o in rows]


async def test_refresh_rotates_and_the_old_token_is_single_use(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await register_user(api)
    first = await login(api, user.email)
    assert first["refresh_token"] not in (first["access_token"], "")
    stored = await db_session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(first["refresh_token"])
        )
    )
    assert stored is not None  # only the hash is stored
    plain = await db_session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == first["refresh_token"])
    )
    assert plain is None

    second = (await refresh(api, first["refresh_token"])).json()
    assert second["refresh_token"] != first["refresh_token"]
    assert (await api.get(f"{AUTH}/me", headers=bearer(second["access_token"]))).status_code == 200

    # Inside the leeway a second use is a benign race (two tabs): it just rotates again.
    raced = await refresh(api, first["refresh_token"])
    assert raced.status_code == 200
    assert raced.json()["refresh_token"] not in (first["refresh_token"], second["refresh_token"])

    # After the leeway, replaying a rotated token means it was copied: session revoked.
    await db_session.execute(
        update(RefreshToken)
        .where(RefreshToken.rotated_at.is_not(None))
        .values(rotated_at=datetime.now(UTC) - timedelta(seconds=11))
    )
    replay = await refresh(api, first["refresh_token"])
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "invalid_refresh_token"
    assert (await refresh(api, second["refresh_token"])).status_code == 401
    assert (await refresh(api, raced.json()["refresh_token"])).status_code == 401
    assert ("auth.refresh_reuse_detected", "FAILURE") in await actions(db_session, user.id)

    # A different login session of the same user is unaffected.
    other = await login(api, user.email)
    assert (await refresh(api, other["refresh_token"])).status_code == 200


async def test_refresh_rejects_unknown_expired_and_inactive(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await register_user(api)
    assert (await refresh(api, "x" * 64)).status_code == 401
    assert (await refresh(api, "short")).status_code == 422

    tokens = await login(api, user.email)
    past = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.execute(update(RefreshToken).values(expires_at=past))
    assert (await refresh(api, tokens["refresh_token"])).status_code == 401

    tokens = await login(api, user.email)
    await db_session.execute(
        text("UPDATE users SET is_active = false WHERE id = :u"), {"u": user.id}
    )
    assert (await refresh(api, tokens["refresh_token"])).status_code == 401


async def test_rotation_cannot_extend_a_session_past_its_absolute_limit(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await register_user(api)
    tokens = await login(api, user.email)
    cap = datetime.now(UTC) + timedelta(hours=1)
    await db_session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(tokens["refresh_token"]))
        .values(family_expires_at=cap)
    )
    rotated = (await refresh(api, tokens["refresh_token"])).json()
    assert datetime.fromisoformat(rotated["refresh_expires_at"]) == cap


async def test_logout_ends_one_session_and_is_idempotent(api: httpx.AsyncClient) -> None:
    user = await register_user(api)
    phone, laptop = await login(api, user.email), await login(api, user.email)
    for _ in range(2):
        out = await api.post(f"{AUTH}/logout", json={"refresh_token": phone["refresh_token"]})
        assert out.status_code == 204
    assert (await api.post(f"{AUTH}/logout", json={"refresh_token": "y" * 64})).status_code == 204
    assert (await refresh(api, phone["refresh_token"])).status_code == 401
    assert (await refresh(api, laptop["refresh_token"])).status_code == 200


async def test_logout_all_revokes_access_and_refresh_tokens_everywhere(
    api: httpx.AsyncClient,
) -> None:
    user = await register_user(api)
    phone, laptop = await login(api, user.email), await login(api, user.email)
    assert (
        await api.post(f"{AUTH}/logout-all", headers=bearer(phone["access_token"]))
    ).status_code == 204
    for tokens in (phone, laptop):
        me = await api.get(f"{AUTH}/me", headers=bearer(tokens["access_token"]))
        assert (me.status_code, me.json()["error"]["message"]) == (401, "Token has been revoked.")
        assert (await refresh(api, tokens["refresh_token"])).status_code == 401
    assert (await api.get(f"{AUTH}/me", headers=user.headers)).status_code == 401
    fresh = await login(api, user.email)
    assert (await api.get(f"{AUTH}/me", headers=bearer(fresh["access_token"]))).status_code == 200


async def test_change_password_ends_other_sessions(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await register_user(api)
    other = await login(api, user.email)
    new_password = "a-brand-new-passphrase"
    wrong = await api.post(
        f"{AUTH}/change-password",
        json={"current_password": "not-the-password", "new_password": new_password},
        headers=user.headers,
    )
    assert wrong.status_code == 401
    weak = await api.post(
        f"{AUTH}/change-password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=user.headers,
    )
    assert weak.status_code == 422

    changed = await api.post(
        f"{AUTH}/change-password",
        json={"current_password": PASSWORD, "new_password": new_password},
        headers=user.headers,
    )
    assert changed.status_code == 200
    assert (await api.get(f"{AUTH}/me", headers=user.headers)).status_code == 401
    assert (await refresh(api, other["refresh_token"])).status_code == 401
    assert (
        await api.get(f"{AUTH}/me", headers=bearer(changed.json()["access_token"]))
    ).status_code == 200
    old = await api.post(f"{AUTH}/login", json={"email": user.email, "password": PASSWORD})
    assert old.status_code == 401
    await login(api, user.email, new_password)
    trail = await actions(db_session, user.id)
    assert ("auth.password_changed", "FAILURE") in trail
    assert ("auth.password_changed", "SUCCESS") in trail


async def test_audit_trail_for_auth_events_holds_no_secrets(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await register_user(api)
    await api.post(f"{AUTH}/login", json={"email": user.email, "password": "wrong-password-x"})
    await api.post(
        f"{AUTH}/login", json={"email": "ghost@example.com", "password": "whatever-12345"}
    )
    listed = (await api.get(f"{AUTH}/audit-logs", headers=user.headers)).json()
    assert [(i["action"], i["outcome"]) for i in listed["items"]] == [
        ("auth.login", "FAILURE"),
        ("auth.login", "SUCCESS"),
        ("auth.register", "SUCCESS"),
    ]
    assert listed["total"] == 3
    entry = listed["items"][0]
    assert entry["ip"] == "127.0.0.1"
    assert len(entry["request_id"]) >= 8
    assert set(entry["details"]) == {"email_fingerprint"}

    ghost = await db_session.scalar(
        select(AuditLog).where(AuditLog.actor_id.is_(None), AuditLog.action == "auth.login")
    )
    assert ghost is not None  # unknown emails are recorded without an actor
    everything = str([(r.details, r.target_id) for r in await db_session.scalars(select(AuditLog))])
    for secret in ("wrong-password-x", "whatever-12345", PASSWORD, "ghost@example.com", user.email):
        assert secret not in everything


async def test_audit_log_is_append_only(api: httpx.AsyncClient, db_session: AsyncSession) -> None:
    await register_user(api)
    for statement in (
        update(AuditLog).values(outcome="SUCCESS"),
        delete(AuditLog),
        text("TRUNCATE audit_logs"),
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db_session.begin_nested():
                await db_session.execute(statement)


async def test_workspace_audit_log_is_owner_only(
    api: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner, editor, member, stranger = [await register_user(api) for _ in range(4)]
    ws_id = await workspace(api, owner)
    await join(db_session, ws_id, editor, WorkspaceRole.EDITOR)
    await join(db_session, ws_id, member, WorkspaceRole.MEMBER)
    base = f"/api/v1/workspaces/{ws_id}"
    await api.patch(base, json={"name": "Renamed"}, headers=editor.headers)
    comment = await api.post(f"{base}/comments", json={"body": "spam"}, headers=member.headers)
    own = await api.post(f"{base}/comments", json={"body": "mine"}, headers=owner.headers)
    await api.delete(f"{base}/comments/{comment.json()['id']}", headers=owner.headers)
    await api.delete(f"{base}/comments/{own.json()['id']}", headers=owner.headers)

    assert (await api.get(f"{base}/audit-logs", headers=editor.headers)).status_code == 403
    assert (await api.get(f"{base}/audit-logs", headers=stranger.headers)).status_code == 404
    trail = (await api.get(f"{base}/audit-logs", headers=owner.headers)).json()
    assert [i["action"] for i in trail["items"]] == [
        "comment.moderated",  # only the removal of someone else's comment is moderation
        "workspace.updated",
        "workspace.created",
    ]
    moderated, updated, _ = trail["items"]
    assert (moderated["actor_id"], moderated["target_id"]) == (owner.id, comment.json()["id"])
    assert moderated["details"] == {"author_id": member.id}
    assert (updated["actor_id"], updated["details"]) == (editor.id, {"fields": ["name"]})


async def _product(api: httpx.AsyncClient, user: ApiUser) -> str:
    created = await api.post(
        "/api/v1/products",
        json={"brand": f"Acme-{uuid.uuid4().hex[:6]}", "name": "A", "category": "laptop"},
        headers=user.headers,
    )
    pid: str = created.json()["id"]
    return pid


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64, "Linux executable"),
        (b"MZ\x90\x00\x03\x00\x00\x00", "Windows executable"),
        (b"PK\x03\x04\x14\x00\x00\x00", "ZIP archive"),
        (b"#!/bin/sh\nrm -rf /\n", "script"),
        (b"%PDF-1.7\n1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>", "active"),
        (b"   \n", "empty"),
    ],
)
async def test_dangerous_uploads_are_rejected_and_audited(
    api: httpx.AsyncClient, db_session: AsyncSession, content: bytes, reason: str
) -> None:
    user = await register_user(api)
    pid = await _product(api, user)
    response = await api.post(
        f"/api/v1/products/{pid}/sources/upload",
        files={"file": ("../../evil.pdf", content, "application/pdf")},
        headers=user.headers,
    )
    assert response.status_code == 422
    assert reason in response.json()["error"]["message"]
    entry = await db_session.scalar(select(AuditLog).where(AuditLog.action == "source.rejected"))
    assert entry is not None
    assert (entry.outcome, entry.details["filename"]) == ("FAILURE", "evil.pdf")


async def test_upload_filename_is_sanitized_and_text_is_capped(
    api: httpx.AsyncClient, api_app: object, db_session: AsyncSession
) -> None:
    user = await register_user(api)
    pid = await _product(api, user)
    ok = await api.post(
        f"/api/v1/products/{pid}/sources/upload",
        files={"file": ("..\\..\\MZ notes<script>.txt", b"MZ-500 is a fine laptop.", "text/plain")},
        headers=user.headers,
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["source"]["title"] == "MZ notes_script_.txt"
    uploaded = await db_session.scalar(select(AuditLog).where(AuditLog.action == "source.uploaded"))
    assert uploaded is not None
    assert uploaded.details == {
        "filename": "MZ notes_script_.txt",
        "bytes": 24,
        "content_type": "text/plain",
    }

    api_app.state.settings.ingestion_max_text_chars = 1000  # type: ignore[attr-defined]
    too_much = await api.post(
        f"/api/v1/products/{pid}/sources/upload",
        files={"file": ("big.txt", b"word " * 400, "text/plain")},
        headers=user.headers,
    )
    assert too_much.status_code == 422
    assert "too much text" in too_much.json()["error"]["message"]


async def test_revoked_access_token_cannot_open_a_websocket(stack: StackFactory) -> None:
    async with stack() as s:
        user = await register_user(s.http)
        ws_id = await workspace(s.http, user)
        await s.http.post(f"{AUTH}/logout-all", headers=user.headers)
        async with s.ws(ws_id) as sock:
            await sock.send_json({"type": "auth", "token": user.token})
            closed = await sock.closed()
            assert (closed.code, closed.reason) == (4401, "invalid_token")
