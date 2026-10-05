"""The Prometheus endpoint: what it exposes, how it is labelled and who may read it."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi import FastAPI
from prometheus_client.parser import text_string_to_metric_families

from app.main import create_app
from app.telemetry import metrics
from tests.conftest import SettingsFactory

pytestmark = pytest.mark.api

OBSERVABILITY = Path(__file__).resolve().parents[3] / "infrastructure" / "observability"
DASHBOARD = OBSERVABILITY / "grafana" / "dashboards" / "mirrormarket.json"
ALERTS = OBSERVABILITY / "prometheus" / "alerts.yml"
PROMETHEUS = OBSERVABILITY / "prometheus" / "prometheus.yml"
COMPOSE = OBSERVABILITY.parents[1] / "docker-compose.yml"
_METRIC = re.compile(r"\bmm_[a-z0-9_]+")


def _value(name: str, **labels: str) -> float:
    return metrics.REGISTRY.get_sample_value(name, labels) or 0.0


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def test_metrics_are_served_in_the_prometheus_text_format(client: httpx.AsyncClient) -> None:
    await client.get("/api/v1/health")
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    families = {family.name for family in text_string_to_metric_families(response.text)}
    assert {"mm_build_info", "mm_http_requests", "mm_http_request_duration_seconds"} <= families
    assert "process_cpu_seconds" in families
    assert 'mm_build_info{version="' in response.text


async def test_requests_are_counted_by_route_template_and_status(
    client: httpx.AsyncClient,
) -> None:
    labels = {"method": "GET", "route": "/api/v1/health", "status": "200"}
    before = _value("mm_http_requests_total", **labels)
    timed = _value("mm_http_request_duration_seconds_count", method="GET", route="/api/v1/health")
    await client.get("/api/v1/health")
    await client.get("/api/v1/health")
    assert _value("mm_http_requests_total", **labels) == before + 2
    assert (
        _value("mm_http_request_duration_seconds_count", method="GET", route="/api/v1/health")
        == timed + 2
    )
    assert _value("mm_http_requests_in_progress") == 0


async def test_path_parameters_never_become_label_values(client: httpx.AsyncClient) -> None:
    """One series per route, not one per workspace id."""
    template = "/api/v1/workspaces/{workspace_id}"
    before = _value("mm_http_requests_total", method="GET", route=template, status="401")
    ids = ["3f0c1f8e-6c0b-4d0e-9a51-0d6a3c0b7a11", "9d2b7c44-1a5e-4f1b-8f0a-5e2f6a7b8c90"]
    for workspace_id in ids:
        assert (await client.get(f"/api/v1/workspaces/{workspace_id}")).status_code == 401
    assert (
        _value("mm_http_requests_total", method="GET", route=template, status="401") == before + 2
    )
    body = (await client.get("/metrics")).text
    assert not any(workspace_id in body for workspace_id in ids)


async def test_unknown_paths_share_one_label(client: httpx.AsyncClient) -> None:
    """A scanner trying thousands of URLs must not create thousands of series."""
    labels = {"method": "GET", "route": metrics.UNMATCHED_ROUTE, "status": "404"}
    before = _value("mm_http_requests_total", **labels)
    await client.get("/wp-admin/setup.php")
    await client.get("/.env")
    assert _value("mm_http_requests_total", **labels) == before + 2
    assert "wp-admin" not in (await client.get("/metrics")).text


async def test_scrapes_are_not_counted_as_requests(client: httpx.AsyncClient) -> None:
    await client.get("/metrics")
    assert 'route="/metrics"' not in (await client.get("/metrics")).text


async def test_metrics_endpoint_is_not_part_of_the_public_api(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/api/v1/openapi.json")).json()
    assert "/metrics" not in spec["paths"]


async def test_metrics_can_be_switched_off(make_settings: SettingsFactory) -> None:
    async with await _client(create_app(make_settings(metrics_enabled=False))) as http:
        assert (await http.get("/metrics")).status_code == 404
        assert (await http.get("/api/v1/health")).status_code == 200


async def test_metrics_token_is_required_when_configured(make_settings: SettingsFactory) -> None:
    app = create_app(make_settings(metrics_token="scrape-secret-0123456789"))
    async with await _client(app) as http:
        missing = await http.get("/metrics")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert missing.content == b""
        wrong = await http.get("/metrics", headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        right = await http.get(
            "/metrics", headers={"Authorization": "Bearer scrape-secret-0123456789"}
        )
        assert right.status_code == 200
        assert "mm_build_info" in right.text
        assert "scrape-secret" not in right.text


def test_engine_names_collapse_to_a_small_label_set() -> None:
    assert metrics.engine_kind("openai:gpt-4.1-mini") == "openai"
    assert metrics.engine_kind("rules-v1") == "rules"


def test_agent_runs_record_tokens_and_degradation() -> None:
    labels = {"agent": "risk", "engine": "openai", "status": "SUCCEEDED"}
    runs = _value("mm_agent_runs_total", **labels)
    tokens = _value("mm_llm_tokens_total", agent="risk")
    degraded = _value("mm_agent_degraded_total", agent="risk")
    metrics.observe_agent_run("risk", "openai:gpt-4.1-mini", "SUCCEEDED", 1.5, 120, degraded=True)
    metrics.observe_agent_run("risk", "openai:gpt-4.1-mini", "SUCCEEDED", 0.2, 0, degraded=False)
    assert _value("mm_agent_runs_total", **labels) == runs + 2
    assert _value("mm_llm_tokens_total", agent="risk") == tokens + 120
    assert _value("mm_agent_degraded_total", agent="risk") == degraded + 1


def test_pool_gauges_read_live_values() -> None:
    state = {"out": 3.0}
    metrics.watch_pool(lambda: state["out"], lambda: 2.0)
    assert _value("mm_db_pool_connections", state="checked_out") == 3.0
    state["out"] = 5.0
    assert _value("mm_db_pool_connections", state="checked_out") == 5.0
    assert _value("mm_db_pool_connections", state="idle") == 2.0


# --- The dashboards and alerts must only use metrics the code really exposes ----------


def _used_in_alerts() -> set[str]:
    rules = yaml.safe_load(ALERTS.read_text())
    return {
        name
        for group in rules["groups"]
        for rule in group["rules"]
        for name in _METRIC.findall(rule["expr"])
    }


def _used_in_dashboard() -> set[str]:
    dashboard = json.loads(DASHBOARD.read_text())
    return {
        name
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
        for name in _METRIC.findall(target.get("expr", ""))
    }


def test_alert_rules_only_reference_metrics_that_exist() -> None:
    used = _used_in_alerts()
    assert used, "no mm_* metric found in alerts.yml"
    assert used - metrics.metric_names() == set()


def test_dashboard_only_references_metrics_that_exist() -> None:
    used = _used_in_dashboard()
    assert used, "no mm_* metric found in the dashboard"
    assert used - metrics.metric_names() == set()


def test_every_alert_says_what_it_means_and_how_bad_it_is() -> None:
    rules = [r for g in yaml.safe_load(ALERTS.read_text())["groups"] for r in g["rules"]]
    assert len({rule["alert"] for rule in rules}) == len(rules)
    for rule in rules:
        assert rule["labels"]["severity"] in {"critical", "warning", "info"}, rule["alert"]
        assert rule["annotations"]["summary"], rule["alert"]
        assert rule["expr"].strip(), rule["alert"]


def test_dashboard_panels_are_well_formed() -> None:
    dashboard = json.loads(DASHBOARD.read_text())
    assert dashboard["uid"] == "mirrormarket-overview"
    panels = dashboard["panels"]
    assert len({panel["id"] for panel in panels}) == len(panels)
    for panel in panels:
        if panel["type"] == "row":
            continue
        assert panel["title"], panel["id"]
        assert panel["targets"], panel["title"]
        assert panel["datasource"]["uid"] == "prometheus", panel["title"]


def test_prometheus_scrapes_services_that_compose_defines() -> None:
    config = yaml.safe_load(PROMETHEUS.read_text())
    assert config["rule_files"] == ["/etc/prometheus/alerts.yml"]
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    targets = [
        target
        for job in config["scrape_configs"]
        for static in job["static_configs"]
        for target in static["targets"]
    ]
    assert targets
    for target in targets:
        assert target.split(":")[0] in services, target
    for name in ("prometheus", "grafana", "jaeger"):
        assert services[name]["profiles"] == ["observability"]
        for port in services[name].get("ports", []):
            assert port.startswith("127.0.0.1:"), f"{name} must only listen on localhost"
