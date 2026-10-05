#!/usr/bin/env python3
"""Smoke test of the observability stack (`make obs-up`).

Proves the whole path, not just that containers started:
  1. the API serves /metrics and counts the requests this script makes;
  2. Prometheus scrapes the API and both workers, and loaded the alert rules;
  3. Grafana is healthy and has the provisioned dashboard and data sources;
  4. Jaeger received a trace from the API (so OTLP export works end to end).

Standard library only, so it runs on a bare CI runner:
    python3 infrastructure/scripts/smoke_observability.py [api_url]
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

API = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
PROMETHEUS = os.environ.get("PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")
GRAFANA = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3001").rstrip("/")
JAEGER = os.environ.get("JAEGER_URL", "http://127.0.0.1:16686").rstrip("/")
DEADLINE_SECONDS = float(os.environ.get("SMOKE_DEADLINE_SECONDS", "90"))
JOBS = {"api", "event-worker", "embedding-worker"}
SERVICE = "mirrormarket-api"
DASHBOARD_UID = "mirrormarket-overview"


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - fixed local URLs
        return response.read()


def get_json(url: str) -> Any:
    return json.loads(get(url))


def status_of(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def query(expression: str) -> list[dict]:
    url = f"{PROMETHEUS}/api/v1/query?" + urllib.parse.urlencode({"query": expression})
    return get_json(url)["data"]["result"]


def eventually(what: str, check: Callable[[], str | None]) -> None:
    """Retry `check` until it returns None (ok). It returns the reason while it is not ok."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    reason = "never ran"
    while time.monotonic() < deadline:
        try:
            reason = check() or ""
        except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
        if not reason:
            print(f"ok   {what}")
            return
        time.sleep(2)
    print(f"FAIL {what}: {reason}")
    raise SystemExit(1)


def api_serves_metrics() -> str | None:
    for _ in range(3):
        # Traced (unlike /health); 401 without a token, which is all this needs.
        status_of(f"{API}/api/v1/auth/me")
    body = get(f"{API}/metrics").decode()
    wanted = 'mm_http_requests_total{method="GET",route="/api/v1/auth/me",status="401"}'
    return None if wanted in body else "the requests just made are not in /metrics"


def prometheus_scrapes_everything() -> str | None:
    up = {r["metric"]["job"]: r["value"][1] for r in query("up")}
    down = sorted(job for job in JOBS if up.get(job) != "1")
    return f"not up: {down} (saw {up})" if down else None


def prometheus_has_request_metrics() -> str | None:
    result = query('sum(mm_http_requests_total{route="/api/v1/auth/me"})')
    return None if result and float(result[0]["value"][1]) >= 3 else f"got {result}"


def prometheus_loaded_alert_rules() -> str | None:
    groups = get_json(f"{PROMETHEUS}/api/v1/rules")["data"]["groups"]
    rules = [rule for group in groups for rule in group["rules"]]
    broken = [rule["name"] for rule in rules if rule.get("health") not in ("ok", "unknown")]
    if broken:
        return f"rules failing to evaluate: {broken}"
    return None if len(rules) >= 10 else f"only {len(rules)} rules loaded"


def grafana_has_the_dashboard() -> str | None:
    if get_json(f"{GRAFANA}/api/health").get("database") != "ok":
        return "Grafana is not healthy"
    found = get_json(f"{GRAFANA}/api/dashboards/uid/{DASHBOARD_UID}")
    panels = found["dashboard"]["panels"]
    return None if len(panels) >= 20 else f"dashboard has only {len(panels)} panels"


def grafana_can_query_prometheus() -> str | None:
    """Through Grafana's own data source proxy: the path a dashboard panel uses."""
    url = f"{GRAFANA}/api/datasources/proxy/uid/prometheus/api/v1/query?" + urllib.parse.urlencode(
        {"query": "mm_build_info"}
    )
    result = get_json(url)["data"]["result"]
    return None if result else "mm_build_info returned no series through Grafana"


def jaeger_received_a_trace() -> str | None:
    status_of(f"{API}/api/v1/auth/me")
    services = get_json(f"{JAEGER}/api/services").get("data") or []
    if SERVICE not in services:
        return f"service not known to Jaeger yet (has {services})"
    found = get_json(f"{JAEGER}/api/traces?service={SERVICE}&limit=5").get("data") or []
    names = {span["operationName"] for item in found for span in item["spans"]}
    return None if "GET /api/v1/auth/me" in names else f"no request span yet (saw {sorted(names)})"


def main() -> int:
    eventually("API serves /metrics and counts requests by route", api_serves_metrics)
    eventually("Prometheus scrapes the API and both workers", prometheus_scrapes_everything)
    eventually("Prometheus has the API's request metrics", prometheus_has_request_metrics)
    eventually("Prometheus loaded the alert rules", prometheus_loaded_alert_rules)
    eventually("Grafana is healthy and has the dashboard", grafana_has_the_dashboard)
    eventually("Grafana can query Prometheus", grafana_can_query_prometheus)
    eventually("Jaeger received a trace from the API", jaeger_received_a_trace)
    print("observability smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
