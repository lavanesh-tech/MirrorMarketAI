"""Embedding providers behind one small interface.

- OpenAIEmbeddingProvider: real semantic embeddings via the OpenAI REST API
  (httpx, explicit timeouts, bounded retries with backoff on 429/5xx).
- HashingEmbeddingProvider: deterministic, offline lexical embeddings (feature
  hashing of word unigrams + bigrams, L2-normalised). No network, no key: CI
  and the local demo work anywhere. It captures word overlap, NOT meaning;
  retrieval quality with it is measured and reported separately (Phase 27).
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import EMBEDDING_DIMENSIONS, Settings

logger = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")
_RETRYABLE = frozenset({408, 409, 429, 500, 502, 503, 504})


class EmbeddingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    total_tokens: int


class EmbeddingProvider(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: list[str]) -> EmbeddingBatch: ...

    async def aclose(self) -> None: ...


class HashingEmbeddingProvider:
    """Signed feature hashing into a fixed-size, unit-length vector."""

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self._dimensions = dimensions

    @property
    def model(self) -> str:
        return "hashing-v1"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _vector(self, text: str) -> list[float]:
        words = _WORD.findall(text.lower())
        features = words + [f"{a} {b}" for a, b in itertools.pairwise(words)]
        vector = [0.0] * self._dimensions
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % self._dimensions
            vector[index] += 1.0 if (value >> 63) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector

    async def embed(self, texts: list[str]) -> EmbeddingBatch:
        return EmbeddingBatch(
            vectors=[self._vector(t) for t in texts],
            total_tokens=sum(len(_WORD.findall(t.lower())) for t in texts),
        )

    async def aclose(self) -> None:
        return None


class OpenAIEmbeddingProvider:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if settings.openai_api_key is None or not settings.openai_configured:
            raise EmbeddingError("OPENAI_API_KEY is not configured")
        self._model = settings.openai_embedding_model
        self._dimensions = settings.openai_embedding_dimensions
        self._max_retries = settings.embedding_max_retries
        self._client = httpx.AsyncClient(
            base_url=settings.openai_base_url,
            transport=transport,
            timeout=httpx.Timeout(settings.openai_request_timeout_seconds),
            headers={"Authorization": f"Bearer {settings.openai_api_key.get_secret_value()}"},
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed(self, texts: list[str]) -> EmbeddingBatch:
        payload = {"model": self._model, "input": texts, "dimensions": self._dimensions}
        body = await self._post_with_retries(payload)
        data = sorted(body.get("data", []), key=lambda item: item["index"])
        vectors = [list(map(float, item["embedding"])) for item in data]
        if len(vectors) != len(texts):
            raise EmbeddingError("embedding count does not match input count")
        if any(len(v) != self._dimensions for v in vectors):
            raise EmbeddingError("embedding dimensions do not match configuration")
        tokens = int(body.get("usage", {}).get("total_tokens", 0))
        return EmbeddingBatch(vectors=vectors, total_tokens=tokens)

    async def _post_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                response = await self._client.post("/embeddings", json=payload)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise EmbeddingError(f"embedding request failed: {type(exc).__name__}") from exc
            else:
                if response.status_code == httpx.codes.OK:
                    result: dict[str, Any] = response.json()
                    return result
                if response.status_code not in _RETRYABLE or attempt >= self._max_retries:
                    # Never include the request (it would contain the API key header
                    # in some clients' reprs) or document text in the error.
                    raise EmbeddingError(f"embedding API returned HTTP {response.status_code}")
            attempt += 1
            delay = min(8.0, 0.5 * 2**attempt)
            logger.warning("embedding request retry", extra={"attempt": attempt, "delay_s": delay})
            await asyncio.sleep(delay)


def create_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "openai":
        return OpenAIEmbeddingProvider(settings)
    return HashingEmbeddingProvider(settings.openai_embedding_dimensions)
