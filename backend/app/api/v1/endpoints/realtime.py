"""WebSocket endpoint: `/api/v1/ws/workspaces/{workspace_id}`.

Protocol (JSON text frames):
  client -> {"type": "auth", "token": "<access token>"}   within WS_AUTH_TIMEOUT_SECONDS
  server -> {"type": "ready", "connection_id", "user_id", "role", "presence": [...],
             "heartbeat_seconds": N}
  client -> {"type": "ping"}                              every heartbeat_seconds
  server -> {"type": "pong"}
  server -> {"type": "<event>", "workspace_id", "actor_id", "at", "data": {...}}
            events: presence.joined, presence.left, comment.created, comment.updated,
            comment.deleted, vote.changed, agent_run.completed

The token travels in the first message, never in the URL (URLs end up in access
logs and browser history), and browsers cannot set an Authorization header on a
WebSocket. The server is push-only for data: all writes go through the REST API,
so validation, authorization and idempotency live in one place.

Close codes: 4401 not authenticated / token expired, 4404 not a member,
4408 auth or idle timeout, 4429 too many connections or messages,
1003 not text/JSON, 1009 message too big, 1013 client too slow.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from app.api.deps import SessionFactoryDep, resolve_user
from app.core.config import Settings
from app.core.errors import AppError, AuthenticationError
from app.domain.roles import WorkspaceRole
from app.realtime.bus import EventBus
from app.realtime.hub import Connection, Hub
from app.realtime.presence import Presence
from app.security.tokens import decode_access_token
from app.services.workspaces import WorkspaceService

router = APIRouter(tags=["realtime"])
logger = logging.getLogger(__name__)

CLOSE_UNSUPPORTED = 1003
CLOSE_POLICY = 1008
CLOSE_TOO_BIG = 1009
CLOSE_OVERLOADED = 1013
CLOSE_UNAUTHENTICATED = 4401
CLOSE_NOT_MEMBER = 4404
CLOSE_TIMEOUT = 4408
CLOSE_TOO_MANY = 4429
RATE_WINDOW_SECONDS = 10.0

EVENT_PRESENCE_JOINED = "presence.joined"
EVENT_PRESENCE_LEFT = "presence.left"


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: uuid.UUID
    display_name: str
    role: WorkspaceRole
    expires_at: datetime


def _parse(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


async def _reject(websocket: WebSocket, code: int, reason: str) -> None:
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await websocket.send_json({"type": "error", "code": reason})
        await websocket.close(code=code, reason=reason)


async def _authenticate(
    websocket: WebSocket, workspace_id: uuid.UUID, sessions: SessionFactoryDep, settings: Settings
) -> Identity | None:
    try:
        async with asyncio.timeout(settings.ws_auth_timeout_seconds):
            message = await websocket.receive()
    except TimeoutError:
        await _reject(websocket, CLOSE_TIMEOUT, "auth_timeout")
        return None
    if message["type"] == "websocket.disconnect":
        return None
    payload = _parse(message.get("text") or "")
    token = payload.get("token") if payload and payload.get("type") == "auth" else None
    if not isinstance(token, str):
        await _reject(websocket, CLOSE_UNAUTHENTICATED, "auth_required")
        return None
    try:
        claims = decode_access_token(token, settings)
        async with sessions() as session:
            user = await resolve_user(session, token, settings)
            membership = await WorkspaceService(session).authorize(workspace_id, user)
            return Identity(user.id, user.display_name, membership.role, claims.expires_at)
    except AuthenticationError:
        await _reject(websocket, CLOSE_UNAUTHENTICATED, "invalid_token")
    except AppError:
        # Same answer for "no such workspace" and "not a member": no existence leak.
        await _reject(websocket, CLOSE_NOT_MEMBER, "workspace_not_found")
    return None


async def _writer(websocket: WebSocket, connection: Connection) -> None:
    while True:
        await websocket.send_text(await connection.queue.get())


Close = tuple[int, str]


class _MessageBudget:
    """At most `limit` inbound messages per fixed 10-second window."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.window_start = time.monotonic()
        self.count = 0

    def spend(self) -> bool:
        now = time.monotonic()
        if now - self.window_start > RATE_WINDOW_SECONDS:
            self.window_start, self.count = now, 0
        self.count += 1
        return self.count <= self.limit


def _inspect(
    message: Mapping[str, Any], settings: Settings, budget: _MessageBudget
) -> Close | dict[str, Any]:
    """A parsed JSON object, or the reason to close the connection."""
    text = message.get("text")
    if text is None:
        return CLOSE_UNSUPPORTED, "text_frames_only"
    if len(text.encode()) > settings.ws_max_message_bytes:
        return CLOSE_TOO_BIG, "message_too_big"
    if not budget.spend():
        return CLOSE_TOO_MANY, "too_many_messages"
    payload = _parse(text)
    return payload if payload is not None else (CLOSE_UNSUPPORTED, "invalid_json")


async def _reader(
    websocket: WebSocket,
    connection: Connection,
    presence: Presence,
    settings: Settings,
    expires_at: datetime,
) -> Close | None:
    """Runs until the client leaves (None) or must be closed (code, reason)."""
    budget = _MessageBudget(settings.ws_max_messages_per_10s)
    while True:
        token_left = (expires_at - datetime.now(UTC)).total_seconds()
        try:
            async with asyncio.timeout(max(0.0, min(settings.ws_idle_timeout_seconds, token_left))):
                message = await websocket.receive()
        except TimeoutError:
            if datetime.now(UTC) >= expires_at:
                return CLOSE_UNAUTHENTICATED, "token_expired"
            return CLOSE_TIMEOUT, "idle_timeout"
        if message["type"] == "websocket.disconnect":
            return None
        payload = _inspect(message, settings, budget)
        if isinstance(payload, tuple):
            return payload
        if payload.get("type") == "ping":
            await presence.touch(connection)
            connection.offer('{"type":"pong"}')
        else:
            connection.offer('{"type":"error","code":"unknown_message_type"}')


@router.websocket("/ws/workspaces/{workspace_id}")
async def workspace_socket(
    websocket: WebSocket, workspace_id: uuid.UUID, sessions: SessionFactoryDep
) -> None:
    state = websocket.app.state
    settings: Settings = state.settings
    hub: Hub = state.hub
    bus: EventBus = state.bus
    presence: Presence = state.presence

    # Browsers always send Origin; only our own front-end origins may connect.
    origin = websocket.headers.get("origin")
    if origin and settings.cors_allowed_origins and origin not in settings.cors_allowed_origins:
        await websocket.close(code=CLOSE_POLICY)  # before accept: the handshake gets HTTP 403
        return
    await websocket.accept()

    identity = await _authenticate(websocket, workspace_id, sessions, settings)
    if identity is None:
        return
    if hub.count(workspace_id, identity.user_id) >= settings.ws_max_connections_per_user:
        await _reject(websocket, CLOSE_TOO_MANY, "too_many_connections")
        return

    connection = Connection(
        workspace_id, identity.user_id, asyncio.Queue(maxsize=settings.ws_send_queue_size)
    )
    user_key = str(identity.user_id)
    already_online = user_key in await presence.users(workspace_id)
    hub.join(connection)
    await presence.touch(connection)
    online = await presence.users(workspace_id)
    connection.offer(
        json.dumps(
            {
                "type": "ready",
                "connection_id": connection.id,
                "user_id": user_key,
                "role": identity.role.value,
                "presence": online,
                "heartbeat_seconds": settings.ws_heartbeat_seconds,
            }
        )
    )
    who = {"user_id": user_key, "display_name": identity.display_name}
    if not already_online:
        await bus.publish(workspace_id, EVENT_PRESENCE_JOINED, who, identity.user_id)

    writer = asyncio.create_task(_writer(websocket, connection))
    reader = asyncio.create_task(
        _reader(websocket, connection, presence, settings, identity.expires_at)
    )
    overflow = asyncio.create_task(connection.overflowed.wait())
    try:
        await asyncio.wait({writer, reader, overflow}, return_when=asyncio.FIRST_COMPLETED)
        close: Close | None = None
        if overflow.done():
            close = (CLOSE_OVERLOADED, "slow_consumer")
        elif reader.done() and reader.exception() is None:
            close = reader.result()
        if close is not None:
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await websocket.close(code=close[0], reason=close[1])
    finally:
        for task in (writer, reader, overflow):
            task.cancel()
        await asyncio.gather(writer, reader, overflow, return_exceptions=True)
        hub.leave(connection)
        await presence.remove(connection)
        if user_key not in await presence.users(workspace_id):
            await bus.publish(workspace_id, EVENT_PRESENCE_LEFT, who, identity.user_id)
