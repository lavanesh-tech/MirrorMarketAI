"""Authorization regression net: every route is checked, including ones added later.

The routes are read from the running app, so a new endpoint that forgets
authentication or the workspace membership check fails here without anyone
having to remember to write a test for it.
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi import FastAPI

from tests.db.conftest import StackFactory, register_user, workspace

pytestmark = [pytest.mark.db, pytest.mark.api]

API = "/api/v1"
# Reachable without an access token, on purpose.
PUBLIC = {
    ("GET", f"{API}/health"),
    ("GET", f"{API}/ready"),
    ("POST", f"{API}/auth/register"),
    ("POST", f"{API}/auth/login"),
    ("POST", f"{API}/auth/refresh"),  # authenticated by the refresh token in the body
    ("POST", f"{API}/auth/logout"),  # same
}
_PARAM = re.compile(r"\{(\w+)\}")
_INTEGER_PARAMS = {"version", "number"}


_METHODS = {"get", "post", "put", "patch", "delete"}


def http_routes(app: FastAPI) -> list[tuple[str, str]]:
    """(METHOD, path template) for every documented HTTP operation."""
    found = [
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in sorted(operations)
        if method in _METHODS
    ]
    assert len(found) > 60  # the app really was introspected
    return found


def fill(path: str, **known: str) -> str:
    def value(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in _INTEGER_PARAMS:
            return "1"
        return known.get(name) or str(uuid.uuid4())

    return _PARAM.sub(value, path)


async def test_every_non_public_route_requires_authentication(stack: StackFactory) -> None:
    async with stack() as s:
        unprotected = []
        for method, path in http_routes(s.app):
            if (method, path) in PUBLIC:
                continue
            for headers in (
                {},
                {"Authorization": "Bearer not-a-token"},
                {"Authorization": "Basic eDp5"},
            ):
                response = await s.http.request(method, fill(path), json={}, headers=headers)
                if response.status_code != 401:
                    unprotected.append((method, path, response.status_code))
        assert unprotected == []


async def test_public_routes_are_exactly_the_documented_ones(stack: StackFactory) -> None:
    async with stack() as s:
        routes = set(http_routes(s.app))
        assert routes >= PUBLIC  # none of them was renamed or removed
        for method, path in sorted(PUBLIC):
            response = await s.http.request(method, path, json={})
            assert response.status_code != 401, (method, path)


async def test_workspace_routes_never_serve_a_non_member(stack: StackFactory) -> None:
    async with stack() as s:
        owner, stranger = await register_user(s.http), await register_user(s.http)
        ws_id = await workspace(s.http, owner)
        checked = 0
        leaks = []
        for method, path in http_routes(s.app):
            if "{workspace_id}" not in path:
                continue
            checked += 1
            for target in (ws_id, str(uuid.uuid4())):  # a real workspace and a made-up one
                response = await s.http.request(
                    method, fill(path, workspace_id=target), json={}, headers=stranger.headers
                )
                # 404 = "no such workspace for you"; 422 = the body was rejected before
                # any lookup. Both are identical for real and made-up workspaces.
                if response.status_code not in (404, 422):
                    leaks.append((method, path, target == ws_id, response.status_code))
        assert leaks == []
        assert checked > 30


async def test_real_and_missing_workspaces_look_the_same_to_a_stranger(
    stack: StackFactory,
) -> None:
    async with stack() as s:
        owner, stranger = await register_user(s.http), await register_user(s.http)
        ws_id = await workspace(s.http, owner)
        for method, path in http_routes(s.app):
            if "{workspace_id}" not in path or method != "GET":
                continue
            seen = []
            others = {name: str(uuid.uuid4()) for name in _PARAM.findall(path)}
            for target in (ws_id, str(uuid.uuid4())):
                url = fill(path, **(others | {"workspace_id": target}))
                response = await s.http.get(url, headers=stranger.headers)
                body = response.json()["error"]
                seen.append((response.status_code, body["code"], body["message"]))
            assert seen[0] == seen[1], (path, seen)  # no way to tell that a workspace exists
