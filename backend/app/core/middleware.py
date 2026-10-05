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
    reset_client_ip,
    reset_request_id,
    resolve_request_id,
    set_client_ip,
    set_request_id,
)
from app.telemetry import metrics

logger = logging.getLogger("app.access")

# Probe endpoints are hit every few seconds by Docker/ALB health checks.
# Log them at DEBUG so they don't drown out real traffic at INFO.
METRICS_PATH = "/metrics"
_QUIET_PATHS = frozenset({"/api/v1/health", "/api/v1/ready", METRICS_PATH})


def route_template(scope: Scope) -> str:
    """The matched route as written in the code, prefix included, or "unmatched".

    FastAPI keeps the full template of a route inside an included router in its own
    part of the scope; `scope["route"].path` alone is relative to that router.
    """
    effective = scope.get("fastapi", {}).get("effective_route_context")
    path = getattr(effective, "path", None) or getattr(scope.get("route"), "path", None)
    return path if isinstance(path, str) else metrics.UNMATCHED_ROUTE


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
        client = scope.get("client")
        ip_token = set_client_ip(client[0] if client else None)
        started = time.perf_counter()
        status_code = 500
        response_started = False
        metrics.HTTP_IN_PROGRESS.inc()

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
            elapsed = time.perf_counter() - started
            duration_ms = round(elapsed * 1000, 2)
            path = scope.get("path", "")
            metrics.HTTP_IN_PROGRESS.dec()
            if path != METRICS_PATH:  # scrapes would dominate the request metrics
                # The matched route's template ("/workspaces/{workspace_id}"), never the
                # path itself: one label value per id would be one time series per id.
                metrics.observe_http(
                    scope.get("method", ""), route_template(scope), status_code, elapsed
                )
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
            reset_client_ip(ip_token)
            reset_request_id(token)
