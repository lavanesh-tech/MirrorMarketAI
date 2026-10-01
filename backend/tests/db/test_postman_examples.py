"""The generated Postman collection really works when its requests are run in order."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest

from tools import api_docs

pytestmark = [pytest.mark.db, pytest.mark.api]

VARIABLE = re.compile(r"\{\{(\w+)\}\}")

# (operation id, expected status). Each step uses only the collection's own example body.
JOURNEY = [
    ("register", 201),
    ("login", 200),
    ("me", 200),
    ("create_workspace", 201),
    ("list_workspaces", 200),
    ("create_product", 201),
    ("add_product", 201),
    ("record_prices", 201),
    ("price_history", 200),
    ("save_requirements", 201),
    ("get_requirements", 200),
    ("add_comment", 201),
    ("list_comments", 200),
    ("vote", 200),
    ("list_votes", 200),
    ("assess_value", 201),
    ("ask", 201),
    ("list_agent_runs", 200),
    ("activity", 200),
    ("my_audit_logs", 200),
    ("workspace_audit_logs", 200),
    ("refresh", 200),
    ("change_password", 200),
    ("logout", 204),
]


class PostmanRunner:
    """Just enough of Postman: variable substitution, bearer auth, and the capture scripts."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        spec = api_docs.build_spec()
        collection = api_docs.build_postman(spec)
        self.variables = {v["key"]: v["value"] for v in collection["variable"]}
        requests = {
            (r["request"]["method"], r["request"]["url"]["raw"]): r["request"]
            for folder in collection["item"]
            for r in folder["item"]
        }
        self.by_operation = {
            operation["operationId"]: requests[
                method, "{{base_url}}" + re.sub(r"\{(\w+)\}", r"{{\1}}", path)
            ]
            for method, path, operation in api_docs.operations(spec)
        }

    def _fill(self, text: str) -> str:
        return VARIABLE.sub(lambda m: str(self.variables[m.group(1)]), text)

    async def run(self, operation_id: str) -> httpx.Response:
        request = self.by_operation[operation_id]
        url = self._fill(request["url"]["raw"]).removeprefix(self.variables["base_url"])
        headers = {}
        if request.get("auth", {}).get("type") != "noauth":
            headers["Authorization"] = f"Bearer {self.variables['access_token']}"
        body: Any = None
        if request.get("body", {}).get("mode") == "raw":
            body = json.loads(self._fill(request["body"]["raw"]))
        params = {q["key"]: q["value"] for q in request["url"]["query"] if not q["disabled"]}
        response = await self.client.request(
            request["method"], url, json=body, params=params, headers=headers
        )
        if response.is_success and operation_id in api_docs.TOKEN_OPERATIONS:
            self.variables["access_token"] = response.json()["access_token"]
            self.variables["refresh_token"] = response.json()["refresh_token"]
        if response.is_success and operation_id in api_docs.CAPTURE:
            variable, field = api_docs.CAPTURE[operation_id]
            self.variables[variable] = response.json()[field]
        return response


async def test_collection_journey_runs_against_the_real_api(api: httpx.AsyncClient) -> None:
    runner = PostmanRunner(api)
    for operation_id, expected in JOURNEY:
        response = await runner.run(operation_id)
        assert response.status_code == expected, f"{operation_id}: {response.text[:300]}"
    assert runner.variables["workspace_id"]
    assert runner.variables["product_id"]


def test_capture_rules_refer_to_real_operations() -> None:
    ids = {o["operationId"] for _, _, o in api_docs.operations(api_docs.build_spec())}
    assert set(api_docs.CAPTURE) | api_docs.TOKEN_OPERATIONS <= ids
    assert {operation for operation, _ in JOURNEY} <= ids
