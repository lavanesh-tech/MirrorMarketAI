"""ASGI middleware: request correlation, access logging, safe 500s.

Written as *pure ASGI* middleware rather than Starlette's `BaseHTTPMiddleware`:
pure ASGI does not buffer response bodies (important later for streaming LLM
output), and contextvars set here are visible to the route handler.
"""

from __future__ import annotations

import logging
import time

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.request_context import (
    REQUEST_ID_HEADER,
    reset_request_id,
    resolve_request_id,
    set_request_id,
)

logger = logging.getLogger("app.access")

# Probe endpoints are hit every few seconds by Docker/ALB health checks.
# Log them at DEBUG so they don't drown out real traffic at INFO.
_QUIET_PATHS = frozenset({"/api/v1/health"})


class RequestContextMiddleware:
    """Assign a request ID, echo it back, log the request, and hide internal errors.

    For each HTTP request:
    1. Reuse a well-formed `X-Request-ID` from the caller, or mint a new one.
    2. Store it in a contextvar so every log line during the request carries it.
    3. Add it to the response headers so clients can quote it in bug reports.
    4. Emit exactly one structured access-log line (method, path, status, duration).
    5. Convert any unhandled exception into a generic JSON 500 that contains the
       request ID but never the exception text or stack trace.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # WebSockets (Phase 20) and lifespan events pass straight through.
            await self.app(scope, receive, send)
            return

        request_id = resolve_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        token = set_request_id(request_id)
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception("unhandled exception while processing request")
            if response_started:
                # Headers are already on the wire; the only honest option is to
                # let the server abort the connection.
                raise
            status_code = 500
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "An unexpected error occurred.",
                        "request_id": request_id,
                    }
                },
                headers={REQUEST_ID_HEADER: request_id},
            )
            await response(scope, receive, send)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            path = scope.get("path", "")
            logger.log(
                logging.DEBUG if path in _QUIET_PATHS else logging.INFO,
                "request completed",
                extra={
                    "http_method": scope.get("method"),
                    # Path only: query strings can carry tokens or personal data.
                    "http_path": path,
                    "http_status": status_code,
                    "duration_ms": duration_ms,
                },
            )
            reset_request_id(token)
