"""A minimal in-process WebSocket client that speaks ASGI directly.

Starlette's TestClient runs the app in another thread and event loop, which
cannot share the test's database session. This client drives the app in the
test's own loop, like `httpx.ASGITransport` does for HTTP.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from types import TracebackType
from typing import Any, Self

from starlette.types import ASGIApp, Message

TIMEOUT = 3.0


class Closed(Exception):  # noqa: N818 - reads naturally in tests
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(f"closed {code} {reason}")
        self.code = code
        self.reason = reason


class WsClient:
    def __init__(self, app: ASGIApp, path: str, headers: dict[str, str] | None = None) -> None:
        self._app = app
        self._scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "scheme": "ws",
            "http_version": "1.1",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "subprotocols": [],
            "state": {},
        }
        self._to_app: asyncio.Queue[Message] = asyncio.Queue()
        self._from_app: asyncio.Queue[Message] = asyncio.Queue()
        self._task: asyncio.Future[None] | None = None
        self.accepted = False

    async def __aenter__(self) -> Self:
        self._task = asyncio.ensure_future(
            self._app(self._scope, self._to_app.get, self._from_app.put)
        )
        await self._to_app.put({"type": "websocket.connect"})
        first = await self._next()
        if first["type"] == "websocket.accept":
            self.accepted = True
        else:
            await self._from_app.put(first)  # a close before accept: let the test read it
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.disconnect()

    async def _next(self, wait: float = TIMEOUT) -> Message:
        async with asyncio.timeout(wait):
            return await self._from_app.get()

    async def send_json(self, payload: Any) -> None:
        await self.send_text(json.dumps(payload))

    async def send_text(self, text: str) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": text})

    async def send_bytes(self, data: bytes) -> None:
        await self._to_app.put({"type": "websocket.receive", "bytes": data})

    async def receive(self, wait: float = TIMEOUT) -> dict[str, Any]:
        """The next JSON message; raises Closed if the server closed instead."""
        message = await self._next(wait)
        if message["type"] == "websocket.close":
            raise Closed(message.get("code", 1000), message.get("reason") or "")
        payload: dict[str, Any] = json.loads(message["text"])
        return payload

    async def until(self, event_type: str, wait: float = TIMEOUT) -> dict[str, Any]:
        """Skip messages until one of `event_type` arrives."""
        async with asyncio.timeout(wait):
            while True:
                message = await self.receive(wait)
                if message["type"] == event_type:
                    return message

    async def closed(self, wait: float = TIMEOUT) -> Closed:
        """Skip messages until the server closes; returns the close code and reason."""
        async with asyncio.timeout(wait):
            while True:
                try:
                    await self.receive(wait)
                except Closed as closed:
                    return closed

    async def silent(self, seconds: float = 0.15) -> bool:
        try:
            await self._next(seconds)
        except TimeoutError:
            return True
        return False

    async def disconnect(self) -> None:
        if self._task is None:
            return
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            async with asyncio.timeout(TIMEOUT):
                await self._task
        self._task = None
