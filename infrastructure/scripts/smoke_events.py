#!/usr/bin/env python3
"""End-to-end smoke test of the event pipeline against a running stack (`make up`).

Registers a throwaway user, creates a workspace, posts a comment, then polls the
activity feed until the comment shows up. That only happens if every hop works:
API -> outbox (PostgreSQL) -> relay -> Kafka -> consumer -> read model -> API.

Standard library only, so it runs on a bare CI runner:
    python3 infrastructure/scripts/smoke_events.py [base_url]
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/") + "/api/v1"
DEADLINE_SECONDS = float(os.environ.get("SMOKE_DEADLINE_SECONDS", "60"))
PASSWORD = f"smoke-{uuid.uuid4().hex}"


def call(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(  # noqa: S310 - fixed http(s) base URL
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.loads(response.read())


def main() -> int:
    email = f"smoke-{uuid.uuid4().hex[:12]}@example.com"
    call("POST", "/auth/register", {"email": email, "password": PASSWORD, "display_name": "Smoke"})
    token = call("POST", "/auth/login", {"email": email, "password": PASSWORD})["access_token"]
    workspace = call("POST", "/workspaces", {"name": "Smoke test"}, token)["id"]
    text = f"smoke {uuid.uuid4().hex[:8]}"
    started = time.monotonic()
    call("POST", f"/workspaces/{workspace}/comments", {"body": text}, token)

    while time.monotonic() - started < DEADLINE_SECONDS:
        items = call("GET", f"/workspaces/{workspace}/activity", token=token)["items"]
        if any(item["summary"] == f"commented: {text}" for item in items):
            elapsed = time.monotonic() - started
            print(f"OK: comment reached the activity feed through Kafka in {elapsed:.2f}s")
            return 0
        time.sleep(0.5)
    print(f"FAILED: no activity entry after {DEADLINE_SECONDS:.0f}s (is the event-worker healthy?)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
