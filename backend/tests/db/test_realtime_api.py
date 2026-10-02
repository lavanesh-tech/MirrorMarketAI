"""WebSocket hub, presence and realtime events, single process (no Redis)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.roles import WorkspaceRole
from app.security.tokens import create_access_token, create_realtime_ticket
from tests.db.conftest import ApiUser, StackFactory, join, register_user, workspace

pytestmark = [pytest.mark.db, pytest.mark.api]


async def test_ready_presence_ping_and_leave(stack: StackFactory, db_session: AsyncSession) -> None:
    async with stack() as s:
        owner, member = await register_user(s.http), await register_user(s.http)
        ws_id = await workspace(s.http, owner)
        await join(db_session, ws_id, member, WorkspaceRole.MEMBER)

        first = s.ws(ws_id)
        await first.__aenter__()
        await first.send_json({"type": "auth", "token": owner.token})
        ready = await first.receive()
        assert ready["type"] == "ready"
        assert (ready["role"], ready["presence"]) == ("OWNER", [owner.id])
        assert ready["heartbeat_seconds"] == s.settings.ws_heartbeat_seconds
        assert (await first.receive())["type"] == "presence.joined"  # own join

        second = await s.connect(ws_id, member)
        joined = await first.until("presence.joined")
        assert joined["data"] == {"user_id": member.id, "display_name": "Test User"}
        assert joined["actor_id"] == member.id

        await first.send_json({"type": "ping"})
        assert (await first.until("pong")) == {"type": "pong"}
        await first.send_json({"type": "nope"})
        assert (await first.until("error"))["code"] == "unknown_message_type"

        listed = await s.http.get(f"/api/v1/workspaces/{ws_id}/presence", headers=owner.headers)
        assert sorted(listed.json()["user_ids"]) == sorted([owner.id, member.id])

        await second.disconnect()
        assert (await first.until("presence.left"))["data"]["user_id"] == member.id
        await first.disconnect()
        assert s.app.state.hub.total == 0


async def test_second_tab_does_not_announce_again(stack: StackFactory) -> None:
    async with stack() as s:
        owner = await register_user(s.http)
        ws_id = await workspace(s.http, owner)
        tab1 = await s.connect(ws_id, owner)
        await tab1.until("presence.joined")
        tab2 = await s.connect(ws_id, owner)
        assert await tab1.silent()
        await tab2.disconnect()
        assert await tab1.silent()  # still online through tab1: no presence.left
        await tab1.disconnect()


async def test_events_reach_members_of_that_workspace_only(
    stack: StackFactory, db_session: AsyncSession
) -> None:
    async with stack() as s:
        owner, viewer, stranger = [await register_user(s.http) for _ in range(3)]
        ws_id = await workspace(s.http, owner)
        other_ws = await workspace(s.http, stranger)
        await join(db_session, ws_id, viewer, WorkspaceRole.VIEWER)
        sockets = [
            await s.connect(ws_id, owner),
            await s.connect(ws_id, viewer),
            await s.connect(other_ws, stranger),
        ]
        for sock in sockets[:2]:
            await sock.until("presence.joined")
        await sockets[1].silent()
        await sockets[2].until("presence.joined")

        created = await s.http.post(
            f"/api/v1/workspaces/{ws_id}/comments", json={"body": " Hello "}, headers=owner.headers
        )
        assert created.status_code == 201
        for sock in sockets[:2]:
            event = await sock.until("comment.created")
            assert event["data"]["body"] == "Hello"
            assert event["data"]["id"] == created.json()["id"]
            assert (event["workspace_id"], event["actor_id"]) == (ws_id, owner.id)
            assert datetime.fromisoformat(event["at"]).tzinfo is not None
        assert await sockets[2].silent()
        for sock in sockets:
            await sock.disconnect()


async def test_authentication_failures(stack: StackFactory, db_session: AsyncSession) -> None:
    async with stack(ws_auth_timeout_seconds=0.2) as s:
        owner, stranger = await register_user(s.http), await register_user(s.http)
        ws_id = await workspace(s.http, owner)

        async def attempt(first_message: object | None, target: str = ws_id) -> tuple[int, str]:
            async with s.ws(target) as sock:
                if isinstance(first_message, str):
                    await sock.send_text(first_message)
                elif first_message is not None:
                    await sock.send_json(first_message)
                closed = await sock.closed()
                return closed.code, closed.reason

        assert await attempt(None) == (4408, "auth_timeout")
        assert await attempt("not json") == (4401, "auth_required")
        assert await attempt({"type": "ping"}) == (4401, "auth_required")
        assert await attempt({"type": "auth", "token": 5}) == (4401, "auth_required")
        assert await attempt({"type": "auth", "token": "x.y.z"}) == (4401, "invalid_token")
        assert await attempt({"type": "auth", "token": stranger.token}) == (
            4404,
            "workspace_not_found",
        )
        missing = str(uuid.uuid4())
        assert await attempt({"type": "auth", "token": owner.token}, missing) == (
            4404,
            "workspace_not_found",
        )
        ghost = create_access_token(uuid.uuid4(), s.settings).token
        assert await attempt({"type": "auth", "token": ghost}) == (4401, "invalid_token")
        assert s.app.state.hub.total == 0


async def test_origin_allow_list(stack: StackFactory) -> None:
    async with stack(cors_allowed_origins=["https://app.example.com"]) as s:
        owner = await register_user(s.http)
        ws_id = await workspace(s.http, owner)
        async with s.ws(ws_id, origin="https://evil.example.com") as sock:
            assert sock.accepted is False
            assert (await sock.closed()).code == 1008
        async with s.ws(ws_id, origin="https://app.example.com") as sock:
            assert sock.accepted is True


async def test_connection_and_message_limits(stack: StackFactory) -> None:
    limits = {
        "ws_max_connections_per_user": 2,
        "ws_max_message_bytes": 64,
        "ws_max_messages_per_10s": 3,
    }
    async with stack(**limits) as s:
        owner = await register_user(s.http)
        ws_id = await workspace(s.http, owner)
        one, two = await s.connect(ws_id, owner), await s.connect(ws_id, owner)
        async with s.ws(ws_id) as third:
            await third.send_json({"type": "auth", "token": owner.token})
            assert (await third.closed()).reason == "too_many_connections"

        await one.send_text("x" * 65)
        assert (await one.closed()).code == 1009
        await asyncio.sleep(0.05)  # let the handler finish its cleanup
        assert s.app.state.hub.total == 1
        await two.send_bytes(b"\x00")
        assert (await two.closed()).code == 1003

        flood = await s.connect(ws_id, owner)
        for _ in range(4):
            await flood.send_json({"type": "ping"})
        closed = await flood.closed()
        assert (closed.code, closed.reason) == (4429, "too_many_messages")

        garbage = await s.connect(ws_id, owner)
        await garbage.send_text("[1, 2]")
        assert (await garbage.closed()).reason == "invalid_json"


async def test_idle_and_token_expiry_close_the_socket(stack: StackFactory) -> None:
    async with stack(ws_idle_timeout_seconds=0.2) as s:
        owner = await register_user(s.http)
        ws_id = await workspace(s.http, owner)
        idle = await s.connect(ws_id, owner)
        closed = await idle.closed()
        assert (closed.code, closed.reason) == (4408, "idle_timeout")

    async with stack() as s:
        owner = await register_user(s.http)
        ws_id = await workspace(s.http, owner)
        ttl = timedelta(minutes=s.settings.jwt_access_token_ttl_minutes)
        almost_expired = create_access_token(
            uuid.UUID(owner.id), s.settings, now=datetime.now(UTC) - ttl + timedelta(seconds=1)
        )
        async with s.ws(ws_id) as sock:
            await sock.send_json({"type": "auth", "token": almost_expired.token})
            await sock.until("ready")
            closed = await sock.closed()
            assert (closed.code, closed.reason) == (4401, "token_expired")


async def test_slow_consumer_is_disconnected_others_keep_receiving(stack: StackFactory) -> None:
    async with stack(ws_send_queue_size=2) as s:
        owner = await register_user(s.http)
        ws_id = await workspace(s.http, owner)
        sock = await s.connect(ws_id, owner)
        await sock.until("presence.joined")
        hub = s.app.state.hub
        # No await between these: the writer task cannot drain, like a stalled browser.
        delivered = [hub.broadcast(uuid.UUID(ws_id), f'{{"type":"n","i":{i}}}') for i in range(3)]
        assert delivered == [1, 1, 0]
        closed = await sock.closed()
        assert (closed.code, closed.reason) == (1013, "slow_consumer")
        await asyncio.sleep(0.05)
        assert hub.total == 0


async def test_votes_and_vote_events(stack: StackFactory, db_session: AsyncSession) -> None:
    async with stack() as s:
        owner, member, viewer = [await register_user(s.http) for _ in range(3)]
        ws_id = await workspace(s.http, owner)
        await join(db_session, ws_id, member, WorkspaceRole.MEMBER)
        await join(db_session, ws_id, viewer, WorkspaceRole.VIEWER)
        pids = []
        for name in ("A", "B"):
            product = await s.http.post(
                "/api/v1/products",
                json={"brand": f"Acme-{uuid.uuid4().hex[:6]}", "name": name, "category": "laptop"},
                headers=owner.headers,
            )
            pids.append(product.json()["id"])
        base = f"/api/v1/workspaces/{ws_id}"
        await s.http.post(f"{base}/products", json={"product_id": pids[0]}, headers=owner.headers)
        vote_url = f"{base}/products/{pids[0]}/vote"
        sock = await s.connect(ws_id, viewer)
        await sock.until("presence.joined")

        up = await s.http.put(vote_url, json={"value": 1}, headers=owner.headers)
        assert up.json() == {"product_id": pids[0], "up": 1, "down": 0, "score": 1, "my_vote": 1}
        event = await sock.until("vote.changed")
        assert event["data"] == {"product_id": pids[0], "up": 1, "down": 0, "score": 1}

        await s.http.put(vote_url, json={"value": 1}, headers=owner.headers)  # same vote again
        await s.http.put(vote_url, json={"value": -1}, headers=member.headers)
        changed = await s.http.put(vote_url, json={"value": -1}, headers=owner.headers)
        assert changed.json() == {
            "product_id": pids[0],
            "up": 0,
            "down": 2,
            "score": -2,
            "my_vote": -1,
        }
        tallies = (await s.http.get(f"{base}/votes", headers=viewer.headers)).json()["items"]
        assert tallies == [{"product_id": pids[0], "up": 0, "down": 2, "score": -2, "my_vote": 0}]

        removed = await s.http.put(vote_url, json={"value": 0}, headers=owner.headers)
        assert (removed.json()["down"], removed.json()["my_vote"]) == (1, 0)
        await s.http.put(vote_url, json={"value": 0}, headers=member.headers)
        assert (await s.http.get(f"{base}/votes", headers=owner.headers)).json() == {"items": []}

        assert (
            await s.http.put(vote_url, json={"value": 1}, headers=viewer.headers)
        ).status_code == 403
        assert (
            await s.http.put(vote_url, json={"value": 2}, headers=owner.headers)
        ).status_code == 422
        not_in_ws = await s.http.put(
            f"{base}/products/{pids[1]}/vote", json={"value": 1}, headers=owner.headers
        )
        assert not_in_ws.json()["error"]["code"] == "workspace_product_not_found"
        await sock.disconnect()


async def test_comment_rules(stack: StackFactory, db_session: AsyncSession) -> None:  # noqa: PLR0915
    async with stack() as s:
        owner, member, viewer, stranger = [await register_user(s.http) for _ in range(4)]
        ws_id = await workspace(s.http, owner)
        other_ws = await workspace(s.http, stranger)
        await join(db_session, ws_id, member, WorkspaceRole.MEMBER)
        await join(db_session, ws_id, viewer, WorkspaceRole.VIEWER)
        product = await s.http.post(
            "/api/v1/products",
            json={"brand": f"Acme-{uuid.uuid4().hex[:6]}", "name": "A", "category": "laptop"},
            headers=owner.headers,
        )
        pid = product.json()["id"]
        base = f"/api/v1/workspaces/{ws_id}"
        url = f"{base}/comments"
        await s.http.post(f"{base}/products", json={"product_id": pid}, headers=owner.headers)

        async def post(user: ApiUser, **body: object) -> httpx.Response:
            return await s.http.post(url, json=body, headers=user.headers)

        top = (await post(member, body="General")).json()
        on_product = (await post(member, body="About A", product_id=pid)).json()
        reply = await post(owner, body="Agree", parent_id=top["id"])
        assert reply.status_code == 201
        assert reply.json()["parent_id"] == top["id"]
        assert top["author_name"] == "Test User"

        assert (await post(viewer, body="x")).status_code == 403
        assert (await post(stranger, body="x")).status_code == 404
        assert (await post(member, body="   ")).status_code == 422
        assert (await post(member, body="x" * 4001)).status_code == 422
        unknown_product = await post(member, body="x", product_id=str(uuid.uuid4()))
        assert unknown_product.json()["error"]["code"] == "workspace_product_not_found"
        for parent in (reply.json()["id"], on_product["id"]):  # reply-to-reply, other thread
            bad = await post(member, body="x", parent_id=parent)
            assert bad.json()["error"]["code"] == "invalid_comment_parent"
        assert (await post(member, body="x", parent_id=str(uuid.uuid4()))).status_code == 404

        listed = (await s.http.get(url, headers=viewer.headers)).json()
        # One test = one transaction = one created_at, so order is not asserted here.
        assert sorted(c["body"] for c in listed["items"]) == ["About A", "Agree", "General"]
        assert listed["page"]["total"] == 3
        only_product = await s.http.get(url, params={"product_id": pid}, headers=viewer.headers)
        assert [c["body"] for c in only_product.json()["items"]] == ["About A"]
        general = await s.http.get(
            url,
            params={"workspace_level_only": True, "limit": 1, "offset": 1},
            headers=viewer.headers,
        )
        assert len(general.json()["items"]) == 1
        assert general.json()["page"]["total"] == 2

        one = f"{url}/{top['id']}"
        assert (
            await s.http.patch(one, json={"body": "x"}, headers=owner.headers)
        ).status_code == 403
        edited = await s.http.patch(one, json={"body": "General!"}, headers=member.headers)
        assert (edited.json()["body"], edited.json()["edited_at"] is not None) == ("General!", True)
        foreign = f"/api/v1/workspaces/{other_ws}/comments/{top['id']}"
        assert (await s.http.delete(foreign, headers=stranger.headers)).status_code == 404

        mine = (await post(owner, body="Owner note")).json()
        assert (
            await s.http.delete(f"{url}/{mine['id']}", headers=member.headers)
        ).status_code == 403
        deleted = await s.http.delete(one, headers=owner.headers)  # OWNER moderates
        assert (deleted.json()["body"], deleted.json()["deleted"]) == (None, True)
        assert (await s.http.delete(one, headers=member.headers)).status_code == 200  # idempotent
        assert (
            await s.http.patch(one, json={"body": "x"}, headers=member.headers)
        ).status_code == 404
        assert (await post(member, body="x", parent_id=top["id"])).status_code == 422
        after = (await s.http.get(url, headers=viewer.headers)).json()["items"]
        assert [c["deleted"] for c in after if c["id"] == top["id"]] == [True]
        assert sum(c["body"] is None for c in after) == 1


async def test_comment_edit_delete_and_agent_events(stack: StackFactory) -> None:
    async with stack() as s:
        owner = await register_user(s.http)
        ws_id = await workspace(s.http, owner)
        base = f"/api/v1/workspaces/{ws_id}"
        sock = await s.connect(ws_id, owner)
        comment = (
            await s.http.post(f"{base}/comments", json={"body": "a"}, headers=owner.headers)
        ).json()
        one = f"{base}/comments/{comment['id']}"
        await s.http.patch(one, json={"body": "b"}, headers=owner.headers)
        assert (await sock.until("comment.updated"))["data"]["body"] == "b"
        await s.http.delete(one, headers=owner.headers)
        gone = await sock.until("comment.deleted")
        assert gone["data"] == {"id": comment["id"], "product_id": None, "parent_id": None}

        asked = await s.http.post(
            f"{base}/ask", json={"question": "Is it loud?"}, headers=owner.headers
        )
        assert asked.status_code == 201
        event = await sock.until("agent_run.completed")
        assert event["data"] == {
            "run_id": asked.json()["id"],
            "agent": "ask",
            "product_id": None,
            "status": "SUCCEEDED",
        }
        await sock.disconnect()


async def test_realtime_ticket_opens_only_its_own_workspace(stack: StackFactory) -> None:
    async with stack() as s:
        owner, stranger = await register_user(s.http), await register_user(s.http)
        ws_id = await workspace(s.http, owner)
        other_id = await workspace(s.http, owner)

        async def ticket(user: ApiUser, target: str = ws_id) -> httpx.Response:
            return await s.http.post(
                f"/api/v1/workspaces/{target}/realtime-ticket", headers=user.headers
            )

        async def attempt(message: dict[str, object], target: str = ws_id) -> tuple[int, str]:
            async with s.ws(target) as sock:
                await sock.send_json(message)
                closed = await sock.closed()
                return closed.code, closed.reason

        assert (await ticket(stranger)).status_code == 404  # not a member: no ticket

        issued = (await ticket(owner)).json()
        async with s.ws(ws_id) as sock:
            await sock.send_json({"type": "auth", "ticket": issued["ticket"]})
            ready = await sock.receive()
            assert (ready["type"], ready["user_id"], ready["role"]) == ("ready", owner.id, "OWNER")

        # Bound to one workspace, and not an access token.
        assert await attempt({"type": "auth", "ticket": issued["ticket"]}, other_id) == (
            4401,
            "invalid_token",
        )
        as_bearer = await s.http.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {issued['ticket']}"}
        )
        assert as_bearer.status_code == 401
        # An access token is not a ticket either, and an expired ticket is refused.
        assert await attempt({"type": "auth", "ticket": owner.token}) == (4401, "invalid_token")
        old = create_realtime_ticket(
            uuid.UUID(owner.id),
            uuid.UUID(ws_id),
            s.settings,
            now=datetime.now(UTC) - timedelta(minutes=2),
        ).token
        assert await attempt({"type": "auth", "ticket": old}) == (4401, "invalid_token")

        # "Log out everywhere" invalidates tickets issued before it.
        fresh = (await ticket(owner)).json()["ticket"]
        assert (
            await s.http.post("/api/v1/auth/logout-all", headers=owner.headers)
        ).status_code == 204
        assert await attempt({"type": "auth", "ticket": fresh}) == (4401, "invalid_token")
