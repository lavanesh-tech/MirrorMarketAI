"""Minimal OpenAI chat client for structured (JSON Schema) outputs, shared by all LLM features.

Retries transport errors and retryable HTTP statuses with exponential backoff.
Errors never include prompts, documents or the API key.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

_RETRYABLE = {408, 409, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """The model call failed or returned something unusable."""


@dataclass(frozen=True, slots=True)
class JsonCompletion:
    data: dict[str, Any]
    tokens_used: int


class OpenAIChatClient:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if settings.openai_api_key is None or not settings.openai_configured:
            raise LLMError("OPENAI_API_KEY is not configured")
        self.model = settings.openai_chat_model
        self._max_retries = settings.embedding_max_retries
        self._client = httpx.AsyncClient(
            base_url=settings.openai_base_url,
            transport=transport,
            timeout=httpx.Timeout(settings.openai_request_timeout_seconds),
            headers={"Authorization": f"Bearer {settings.openai_api_key.get_secret_value()}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], name: str
    ) -> JsonCompletion:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
        }
        body = await self._post_with_retries(payload)
        try:
            message = body["choices"][0]["message"]
            if message.get("refusal"):
                raise LLMError("model refused the request")
            parsed = json.loads(message["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError("unexpected response shape from chat API") from exc
        if not isinstance(parsed, dict):
            raise LLMError("model output is not a JSON object")
        return JsonCompletion(parsed, int(body.get("usage", {}).get("total_tokens", 0)))

    async def _post_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                response = await self._client.post("/chat/completions", json=payload)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise LLMError(f"chat request failed: {type(exc).__name__}") from exc
            else:
                if response.status_code == httpx.codes.OK:
                    result: dict[str, Any] = response.json()
                    return result
                if response.status_code not in _RETRYABLE or attempt >= self._max_retries:
                    raise LLMError(f"chat API returned HTTP {response.status_code}")
            attempt += 1
            delay = min(8.0, 0.5 * 2**attempt)
            logger.warning("chat request retry", extra={"attempt": attempt, "delay_s": delay})
            await asyncio.sleep(delay)
