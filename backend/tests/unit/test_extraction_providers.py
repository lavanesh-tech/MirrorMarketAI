"""Requirement extractor providers: rules and OpenAI structured outputs (mocked HTTP)."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.domain.requirements import Operator, Priority
from app.providers.extraction import (
    RESPONSE_SCHEMA,
    ExtractionError,
    OpenAIRequirementExtractor,
    RuleBasedExtractor,
    create_requirement_extractor,
)
from tests.conftest import SettingsFactory

pytestmark = pytest.mark.unit

GOOD_OUTPUT: dict[str, Any] = {
    "category": "laptop",
    "budget": {"min_amount": None, "max_amount": 1499.99, "currency": "USD"},
    "criteria": [
        {
            "key": "ram_gb",
            "operator": ">=",
            "value_number": 16,
            "value_text": None,
            "unit": "GB",
            "priority": "MUST",
            "weight": 5,
        },
        {
            "key": "has_thunderbolt",
            "operator": "=",
            "value_number": None,
            "value_text": "yes",
            "unit": None,
            "priority": "SHOULD",
            "weight": 3,
        },
    ],
    "excluded_brands": ["Dell"],
    "use_cases": ["programming"],
    "unparsed": ["likes blue"],
}


def _chat(content: Any, *, status: int = 200, refusal: str | None = None) -> httpx.Response:
    message = {"role": "assistant", "content": json.dumps(content), "refusal": refusal}
    return httpx.Response(
        status, json={"choices": [{"message": message}], "usage": {"total_tokens": 42}}
    )


@pytest.fixture
def openai_settings(make_settings: SettingsFactory) -> Settings:
    return make_settings(
        requirements_extractor="openai", openai_api_key="sk-test-not-real", embedding_max_retries=2
    )


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.providers.extraction.asyncio.sleep", _no_sleep)


def _extractor(
    settings: Settings, handler: Callable[[httpx.Request], httpx.Response]
) -> OpenAIRequirementExtractor:
    return OpenAIRequirementExtractor(settings, transport=httpx.MockTransport(handler))


async def test_rule_extractor_and_factory(make_settings: SettingsFactory) -> None:
    extractor = create_requirement_extractor(make_settings())
    assert isinstance(extractor, RuleBasedExtractor)
    result = await extractor.extract("laptop with at least 16 GB RAM")
    assert result.extractor == "rules-v1"
    assert result.spec.category == "laptop"
    await extractor.aclose()


def test_openai_extractor_requires_key(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        make_settings(requirements_extractor="openai")
    with pytest.raises(ExtractionError, match="OPENAI_API_KEY"):
        OpenAIRequirementExtractor(make_settings())


async def test_openai_extractor_sends_strict_schema_and_parses(openai_settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _chat(GOOD_OUTPUT)

    extractor = _extractor(openai_settings, handler)
    assert isinstance(create_requirement_extractor(openai_settings), OpenAIRequirementExtractor)
    result = await extractor.extract("brief text")
    await extractor.aclose()

    body = json.loads(seen[0].content)
    assert seen[0].url.path.endswith("/chat/completions")
    assert seen[0].headers["authorization"] == "Bearer sk-test-not-real"
    assert body["model"] == "gpt-4.1-mini"
    assert body["temperature"] == 0
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] == RESPONSE_SCHEMA
    assert body["messages"][1] == {"role": "user", "content": "brief text"}
    assert "untrusted" in body["messages"][0]["content"]

    assert result.extractor == "openai:gpt-4.1-mini"
    assert result.tokens_used == 42
    assert result.unparsed == ["likes blue"]
    spec = result.spec
    assert spec.budget is not None
    assert spec.budget.max_amount == Decimal("1499.99")
    ram = spec.criteria[0]
    assert (ram.operator, ram.value_number, ram.priority) == (
        Operator.GTE,
        Decimal(16),
        Priority.MUST,
    )


def test_strict_schema_requires_every_property() -> None:
    def check(node: dict[str, Any]) -> None:
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                check(child)
        for option in node.get("anyOf", []):
            check(option)
        if node.get("type") == "array":
            check(node["items"])

    check(RESPONSE_SCHEMA)


async def test_empty_budget_becomes_none(openai_settings: Settings) -> None:
    output = {**GOOD_OUTPUT, "budget": {"min_amount": None, "max_amount": None, "currency": "USD"}}
    extractor = _extractor(openai_settings, lambda _: _chat(output))
    assert (await extractor.extract("x")).spec.budget is None


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_chat({**GOOD_OUTPUT, "category": "spaceship"}), "failed validation"),
        (_chat(["not", "an", "object"]), "not a JSON object"),
        (_chat(GOOD_OUTPUT, refusal="I can't help"), "refused"),
        (httpx.Response(200, json={"choices": []}), "unexpected response"),
        (
            httpx.Response(200, json={"choices": [{"message": {"content": "{not json"}}]}),
            "unexpected response",
        ),
        (httpx.Response(400, json={"error": "bad"}), "HTTP 400"),
    ],
)
async def test_bad_responses_raise_extraction_error(
    openai_settings: Settings, response: httpx.Response, message: str
) -> None:
    extractor = _extractor(openai_settings, lambda _: response)
    with pytest.raises(ExtractionError, match=message):
        await extractor.extract("brief text")


async def test_retries_transient_errors(openai_settings: Settings, no_sleep: None) -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        if calls["n"] == 2:
            return httpx.Response(429)
        return _chat(GOOD_OUTPUT)

    result = await _extractor(openai_settings, handler).extract("x")
    assert calls["n"] == 3
    assert result.spec.category == "laptop"


async def test_gives_up_after_max_retries(openai_settings: Settings, no_sleep: None) -> None:
    def always_down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(ExtractionError, match="ConnectError"):
        await _extractor(openai_settings, always_down).extract("x")
    with pytest.raises(ExtractionError, match="HTTP 503"):
        await _extractor(openai_settings, lambda _: httpx.Response(503)).extract("x")
