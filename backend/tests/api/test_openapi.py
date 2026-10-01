"""The published API contract: quality rules, and no drift from the committed artefacts."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate

from app.core.openapi import ERROR_SCHEMA, TAGS
from app.main import create_app
from tests.conftest import SettingsFactory
from tools import api_docs

pytestmark = pytest.mark.api

PUBLIC = {"health", "ready", "register", "login", "refresh", "logout"}
SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")
VARIABLE = re.compile(r"\{\{(\w+)\}\}")


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return api_docs.build_spec()


@pytest.fixture(scope="module")
def ops(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return api_docs.operations(spec)


def test_document_is_valid_openapi_3_1(spec: dict[str, Any]) -> None:
    validate(spec)
    assert spec["openapi"].startswith("3.1")
    assert spec["info"]["title"] == "MirrorMarket AI"
    assert "Idempotency-Key" in spec["info"]["description"]


def test_every_operation_is_documented(ops: list[tuple[str, str, dict[str, Any]]]) -> None:
    assert len(ops) > 60
    known_tags = {tag["name"] for tag in TAGS}
    ids = [operation["operationId"] for _, _, operation in ops]
    assert len(ids) == len(set(ids))
    for method, path, operation in ops:
        where = f"{method} {path}"
        assert SNAKE_CASE.fullmatch(operation["operationId"]), where
        assert len(operation.get("summary", "")) >= 3, where
        assert len(operation["tags"]) == 1, where
        assert operation["tags"][0] in known_tags, where


def test_errors_use_one_envelope_and_common_errors_are_declared(
    spec: dict[str, Any], ops: list[tuple[str, str, dict[str, Any]]]
) -> None:
    schemas = spec["components"]["schemas"]
    assert "HTTPValidationError" not in schemas
    assert "ValidationError" not in schemas
    assert "HTTPValidationError" not in json.dumps(spec)
    reference = {"$ref": f"#/components/schemas/{ERROR_SCHEMA}"}
    for method, path, operation in ops:
        where = f"{method} {path}"
        responses = operation["responses"]
        for code, response in responses.items():
            assert "X-Request-ID" in response["headers"], where
            if code[0] in "45":
                assert response["content"]["application/json"]["schema"] == reference, where
                assert response["description"] != "Validation Error", where
        public = operation["operationId"] in PUBLIC
        assert ("401" in responses) == (
            not public or operation["operationId"] in {"login", "refresh"}
        ), where
        assert bool(operation.get("security")) == (not public), where
        if "{workspace_id}" in path:
            assert {"403", "404"} <= set(responses), where
        if "429" in responses:
            assert "Retry-After" in responses["429"]["headers"], where
        idempotent = any(
            p.get("$ref", "").endswith("/IdempotencyKey") for p in operation.get("parameters", [])
        )
        assert idempotent == (method in ("POST", "PATCH")), where


async def test_error_schema_matches_real_error_responses(
    spec: dict[str, Any], make_settings: SettingsFactory
) -> None:
    validator = Draft202012Validator(spec["components"]["schemas"][ERROR_SCHEMA])
    app = create_app(make_settings())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        unauthenticated = (await client.get("/api/v1/auth/me")).json()
        invalid = (await client.post("/api/v1/auth/login", json={"email": "nope"})).json()
        missing = (await client.get("/api/v1/does-not-exist")).json()
    for body in (unauthenticated, invalid, missing):
        validator.validate(body)
    assert invalid["error"]["code"] == "validation_error"
    assert "details" in invalid["error"]


def test_docs_can_be_switched_off(make_settings: SettingsFactory) -> None:
    app = create_app(make_settings(docs_enabled=False))
    assert (app.openapi_url, app.docs_url) == (None, None)
    assert len(app.openapi()["paths"]) > 40  # still available to tooling


def test_committed_api_artefacts_are_up_to_date() -> None:
    """If this fails, the API changed: run `make api-docs` and commit the result."""
    for name, content in api_docs.render_all().items():
        committed = api_docs.OUTPUT_DIR / name
        assert committed.exists(), f"{name} is missing; run `make api-docs`"
        assert committed.read_text(encoding="utf-8") == content, f"{name} is stale"


def test_generation_is_deterministic() -> None:
    assert api_docs.render_all() == api_docs.render_all()


def _requests(collection: dict[str, Any]) -> list[dict[str, Any]]:
    return [request for folder in collection["item"] for request in folder["item"]]


def test_postman_collection_covers_every_operation(
    spec: dict[str, Any], ops: list[tuple[str, str, dict[str, Any]]]
) -> None:
    collection = api_docs.build_postman(spec)
    assert collection["info"]["schema"] == api_docs.POSTMAN_SCHEMA
    requests = _requests(collection)
    assert len(requests) == len(ops)
    assert len({r["id"] for r in requests}) == len(requests)
    assert [folder["name"] for folder in collection["item"]] == [
        tag["name"] for tag in TAGS if any(o["tags"][0] == tag["name"] for _, _, o in ops)
    ]

    declared = {variable["key"] for variable in collection["variable"]}
    used = set(VARIABLE.findall(json.dumps(collection)))
    assert used <= declared, used - declared

    by_url = {(r["request"]["method"], r["request"]["url"]["raw"]): r for r in requests}
    login = by_url["POST", "{{base_url}}/api/v1/auth/login"]
    assert login["request"]["auth"] == {"type": "noauth"}
    assert "access_token" in "".join(login["event"][0]["script"]["exec"])
    create = by_url["POST", "{{base_url}}/api/v1/workspaces"]
    assert "auth" not in create["request"]  # inherits the collection's bearer token
    assert 'set("workspace_id"' in "".join(create["event"][0]["script"]["exec"])
    upload = by_url["POST", "{{base_url}}/api/v1/products/{{product_id}}/sources/upload"]
    assert upload["request"]["body"]["mode"] == "formdata"
    assert {"key": "file", "type": "file", "src": ""} in upload["request"]["body"]["formdata"]
    listing = by_url["GET", "{{base_url}}/api/v1/workspaces"]
    assert {q["key"] for q in listing["request"]["url"]["query"]} == {"limit", "offset"}
    assert all(q["disabled"] for q in listing["request"]["url"]["query"])


def test_postman_example_bodies_satisfy_the_request_schemas(
    spec: dict[str, Any], ops: list[tuple[str, str, dict[str, Any]]]
) -> None:
    checked = 0
    for method, path, operation in ops:
        content = operation.get("requestBody", {}).get("content", {})
        if "application/json" not in content:
            continue
        body = api_docs.body_example(operation, spec)
        text = VARIABLE.sub(api_docs.ZERO_UUID, json.dumps(body))  # variables become ids
        schema = {**content["application/json"]["schema"], "components": spec["components"]}
        errors = [e.message for e in Draft202012Validator(schema).iter_errors(json.loads(text))]
        assert errors == [], f"{method} {path}: {errors}"
        checked += 1
    assert checked > 15


def test_endpoint_index_lists_every_operation(
    spec: dict[str, Any], ops: list[tuple[str, str, dict[str, Any]]]
) -> None:
    index = api_docs.build_endpoints(spec)
    for method, path, _ in ops:
        assert f"| {method} | `{path}` |" in index
    assert index.count("| public |") == len(PUBLIC)
