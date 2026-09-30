from __future__ import annotations

import asyncio
import ipaddress

import httpx
import pytest

from app.core.config import Settings
from app.ingestion.safe_fetch import (
    FetchError,
    SafeFetcher,
    UnsafeURLError,
    is_public_ip,
    validate_url_syntax,
)
from tests.conftest import SettingsFactory
from tests.support.fake_web import FakeWeb

pytestmark = pytest.mark.unit

PORTS = [80, 443]
PUBLIC_IP = "93.184.216.34"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/x",
        "javascript:alert(1)",
        "http://user:pass@example.com/",
        "http://localhost/",
        "http://LOCALHOST./admin",
        "http://printer.local/",
        "http://db.internal/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://127.0.0.1/",
        "http://127.1/",
        "http://0.0.0.0/",
        "http://10.0.0.5/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/",
        "http://[::1]/",
        "http://[fd00::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://example.com:22/",
        "http://example.com:6379/",
        "http:///nohost",
        "http://example.com/" + "a" * 2100,
    ],
)
def test_unsafe_urls_are_rejected_before_any_network(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        validate_url_syntax(url, PORTS)


@pytest.mark.parametrize(
    "url",
    ["https://www.apple.com/macbook-air/specs/", "http://example.com", "https://1.1.1.1/x?q=1"],
)
def test_public_urls_pass_syntax_checks(url: str) -> None:
    validate_url_syntax(url, PORTS)


@pytest.mark.parametrize(
    ("ip", "public"),
    [
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("169.254.169.254", False),
        ("100.64.1.1", False),
        ("224.0.0.1", False),
        ("::ffff:10.0.0.1", False),
        ("fe80::1", False),
        ("::", False),
    ],
)
def test_is_public_ip(ip: str, public: bool) -> None:
    assert is_public_ip(ipaddress.ip_address(ip)) is public


@pytest.fixture
def settings(make_settings: SettingsFactory) -> Settings:
    return make_settings(ingestion_max_bytes=2048, ingestion_timeout_seconds=1)


@pytest.fixture
def web() -> FakeWeb:
    web = FakeWeb()
    web.host("shop.example", PUBLIC_IP)
    return web


async def _fetch(web: FakeWeb, settings: Settings, url: str) -> object:
    fetcher = web.fetcher(settings)
    try:
        return await fetcher.fetch(url)
    finally:
        await fetcher.aclose()


async def test_fetch_success_pins_validated_ip_and_keeps_host(
    web: FakeWeb, settings: Settings
) -> None:
    web.page("shop.example", "/specs", "<html><body>Specs</body></html>")
    fetcher = web.fetcher(settings)
    result = await fetcher.fetch("https://shop.example/specs")
    await fetcher.aclose()

    assert result.content == b"<html><body>Specs</body></html>"
    assert result.content_type == "text/html"
    [request] = web.requests
    assert request.url.host == PUBLIC_IP  # connected to the IP we validated
    assert request.headers["host"] == "shop.example"
    assert request.extensions["sni_hostname"] == "shop.example"
    assert request.headers["user-agent"].startswith("MirrorMarketBot/")


async def test_hostname_resolving_to_private_ip_is_blocked(
    web: FakeWeb, settings: Settings
) -> None:
    web.host("evil.example", "10.0.0.7")
    with pytest.raises(UnsafeURLError, match="non-public"):
        await _fetch(web, settings, "http://evil.example/")
    assert web.requests == []  # never connected


async def test_mixed_public_and_private_dns_answers_are_blocked(
    web: FakeWeb, settings: Settings
) -> None:
    web.host("rebind.example", PUBLIC_IP, "169.254.169.254")
    with pytest.raises(UnsafeURLError):
        await _fetch(web, settings, "http://rebind.example/")


async def test_redirect_to_internal_address_is_blocked(web: FakeWeb, settings: Settings) -> None:
    web.redirect("shop.example", "/go", "http://169.254.169.254/latest/meta-data/")
    with pytest.raises(UnsafeURLError):
        await _fetch(web, settings, "http://shop.example/go")


async def test_redirect_to_public_page_is_followed(web: FakeWeb, settings: Settings) -> None:
    web.host("cdn.example", "1.1.1.1")
    web.redirect("shop.example", "/old", "https://cdn.example/new")
    web.page("cdn.example", "/new", "moved", content_type="text/plain")
    result = await _fetch(web, settings, "http://shop.example/old")
    assert result.final_url == "https://cdn.example/new"  # type: ignore[attr-defined]


async def test_redirect_loop_stops(web: FakeWeb, settings: Settings) -> None:
    web.redirect("shop.example", "/loop", "/loop")
    with pytest.raises(FetchError, match="too many redirects"):
        await _fetch(web, settings, "http://shop.example/loop")


@pytest.mark.parametrize(
    ("content_type", "status", "error"),
    [
        ("application/octet-stream", 200, "not supported"),
        ("image/png", 200, "not supported"),
        ("text/html", 500, "HTTP status 500"),
        ("text/html", 404, "HTTP status 404"),
    ],
)
async def test_bad_responses_are_rejected(
    web: FakeWeb, settings: Settings, content_type: str, status: int, error: str
) -> None:
    web.page("shop.example", "/x", "data", content_type=content_type, status=status)
    with pytest.raises(FetchError, match=error):
        await _fetch(web, settings, "http://shop.example/x")


async def test_oversized_body_is_rejected_even_without_content_length(
    web: FakeWeb, settings: Settings
) -> None:
    web.page("shop.example", "/big", "x" * 5000, content_type="text/plain")
    with pytest.raises(FetchError, match="larger than"):
        await _fetch(web, settings, "http://shop.example/big")


async def test_declared_oversized_content_length_is_rejected(
    web: FakeWeb, settings: Settings
) -> None:
    web.page(
        "shop.example",
        "/liar",
        "small",
        content_type="text/plain",
        headers={"content-length": "999999999"},
    )
    with pytest.raises(FetchError):
        await _fetch(web, settings, "http://shop.example/liar")


async def test_unresolvable_host_fails(web: FakeWeb, settings: Settings) -> None:
    with pytest.raises(FetchError, match="resolve"):
        await _fetch(web, settings, "http://nowhere.example/")


async def test_slow_server_times_out(settings: Settings) -> None:
    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    async def resolve(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        return [ipaddress.ip_address(PUBLIC_IP)]

    fetcher = SafeFetcher(settings, resolver=resolve, transport=httpx.MockTransport(slow))
    with pytest.raises(FetchError, match="timed out"):
        await fetcher.fetch("http://slow.example/")
    await fetcher.aclose()
