from __future__ import annotations

import json
import math

import httpx
import pytest

from app.core.config import Settings
from app.providers.embeddings import (
    EmbeddingError,
    HashingEmbeddingProvider,
    OpenAIEmbeddingProvider,
    create_embedding_provider,
)
from tests.conftest import SettingsFactory

pytestmark = pytest.mark.unit

DIMS = 1536


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_hashing_provider_is_deterministic_normalised_and_lexical() -> None:
    provider = HashingEmbeddingProvider(DIMS)
    batch = await provider.embed(
        [
            "Battery life is 18 hours on a single charge",
            "battery life: 18 hours per charge",
            "The chassis is machined aluminium",
            "",
        ]
    )
    battery, battery2, chassis, empty = batch.vectors
    assert len(battery) == DIMS
    assert math.isclose(math.sqrt(sum(v * v for v in battery)), 1.0, rel_tol=1e-9)
    assert _cosine(battery, battery2) > _cosine(battery, chassis)
    assert empty == [0.0] * DIMS
    again = await provider.embed(["Battery life is 18 hours on a single charge"])
    assert again.vectors[0] == battery
    assert provider.model == "hashing-v1"


def test_factory_defaults_to_hashing(make_settings: SettingsFactory) -> None:
    assert isinstance(create_embedding_provider(make_settings()), HashingEmbeddingProvider)


def test_openai_provider_requires_key(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        make_settings(embedding_provider="openai")


def test_dimensions_must_match_vector_column(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValueError, match="1536"):
        make_settings(openai_embedding_dimensions=768)


@pytest.fixture
def openai_settings(make_settings: SettingsFactory) -> Settings:
    return make_settings(
        embedding_provider="openai", openai_api_key="sk-test-not-real", embedding_max_retries=2
    )


def _ok(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    data = [
        {"index": i, "embedding": [float(i)] * body["dimensions"]}
        for i in reversed(range(len(body["input"])))  # out of order on purpose
    ]
    return httpx.Response(200, json={"data": data, "usage": {"total_tokens": 7}})


async def test_openai_provider_sends_expected_request(openai_settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok(request)

    provider = OpenAIEmbeddingProvider(openai_settings, transport=httpx.MockTransport(handler))
    batch = await provider.embed(["a", "b", "c"])
    await provider.aclose()

    [request] = seen
    assert request.url.path.endswith("/embeddings")
    assert request.headers["authorization"] == "Bearer sk-test-not-real"
    payload = json.loads(request.content)
    assert payload == {
        "model": "text-embedding-3-small",
        "input": ["a", "b", "c"],
        "dimensions": DIMS,
    }
    assert [v[0] for v in batch.vectors] == [0.0, 1.0, 2.0]  # re-ordered by index
    assert batch.total_tokens == 7


async def test_openai_provider_retries_rate_limits(
    openai_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.providers.embeddings.asyncio.sleep", no_sleep)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429) if calls < 3 else _ok(request)

    provider = OpenAIEmbeddingProvider(openai_settings, transport=httpx.MockTransport(handler))
    batch = await provider.embed(["x"])
    await provider.aclose()
    assert calls == 3
    assert len(batch.vectors) == 1


async def test_openai_provider_gives_up_after_max_retries(
    openai_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.providers.embeddings.asyncio.sleep", no_sleep)
    provider = OpenAIEmbeddingProvider(
        openai_settings, transport=httpx.MockTransport(lambda _: httpx.Response(503))
    )
    with pytest.raises(EmbeddingError, match="HTTP 503") as info:
        await provider.embed(["x"])
    await provider.aclose()
    assert "sk-test" not in str(info.value)


async def test_openai_provider_does_not_retry_client_errors(openai_settings: Settings) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": {"message": "bad"}})

    provider = OpenAIEmbeddingProvider(openai_settings, transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingError, match="HTTP 400"):
        await provider.embed(["x"])
    await provider.aclose()
    assert calls == 1


async def test_openai_provider_rejects_wrong_dimensions(openai_settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    provider = OpenAIEmbeddingProvider(openai_settings, transport=httpx.MockTransport(handler))
    with pytest.raises(EmbeddingError, match="dimensions"):
        await provider.embed(["x"])
    await provider.aclose()
