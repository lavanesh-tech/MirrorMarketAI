"""A fake internet for ingestion tests: DNS answers + HTTP responses, no network."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable

import httpx

from app.core.config import Settings
from app.ingestion.safe_fetch import IPAddress, SafeFetcher

Handler = Callable[[httpx.Request], httpx.Response]


class FakeWeb:
    """Maps hostnames to IPs and (host, path) to responses; records requests."""

    def __init__(self) -> None:
        self.dns: dict[str, list[str]] = {}
        self.routes: dict[tuple[str, str], Handler] = {}
        self.requests: list[httpx.Request] = []

    def host(self, name: str, *ips: str) -> None:
        self.dns[name] = list(ips)

    def page(
        self,
        host: str,
        path: str,
        body: bytes | str,
        *,
        content_type: str = "text/html; charset=utf-8",
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        content = body.encode() if isinstance(body, str) else body

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status, content=content, headers={"content-type": content_type, **(headers or {})}
            )

        self.routes[(host, path)] = handler

    def redirect(self, host: str, path: str, location: str, status: int = 302) -> None:
        self.routes[(host, path)] = lambda _: httpx.Response(status, headers={"location": location})

    async def resolve(self, host: str, port: int) -> list[IPAddress]:
        if host not in self.dns:
            raise OSError("NXDOMAIN")
        return [ipaddress.ip_address(ip) for ip in self.dns[host]]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.headers["host"].split(":")[0]
        handler = self.routes.get((host, request.url.path))
        if handler is None:
            return httpx.Response(404, headers={"content-type": "text/plain"})
        return handler(request)

    def fetcher(self, settings: Settings) -> SafeFetcher:
        return SafeFetcher(
            settings, resolver=self.resolve, transport=httpx.MockTransport(self._handle)
        )
