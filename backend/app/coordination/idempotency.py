"""`Idempotency-Key` support for unsafe requests (POST/PATCH), as pure ASGI middleware.

A client that retries after a timeout must not run an agent twice or create two
workspaces. With an `Idempotency-Key` header:

1. The key is scoped to the caller (hash of the Authorization header), method
   and path, so two users can never see each other's stored responses.
2. `SET NX` claims the key with a "processing" record. A concurrent duplicate
   gets 409 `idempotency_in_progress` (with Retry-After) instead of running twice.
3. The response is captured. Statuses below 500 are stored for the TTL and
   replayed byte-for-byte (plus `Idempotent-Replayed: true`) on later retries.
   A 5xx or an exception releases the key, so the client can retry for real.
4. Reusing a key with a different body is a client bug: 422 `idempotency_key_reused`.

Requests without the header, and all requests when Redis is not configured or
unreachable, pass straight through.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_body
from app.core.request_context import REQUEST_ID_HEADER

logger = logging.getLogger(__name__)

HEADER = "idempotency-key"
REPLAYED_HEADER = "Idempotent-Replayed"
_METHODS = frozenset({"POST", "PATCH"})
_VALID_KEY = re.compile(r"^[\x21-\x7e]{1,255}$")
_SERVER_ERROR = 500


def _error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> Response:
    return JSONResponse(status_code=status, content=error_body(code, message), headers=headers)


class IdempotencyMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        prefix: str,
        ttl_seconds: int,
        lock_seconds: int,
        max_body_bytes: int,
    ) -> None:
        self.app = app
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds
        self.lock_seconds = lock_seconds
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _METHODS:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        key = headers.get(HEADER)
        redis: Redis | None = getattr(scope["app"].state, "redis", None)
        if key is None or redis is None:
            await self.app(scope, receive, send)
            return
        if not _VALID_KEY.fullmatch(key):
            await _error(400, "invalid_idempotency_key", "Use 1-255 visible ASCII characters.")(
                scope, receive, send
            )
            return

        body, more = await self._read_body(receive)
        if more:
            message = "The request body is too large to use with an Idempotency-Key."
            await _error(413, "idempotency_body_too_large", message)(scope, receive, send)
            return

        caller = hashlib.sha256(headers.get("authorization", "anonymous").encode()).hexdigest()
        target = hashlib.sha256(f"{scope['method']} {scope['path']} {key}".encode()).hexdigest()
        redis_key = f"{self.prefix}idem:{caller[:32]}:{target[:32]}"
        fingerprint = hashlib.sha256(body).hexdigest()
        replay_receive = self._replaying(body, receive)

        try:
            claimed = await redis.set(
                redis_key,
                json.dumps({"state": "processing", "fp": fingerprint}),
                nx=True,
                ex=self.lock_seconds,
            )
            existing = None if claimed else await redis.get(redis_key)
        except RedisError:
            logger.warning("idempotency store unavailable; processing without it")
            await self.app(scope, replay_receive, send)
            return

        if not claimed:
            await self._answer_duplicate(existing, fingerprint)(scope, receive, send)
            return
        await self._run_and_store(redis, redis_key, fingerprint, scope, replay_receive, send)

    async def _read_body(self, receive: Receive) -> tuple[bytes, bool]:
        """The whole body, or (partial, True) once it exceeds the limit."""
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            chunk = message.get("body", b"")
            size += len(chunk)
            chunks.append(chunk)
            if size > self.max_body_bytes:
                return b"", True
            if not message.get("more_body", False):
                return b"".join(chunks), False

    @staticmethod
    def _replaying(body: bytes, receive: Receive) -> Receive:
        sent = False

        async def _receive() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()  # e.g. http.disconnect

        return _receive

    def _answer_duplicate(self, raw: bytes | str | None, fingerprint: str) -> Response:
        if raw is None:  # the claim expired between SET and GET
            return _error(
                409, "idempotency_in_progress", "Retry shortly.", headers={"Retry-After": "1"}
            )
        record: dict[str, Any] = json.loads(raw)
        if record["fp"] != fingerprint:
            return _error(
                422,
                "idempotency_key_reused",
                "This Idempotency-Key was used with a different request body.",
            )
        if record["state"] == "processing":
            return _error(
                409,
                "idempotency_in_progress",
                "A request with this Idempotency-Key is still being processed.",
                headers={"Retry-After": "1"},
            )
        response = Response(content=base64.b64decode(record["body"]), status_code=record["status"])
        response.raw_headers = [
            (name.encode("latin-1"), value.encode("latin-1")) for name, value in record["headers"]
        ] + [(REPLAYED_HEADER.lower().encode(), b"true")]
        return response

    async def _run_and_store(
        self,
        redis: Redis,
        redis_key: str,
        fingerprint: str,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        status = _SERVER_ERROR
        response_headers: list[tuple[str, str]] = []
        chunks: list[bytes] = []

        async def capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                response_headers.extend(
                    (k.decode("latin-1"), v.decode("latin-1"))
                    for k, v in message.get("headers", [])
                    if k.decode("latin-1").lower() != REQUEST_ID_HEADER.lower()
                )
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, capture)
        except BaseException:
            await self._release(redis, redis_key)
            raise
        if status >= _SERVER_ERROR:
            await self._release(redis, redis_key)
            return
        record = {
            "state": "done",
            "fp": fingerprint,
            "status": status,
            "headers": response_headers,
            "body": base64.b64encode(b"".join(chunks)).decode("ascii"),
        }
        try:
            await redis.set(redis_key, json.dumps(record), ex=self.ttl_seconds)
        except RedisError:
            logger.warning("could not store idempotent response")

    @staticmethod
    async def _release(redis: Redis, redis_key: str) -> None:
        try:
            await redis.delete(redis_key)
        except RedisError:
            logger.warning("could not release idempotency key; it expires with its lock")
