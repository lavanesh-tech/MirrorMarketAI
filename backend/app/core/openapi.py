"""OpenAPI document: FastAPI's generated schema plus the API-wide conventions.

FastAPI derives paths and models from the code. This module adds what the code
cannot express per route, so the published contract matches real behaviour:

- stable, readable operation ids (the handler's function name) for client generators;
- one `ErrorResponse` schema on every error (the default FastAPI validation schema
  is replaced, because the app returns its own envelope for 422 too);
- the errors every route can produce: 401 on protected routes, 403/404 on workspace
  routes, 413 on routes with a body, 422 where input is validated;
- the `Idempotency-Key` request header on POST/PATCH and the `X-Request-ID` response
  header everywhere;
- tag descriptions and a short guide in the document description.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

from app import __version__

ERROR_SCHEMA = "ErrorResponse"
_METHODS = ("get", "post", "put", "patch", "delete")

DESCRIPTION = """
Collaborative, evidence-backed purchasing intelligence.

**Authentication.** `POST /auth/login` returns a short-lived access token (send it as
`Authorization: Bearer <token>`) and a single-use refresh token for `POST /auth/refresh`.

**Errors.** Every error has the same shape:
`{"error": {"code": "...", "message": "...", "request_id": "..."}}`. Branch on `code`,
show `message`, quote `request_id` in bug reports.

**Tenancy.** Workspace routes return 404 to non-members, exactly as for a workspace that
does not exist; members whose role is too low get 403.

**Pagination.** List endpoints take `limit` (1-100) and `offset` and return the total.

**Idempotency.** Send an `Idempotency-Key` header on POST/PATCH to make retries safe: the
first response is replayed (`Idempotent-Replayed: true`); the same key with a different body
is rejected with 422.

**Rate limits.** 429 responses carry `Retry-After` (seconds).

**Realtime.** `ws(s)://<host>/api/v1/ws/workspaces/{workspace_id}` pushes events (comments,
votes, agent runs, presence). WebSockets cannot be described in OpenAPI; see `docs/REALTIME.md`.
""".strip()

TAGS: list[dict[str, str]] = [
    {"name": "health", "description": "Liveness and readiness probes (no authentication)."},
    {
        "name": "auth",
        "description": "Accounts, sessions (access + refresh tokens), own audit trail.",
    },
    {"name": "workspaces", "description": "Comparison workspaces, members, workspace audit trail."},
    {"name": "workspace products", "description": "Products being compared in a workspace."},
    {"name": "products", "description": "Global product catalog: variants, identifiers, specs."},
    {"name": "prices", "description": "Price observations and bucketed price history."},
    {"name": "sources", "description": "Evidence sources: safe URL ingestion and uploads."},
    {"name": "embeddings", "description": "Chunking and embedding jobs for ingested documents."},
    {"name": "search", "description": "Hybrid (lexical + vector) search over workspace evidence."},
    {"name": "requirements", "description": "Purchase requirements with immutable versions."},
    {"name": "evidence", "description": "Evidence packs and citation validation."},
    {
        "name": "agents",
        "description": "Agent runs: research, reviews, compatibility, value, risk, "
        "synthesis, orchestration, comparison and Ask.",
    },
    {"name": "collaboration", "description": "Comments, votes, presence and the activity feed."},
]

_COMMON_ERRORS: dict[str, str] = {
    "401": "Missing, invalid, expired or revoked access token",
    "403": "Your role in this workspace does not allow this",
    "404": "Not found (also returned to non-members of a workspace)",
    "413": "Request body too large",
    "422": "The request is invalid, or a business rule rejected it",
    "429": "Rate limited; retry after the number of seconds in `Retry-After`",
}


def operation_id(route: APIRoute) -> str:
    """The handler's function name: `create_workspace`, not `create_workspace_api_v1_...`."""
    return route.name


def _error_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{ERROR_SCHEMA}"}}
        },
    }


def _error_schema() -> dict[str, Any]:
    return {
        "title": ERROR_SCHEMA,
        "type": "object",
        "required": ["error"],
        "properties": {
            "error": {
                "type": "object",
                "required": ["code", "message", "request_id"],
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Stable machine-readable code; branch on this.",
                        "examples": ["workspace_not_found"],
                    },
                    "message": {"type": "string", "description": "Safe to show to a user."},
                    "request_id": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Also in the X-Request-ID response header.",
                    },
                    "details": {
                        "type": "array",
                        "description": "Present on validation errors: where and why.",
                        "items": {"type": "object"},
                    },
                },
            }
        },
    }


def _finish_operation(path: str, method: str, operation: dict[str, Any]) -> None:
    responses: dict[str, Any] = operation.setdefault("responses", {})
    expected = []
    if operation.get("security"):
        expected.append("401")
    if "{workspace_id}" in path:
        expected += ["403", "404"]
    if "requestBody" in operation:
        expected.append("413")
    for code in expected:
        responses.setdefault(code, {"description": _COMMON_ERRORS[code]})

    for code, response in responses.items():
        if code[0] in "45":
            description = response.get("description") or _COMMON_ERRORS.get(code, "Error")
            if description == "Validation Error":  # FastAPI's default wording
                description = _COMMON_ERRORS["422"]
            responses[code] = _error_response(description)
        headers = responses[code].setdefault("headers", {})
        headers["X-Request-ID"] = {"$ref": "#/components/headers/X-Request-ID"}
        if code == "429":
            headers["Retry-After"] = {"$ref": "#/components/headers/Retry-After"}

    if method in ("post", "patch"):
        operation.setdefault("parameters", []).append(
            {"$ref": "#/components/parameters/IdempotencyKey"}
        )


def build_openapi(app: FastAPI) -> dict[str, Any]:
    spec = get_openapi(
        title=app.title,
        version=__version__,
        description=DESCRIPTION,
        routes=app.routes,
        tags=TAGS,
        license_info={"name": "Proprietary"},
        contact={
            "name": "MirrorMarket AI",
            "url": "https://github.com/lavanesh-tech/MirrorMarketAI",
        },
    )
    components = spec.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)
    schemas[ERROR_SCHEMA] = _error_schema()
    components["headers"] = {
        "X-Request-ID": {
            "description": "Correlation id of this request (echoes a valid incoming one).",
            "schema": {"type": "string"},
        },
        "Retry-After": {
            "description": "Seconds to wait before retrying.",
            "schema": {"type": "integer"},
        },
    }
    components["parameters"] = {
        "IdempotencyKey": {
            "name": "Idempotency-Key",
            "in": "header",
            "required": False,
            "description": "1-255 visible ASCII characters. Makes a retry return the first "
            "response instead of repeating the action.",
            "schema": {"type": "string", "minLength": 1, "maxLength": 255},
        }
    }
    for path, item in spec["paths"].items():
        for method in _METHODS:
            if method in item:
                _finish_operation(path, method, item[method])
    return spec


def install(app: FastAPI) -> None:
    """Make `app.openapi()` (and therefore /openapi.json and /docs) serve the finished document."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = build_openapi(app)
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
