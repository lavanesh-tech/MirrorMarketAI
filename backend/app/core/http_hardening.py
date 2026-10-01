"""HTTP hardening as pure ASGI middleware: security headers and a request body limit.

Headers (on every HTTP response):
- `X-Content-Type-Options: nosniff`  browsers must not guess content types
- `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'`  no clickjacking
- `Content-Security-Policy: default-src 'none'`  this API serves JSON, so a response
  that is somehow rendered as HTML may load nothing (the Swagger page is exempt)
- `Referrer-Policy: no-referrer`, `Permissions-Policy`, `Cross-Origin-Resource-Policy`
- `Cache-Control: no-store` unless the handler set its own  responses contain private data
- `Strict-Transport-Security` outside local/test  browsers must use HTTPS

Body limit: requests larger than `max_request_body_bytes` get 413. A declared
Content-Length is rejected up front; a chunked body is counted as it streams,
so a client cannot bypass the limit by omitting the header.
"""

from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import RequestTooLargeError, error_body

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
STATIC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Resource-Policy": "same-site",
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts_max_age: int, docs_prefix: str) -> None:
        self.app = app
        self.hsts = f"max-age={hsts_max_age}; includeSubDomains" if hsts_max_age else None
        self.docs_prefix = docs_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_docs = scope.get("path", "").startswith(self.docs_prefix)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in STATIC_HEADERS.items():
                    headers[name] = value
                if not is_docs:  # Swagger UI needs scripts and styles from its CDN
                    headers["Content-Security-Policy"] = API_CSP
                if "cache-control" not in headers:
                    headers["Cache-Control"] = "no-store"
                if self.hsts:
                    headers["Strict-Transport-Security"] = self.hsts
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _BodyTooLargeError(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, receive, send)
            return

        received = 0
        started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLargeError:
            if started:
                raise
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        error = RequestTooLargeError
        response = JSONResponse(
            status_code=error.status_code,
            content=error_body(error.code, error.message),
            headers={"Connection": "close"},
        )
        await response(scope, receive, send)
