"""SSRF-resistant HTTP fetching for user-supplied URLs.

Threats handled:
- Non-HTTP schemes (file://, gopher://, ftp://...)        -> rejected
- Credentials in URLs (http://user:pass@host)             -> rejected
- Non-standard ports (internal admin services)            -> rejected (allow-list)
- Internal hostnames (localhost, *.internal, *.local)     -> rejected before DNS
- Hostnames that RESOLVE to private/loopback/link-local/  -> rejected after DNS
  metadata (169.254.169.254), CGNAT, multicast, reserved,
  IPv4-mapped IPv6 of any of those
- DNS rebinding (resolve public, then connect private)    -> we connect to the exact
                                                             IP we validated
                                                             (Host header + TLS SNI
                                                             keep HTTPS verification)
- Redirects to internal targets                           -> every hop re-validated
- Huge responses / slowloris                              -> byte cap + total timeout
- Unexpected content                                      -> content-type allow-list
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.config import Settings

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[IPAddress]]]

ALLOWED_CONTENT_TYPES = frozenset(
    {"text/html", "application/xhtml+xml", "text/plain", "text/markdown", "application/pdf"}
)
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")
_BLOCKED_HOSTS = frozenset({"localhost", "metadata.google.internal", "metadata"})
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_HTTP_OK = 200
_NUMERIC_HOST = re.compile(r"[0-9.]+|(0x[0-9a-f]+\.?)+[0-9a-fx.]*")
_MAX_URL_LENGTH = 2048
_IPV6 = 6


class UnsafeURLError(ValueError):
    """The URL (or a redirect target) is not allowed to be fetched."""


class FetchError(RuntimeError):
    """The fetch failed for a non-security reason (timeout, size, HTTP error...)."""


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str  # the URL requested
    final_url: str  # after redirects
    status_code: int
    content_type: str
    content: bytes
    elapsed_ms: float


def is_public_ip(ip: IPAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return is_public_ip(ip.ipv4_mapped)
    return bool(
        ip.is_global
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_private
        and not ip.is_unspecified
    )


async def system_resolver(host: str, port: int) -> list[IPAddress]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list({ipaddress.ip_address(info[4][0]) for info in infos})


@dataclass(frozen=True, slots=True)
class _Target:
    scheme: str
    host: str
    port: int
    path_and_query: str


def validate_url_syntax(url: str, allowed_ports: list[int]) -> _Target:
    """Checks that need no DNS. Raises UnsafeURLError."""
    if len(url) > _MAX_URL_LENGTH:
        raise UnsafeURLError("URL is too long")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise UnsafeURLError("only http and https URLs are allowed")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with credentials are not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURLError("URL has no host")
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise UnsafeURLError("internal hostnames are not allowed")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURLError("invalid port") from exc
    if port not in allowed_ports:
        raise UnsafeURLError(f"port {port} is not allowed")
    try:  # literal IPs are checked immediately
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
        # "127.1", "2130706433", "0x7f.1": legacy numeric forms that resolvers
        # expand to IPs. They have no legitimate use here, so refuse them.
        if _NUMERIC_HOST.fullmatch(host):
            raise UnsafeURLError("numeric host forms are not allowed") from None
    if literal is not None and not is_public_ip(literal):
        raise UnsafeURLError("non-public IP addresses are not allowed")
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return _Target(scheme=scheme, host=host, port=port, path_and_query=path)


class SafeFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.resolver = resolver
        self._client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,  # every hop is validated by us
            timeout=httpx.Timeout(settings.ingestion_timeout_seconds),
            headers={
                "User-Agent": settings.ingestion_user_agent,
                "Accept": "text/html,text/plain,application/pdf;q=0.9,*/*;q=0.1",
            },
            trust_env=False,  # never route through env-configured proxies
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _resolve_public(self, target: _Target) -> IPAddress:
        try:
            ips = await self.resolver(target.host, target.port)
        except OSError as exc:
            raise FetchError("could not resolve host") from exc
        if not ips:
            raise FetchError("could not resolve host")
        # Every address must be public: an attacker-controlled DNS record could
        # return one public and one private address.
        if not all(is_public_ip(ip) for ip in ips):
            raise UnsafeURLError("host resolves to a non-public address")
        return sorted(ips, key=lambda ip: (ip.version, int(ip)))[0]

    async def _get_once(self, url: str) -> httpx.Response:
        target = validate_url_syntax(url, self.settings.ingestion_allowed_ports)
        ip = await self._resolve_public(target)
        ip_host = f"[{ip}]" if ip.version == _IPV6 else str(ip)
        pinned = f"{target.scheme}://{ip_host}:{target.port}{target.path_and_query}"
        default_port = 443 if target.scheme == "https" else 80
        host_header = target.host if target.port == default_port else f"{target.host}:{target.port}"
        request = self._client.build_request(
            "GET",
            pinned,
            headers={"Host": host_header},
            # TLS: send SNI and verify the certificate for the *hostname*, while the
            # TCP connection goes to the IP we validated (defeats DNS rebinding).
            extensions={"sni_hostname": target.host},
        )
        return await self._client.send(request, stream=True)

    async def fetch(self, url: str) -> FetchResult:
        started = time.perf_counter()
        deadline = self.settings.ingestion_timeout_seconds
        try:
            async with asyncio.timeout(deadline):
                return await self._fetch(url, started)
        except TimeoutError as exc:
            raise FetchError("fetch timed out") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"fetch failed: {type(exc).__name__}") from exc

    async def _fetch(self, url: str, started: float) -> FetchResult:
        current = url
        for _ in range(self.settings.ingestion_max_redirects + 1):
            response = await self._get_once(current)
            try:
                if response.status_code in _REDIRECT_CODES:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError("redirect without Location header")
                    current = urljoin(current, location)
                    continue
                if response.status_code != _HTTP_OK:
                    raise FetchError(f"unexpected HTTP status {response.status_code}")
                content_type = (
                    response.headers.get("content-type", "").split(";")[0].strip().lower()
                )
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise FetchError(f"content type '{content_type or 'unknown'}' is not supported")
                body = await self._read_limited(response)
                return FetchResult(
                    url=url,
                    final_url=current,
                    status_code=response.status_code,
                    content_type=content_type,
                    content=body,
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                )
            finally:
                await response.aclose()
        raise FetchError("too many redirects")

    async def _read_limited(self, response: httpx.Response) -> bytes:
        limit = self.settings.ingestion_max_bytes
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise FetchError("response is larger than the allowed size")
        chunks: list[bytes] = []
        received = 0
        # aiter_bytes yields *decompressed* bytes, so the cap also stops
        # gzip bombs; Content-Length is never trusted on its own.
        async for chunk in response.aiter_bytes():
            received += len(chunk)
            if received > limit:
                raise FetchError("response is larger than the allowed size")
            chunks.append(chunk)
        return b"".join(chunks)
