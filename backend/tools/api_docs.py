"""Generate the published API artefacts from the running application's OpenAPI document.

    uv run python -m tools.api_docs            # write docs/api/*
    uv run python -m tools.api_docs --check    # exit 1 if the committed files are stale

Outputs (all derived from code, never edited by hand):
- docs/api/openapi.json                         the contract
- docs/api/ENDPOINTS.md                         a human-readable endpoint index
- docs/api/mirrormarket.postman_collection.json one request per operation, with example
                                                bodies and scripts that carry ids/tokens forward
- docs/api/mirrormarket.postman_environment.json

A test runs the same generators and compares with the committed files, so the
documentation cannot drift from the code without CI failing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.openapi import TAGS
from app.main import create_app

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "api"
OPENAPI_FILE = "openapi.json"
ENDPOINTS_FILE = "ENDPOINTS.md"
POSTMAN_FILE = "mirrormarket.postman_collection.json"
ENVIRONMENT_FILE = "mirrormarket.postman_environment.json"

METHODS = ("get", "post", "put", "patch", "delete")
POSTMAN_SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
_NAMESPACE = uuid.UUID("6f0d3c1e-5d0b-4a55-9d2b-1f2a3b4c5d6e")  # stable ids across runs
_PATH_PARAM = re.compile(r"\{(\w+)\}")
ZERO_UUID = "00000000-0000-0000-0000-000000000000"

# Values that make the generated requests work when run in order against a fresh stack.
EXAMPLE_VALUES: dict[str, Any] = {
    "email": "demo@example.com",
    "password": "correct-horse-battery-staple",
    "current_password": "correct-horse-battery-staple",
    "new_password": "a-brand-new-passphrase",
    "display_name": "Demo User",
    "refresh_token": "{{refresh_token}}",
    "product_id": "{{product_id}}",
    "workspace_id": "{{workspace_id}}",
    "parent_id": None,
    "question": "How long does the battery last?",
    "query": "battery life",
    "body": "The battery life looks better on this one.",
    "brand": "Acme",
    "category": "laptop",
    "retailer": "Example Store",
    "currency": "USD",
    "amount": "1299.00",
    "observed_at": "2026-01-15T12:00:00Z",
    "url": "https://example.com/acme-l14",
    "text": "I need a laptop under $1500 with at least 16 GB RAM and a 14 inch screen.",
    "expected_version": 0,
}
# After these operations succeed, remember an id for the requests that follow.
CAPTURE: dict[str, tuple[str, str]] = {
    "create_workspace": ("workspace_id", "id"),
    "create_product": ("product_id", "id"),
    "create_source": ("source_id", "id"),
    "add_comment": ("comment_id", "id"),
    "create_evidence_pack": ("pack_id", "id"),
    "research_product": ("run_id", "id"),
}
TOKEN_OPERATIONS = {"login", "refresh", "change_password"}
# Optional body fields worth showing because the request is not useful without them
# (the schema cannot say "one of these is required").
OPTIONAL_FIELDS: dict[str, tuple[str, ...]] = {
    "save_requirements": ("text",),
    "extract_requirements": ("text",),
}


def build_spec() -> dict[str, Any]:
    """The OpenAPI document of an app built with default settings (no .env, no Redis)."""
    app = create_app(Settings(_env_file=None, redis_url=None))
    return app.openapi()


def operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method.upper(), path, item[method])
        for path, item in spec["paths"].items()
        for method in METHODS
        if method in item
    ]


# --------------------------------------------------------------------------- examples
def _resolve(schema: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _scalar(schema: dict[str, Any]) -> Any:  # noqa: PLR0911 - one return per JSON type
    kind = schema.get("type")
    if kind == "integer":
        if "minimum" in schema:
            return int(schema["minimum"])
        return int(schema.get("exclusiveMinimum", 0)) + 1
    if kind == "number":
        return float(schema.get("minimum", 1))
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    fmt = schema.get("format")
    if fmt == "uuid":
        return ZERO_UUID
    if fmt == "date-time":
        return "2026-01-15T12:00:00Z"
    if fmt == "email":
        return "demo@example.com"
    if fmt == "uri":
        return "https://example.com/"
    return "example".ljust(int(schema.get("minLength", 0)), "x")


def example(schema: dict[str, Any], spec: dict[str, Any], name: str | None = None) -> Any:  # noqa: PLR0911
    """A small valid instance: required fields only, well-known names get realistic values."""
    schema = _resolve(schema, spec)
    if name in EXAMPLE_VALUES and schema.get("type") not in ("object", "array"):
        return EXAMPLE_VALUES[name]
    for key in ("examples", "enum"):
        if schema.get(key):
            return schema[key][0]
    if "const" in schema:
        return schema["const"]
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [s for s in schema[key] if s.get("type") != "null"] or schema[key]
            return example(options[0], spec, name)
    if schema.get("type") == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        return {
            key: example(properties[key], spec, key)
            for key in schema.get("required", [])
            if key in properties
        }
    if schema.get("type") == "array":
        return [example(schema.get("items", {}), spec, name)]
    return _scalar(schema)


# ----------------------------------------------------------------------------- postman
def _script(lines: list[str]) -> dict[str, Any]:
    return {"listen": "test", "script": {"type": "text/javascript", "exec": lines}}


def _events(operation_id: str) -> list[dict[str, Any]]:
    if operation_id in TOKEN_OPERATIONS:
        return [
            _script(
                [
                    "if (pm.response.code === 200) {",
                    "    const data = pm.response.json();",
                    '    pm.collectionVariables.set("access_token", data.access_token);',
                    '    pm.collectionVariables.set("refresh_token", data.refresh_token);',
                    "}",
                ]
            )
        ]
    if operation_id in CAPTURE:
        variable, field = CAPTURE[operation_id]
        return [
            _script(
                [
                    "if (pm.response.code === 201) {",
                    f'    pm.collectionVariables.set("{variable}", pm.response.json().{field});',
                    "}",
                ]
            )
        ]
    return []


def body_example(operation: dict[str, Any], spec: dict[str, Any]) -> Any:
    """The example JSON body for an operation (required fields plus OPTIONAL_FIELDS)."""
    schema = operation["requestBody"]["content"]["application/json"].get("schema", {})
    value = example(schema, spec)
    properties = _resolve(schema, spec).get("properties", {})
    for name in OPTIONAL_FIELDS.get(operation["operationId"], ()):
        if isinstance(value, dict) and name in properties:
            value[name] = example(properties[name], spec, name)
    return value


def _body(operation: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any] | None:
    content = operation.get("requestBody", {}).get("content", {})
    if "application/json" in content:
        value = body_example(operation, spec)
        return {
            "mode": "raw",
            "raw": json.dumps(value, indent=2),
            "options": {"raw": {"language": "json"}},
        }
    if "multipart/form-data" in content:
        schema = _resolve(content["multipart/form-data"].get("schema", {}), spec)
        required = set(schema.get("required", []))
        fields = []
        for key, prop in schema.get("properties", {}).items():
            is_file = _resolve(prop, spec).get("format") == "binary" or key == "file"
            field: dict[str, Any] = {"key": key, "type": "file" if is_file else "text"}
            if is_file:
                field["src"] = ""
            else:
                field["value"] = str(example(prop, spec, key) or "")
                field["disabled"] = key not in required
            fields.append(field)
        return {"mode": "formdata", "formdata": fields}
    return None


def _request(
    method: str, path: str, operation: dict[str, Any], spec: dict[str, Any]
) -> dict[str, Any]:
    raw_path = _PATH_PARAM.sub(lambda m: "{{" + m.group(1) + "}}", path)
    query = [
        {
            "key": parameter["name"],
            "value": str(example(parameter.get("schema", {}), spec, parameter["name"]) or ""),
            "description": parameter.get("description", ""),
            "disabled": not parameter.get("required", False),
        }
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "query"
    ]
    request: dict[str, Any] = {
        "method": method,
        "header": [],
        "url": {
            "raw": "{{base_url}}" + raw_path,
            "host": ["{{base_url}}"],
            "path": [part for part in raw_path.split("/") if part],
            "query": query,
        },
        "description": operation.get("description", ""),
    }
    if not operation.get("security"):
        request["auth"] = {"type": "noauth"}
    body = _body(operation, spec)
    if body is not None:
        request["body"] = body
    item: dict[str, Any] = {
        "name": operation.get("summary", operation["operationId"]),
        "id": str(uuid.uuid5(_NAMESPACE, operation["operationId"])),
        "request": request,
        "response": [],
    }
    events = _events(operation["operationId"])
    if events:
        item["event"] = events
    return item


def build_postman(spec: dict[str, Any]) -> dict[str, Any]:
    by_tag: dict[str, list[dict[str, Any]]] = {tag["name"]: [] for tag in TAGS}
    variables = {"base_url": "http://127.0.0.1:8000", "access_token": "", "refresh_token": ""}
    for method, path, operation in operations(spec):
        by_tag[operation["tags"][0]].append(_request(method, path, operation, spec))
        for name in _PATH_PARAM.findall(path):
            variables.setdefault(name, "")
    return {
        "info": {
            "name": spec["info"]["title"],
            "_postman_id": str(uuid.uuid5(_NAMESPACE, "collection")),
            "description": (
                "Generated from the OpenAPI document; do not edit by hand.\n\n"
                "Run `auth > Create an account`, then `auth > Exchange email + password for an "
                "access token`. Tokens and ids of things you create are stored in collection "
                "variables and used by the requests that follow."
            ),
            "schema": POSTMAN_SCHEMA,
        },
        "auth": {
            "type": "bearer",
            "bearer": [{"key": "token", "value": "{{access_token}}", "type": "string"}],
        },
        "variable": [{"key": key, "value": value} for key, value in variables.items()],
        "item": [
            {"name": tag["name"], "description": tag["description"], "item": by_tag[tag["name"]]}
            for tag in TAGS
            if by_tag[tag["name"]]
        ],
    }


def build_environment() -> dict[str, Any]:
    return {
        "id": str(uuid.uuid5(_NAMESPACE, "environment")),
        "name": "MirrorMarket local",
        "values": [{"key": "base_url", "value": "http://127.0.0.1:8000", "enabled": True}],
        "_postman_variable_scope": "environment",
    }


# --------------------------------------------------------------------------- endpoints
def build_endpoints(spec: dict[str, Any]) -> str:
    grouped: dict[str, list[tuple[str, str, dict[str, Any]]]] = {tag["name"]: [] for tag in TAGS}
    for method, path, operation in operations(spec):
        grouped[operation["tags"][0]].append((method, path, operation))
    total = sum(len(rows) for rows in grouped.values())
    lines = [
        "# API endpoints",
        "",
        f"Generated from `openapi.json` ({total} operations); do not edit by hand. "
        "Regenerate with `make api-docs`.",
        "",
        "Auth: **token** = `Authorization: Bearer <access token>`; **public** = none.",
    ]
    for tag in TAGS:
        rows = grouped[tag["name"]]
        if not rows:
            continue
        lines += ["", f"## {tag['name']}", "", tag["description"], ""]
        lines += ["| Method | Path | What it does | Auth |", "| --- | --- | --- | --- |"]
        for method, path, operation in rows:
            auth = "token" if operation.get("security") else "public"
            summary = operation.get("summary", "").replace("|", "\\|")
            lines.append(f"| {method} | `{path}` | {summary} | {auth} |")
    return "\n".join(lines) + "\n"


def render_all() -> dict[str, str]:
    """File name -> exact content."""
    spec = build_spec()

    def dump(value: Any) -> str:
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    return {
        OPENAPI_FILE: dump(spec),
        ENDPOINTS_FILE: build_endpoints(spec),
        POSTMAN_FILE: dump(build_postman(spec)),
        ENVIRONMENT_FILE: dump(build_environment()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if committed files are stale")
    args = parser.parse_args()
    stale = []
    for name, content in render_all().items():
        target = OUTPUT_DIR / name
        if args.check:
            if not target.exists() or target.read_text(encoding="utf-8") != content:
                stale.append(name)
        else:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            print(f"wrote {target.relative_to(OUTPUT_DIR.parents[1])}")  # noqa: T201
    if stale:
        print("stale: " + ", ".join(stale) + " (run `make api-docs`)")  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
