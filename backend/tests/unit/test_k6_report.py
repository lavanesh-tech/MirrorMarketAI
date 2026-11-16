"""The load-test report: what is kept from a k6 summary and how a run is judged."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from benchmarks import k6_report, k6_run

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
WORKLOAD = ROOT / "benchmarks" / "k6" / "workload.js"
WEIGHTS = {"workspace_get": 60, "search": 30, "analyze": 10}


def _trend(p50: float, p95: float) -> dict[str, Any]:
    values = {"med": p50, "p(95)": p95, "p(99)": p95 * 1.5, "max": p95 * 2, "avg": p50}
    return {"type": "trend", "values": values, "thresholds": {"max>=0": {"ok": True}}}


def _tagged(
    tag: str, count: int, failed: float, p50: float, p95: float, *, ok: bool = True
) -> dict[str, Any]:
    return {
        f"http_req_duration{{{tag}}}": _trend(p50, p95),
        f"http_reqs{{{tag}}}": {"type": "counter", "values": {"count": count, "rate": 1.0}},
        f"http_req_failed{{{tag}}}": {
            "type": "rate",
            "values": {"rate": failed},
            "thresholds": {"rate<0.01": {"ok": ok}},
        },
    }


def _raw(profile: str, **extra_metrics: Any) -> dict[str, Any]:
    metrics: dict[str, Any] = _tagged("phase:test", 1000, 0.0, 8.0, 40.0)
    for name in WEIGHTS:
        metrics |= _tagged(f"op:{name}", 100, 0.0, 5.0, 20.0)
    metrics |= extra_metrics
    return {
        "profile": profile,
        "config": {
            "users": 10,
            "products_per_user": 3,
            "rate": 30,
            "steps": [50, 100, 200],
            "step_seconds": 30,
            "weights": WEIGHTS,
        },
        "scenarios": {profile: {"executor": "constant-arrival-rate", "rate": 30}},
        "state": {"testRunDurationMs": 125_000},
        "metrics": metrics,
    }


def _record(raw: dict[str, Any], label: str = "w1") -> dict[str, Any]:
    return k6_report.summarise(raw) | {
        "label": label,
        "recorded_at": "2026-10-05T00:00:00+00:00",
        "commit": "abc1234",
        "dirty": False,
        "environment": {
            "machine": "arm64, 15 logical CPUs",
            "os": "Darwin 25",
            "docker_cpus": 15,
            "docker_memory_gb": 7.7,
            "api": {
                "WEB_CONCURRENCY": label[1:],
                "AGENT_ENGINE": "rules",
                "EMBEDDING_PROVIDER": "hashing",
            },
        },
    }


def _capacity() -> dict[str, Any]:
    steps: dict[str, Any] = {}
    steps |= _tagged("scenario:step_50", 1500, 0.0, 8.0, 30.0)
    steps |= _tagged("scenario:step_100", 3000, 0.001, 20.0, 400.0)
    steps |= _tagged("scenario:step_200", 5100, 0.2, 900.0, 3050.0)
    steps["dropped_iterations{scenario:step_200}"] = {"type": "counter", "values": {"count": 42}}
    steps["dropped_iterations"] = {"type": "counter", "values": {"count": 42}}
    return _raw("capacity", **steps)


def test_a_steady_run_keeps_counts_errors_and_percentiles() -> None:
    record = k6_report.summarise(_raw("load"))
    assert record["profile"] == "load"
    assert record["target_rate"] == 30
    assert record["duration_seconds"] == 125.0
    assert record["overall"] == {
        "requests": 1000,
        "failed_rate": 0.0,
        "p50_ms": 8.0,
        "p95_ms": 40.0,
        "p99_ms": 60.0,
        "max_ms": 80.0,
        "dropped": 0,
    }
    assert record["operations"]["search"]["weight"] == 30
    assert record["operations"]["search"]["p95_ms"] == 20.0
    assert record["thresholds_passed"] is True
    assert "steps" not in record


def test_a_failed_threshold_is_recorded() -> None:
    raw = _raw("load", **_tagged("phase:test", 1000, 0.05, 8.0, 40.0, ok=False))
    assert k6_report.summarise(raw)["thresholds_passed"] is False


def test_capacity_steps_are_judged_one_by_one() -> None:
    record = k6_report.summarise(_capacity())
    by_rate = {step["target_rate"]: step for step in record["steps"]}
    assert by_rate[50]["achieved_rate"] == 50.0
    assert [by_rate[rate]["sustained"] for rate in (50, 100, 200)] == [True, True, False]
    assert by_rate[200]["dropped"] == 42
    assert by_rate[200]["achieved_rate"] == 170.0
    assert k6_report.highest_sustained(record) == 100


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({}, True),
        ({"failed_rate": 0.01}, False),
        ({"dropped": 1}, False),
        ({"achieved_rate": 97.9}, False),
        ({"p95_ms": 1000.0}, False),
    ],
)
def test_what_counts_as_sustained(change: dict[str, float], expected: bool) -> None:
    step = {
        "failed_rate": 0.0,
        "dropped": 0,
        "achieved_rate": 100.0,
        "target_rate": 100,
        "p95_ms": 50.0,
    }
    assert k6_report.sustained(step | change) is expected


def test_a_recovery_after_a_failed_step_does_not_count() -> None:
    record = k6_report.summarise(_capacity())
    record["steps"][0]["sustained"] = False
    assert k6_report.highest_sustained(record) is None


def test_report_shows_every_run_with_its_environment() -> None:
    report = k6_report.render([_record(_capacity(), "w4"), _record(_raw("load"))])
    assert "### Capacity, w4" in report
    assert "### Steady load, w1" in report
    assert "commit `abc1234`" in report
    assert "arm64, 15 logical CPUs" in report
    assert "API: 4 process(es)" in report
    assert "Highest rate sustained: **100 requests/s**." in report
    assert "| 200 | 170.0 | 20.00% | 42 | 900 | 3,050 | 4,575 | no |" in report
    assert "| `search` | 30% | 100 | 0.00% | 5.0 | 20.0 | 30.0 |" in report
    assert "synthetic" in report


def test_report_without_runs_says_so() -> None:
    assert "No runs recorded yet." in k6_report.render([])


def test_uncommitted_changes_are_disclosed() -> None:
    record = _record(_raw("load")) | {"dirty": True}
    assert "(with uncommitted changes)" in k6_report.render([record])


def test_load_test_refuses_paid_engines() -> None:
    offline = {
        "AGENT_ENGINE": "rules",
        "REQUIREMENTS_EXTRACTOR": "rules",
        "EMBEDDING_PROVIDER": "hashing",
    }
    k6_run.refuse_paid_engines(offline)
    with pytest.raises(SystemExit, match="offline engines"):
        k6_run.refuse_paid_engines(offline | {"AGENT_ENGINE": "openai"})
    with pytest.raises(SystemExit, match="offline engines"):
        k6_run.refuse_paid_engines({})


def test_no_secret_is_recorded_with_a_result() -> None:
    for name in k6_run.RECORDED_SETTINGS:
        assert not re.search(r"KEY|SECRET|TOKEN|PASSWORD|URL", name), name


def test_request_shares_sum_to_100() -> None:
    weights = [
        int(w) for w in re.findall(r"^  \w+: \[\s*(\d+),", WORKLOAD.read_text(), re.MULTILINE)
    ]
    assert len(weights) >= 10
    assert sum(weights) == 100


def test_overlay_forces_offline_engines_and_keeps_k6_optional() -> None:
    overlay = yaml.safe_load((ROOT / "docker-compose.loadtest.yml").read_text())
    for service in ("api", "embedding-worker", "event-worker"):
        env = overlay["services"][service]["environment"]
        assert (env["AGENT_ENGINE"], env["REQUIREMENTS_EXTRACTOR"], env["EMBEDDING_PROVIDER"]) == (
            "rules",
            "rules",
            "hashing",
        )
    k6 = overlay["services"]["k6"]
    assert k6["profiles"] == ["loadtest"]
    assert "--no-usage-report" in k6["entrypoint"]


def test_a_run_that_never_reached_the_limit_does_not_claim_one() -> None:
    raw = _capacity()
    raw["metrics"] |= _tagged("scenario:step_200", 6000, 0.0, 10.0, 50.0)
    raw["metrics"]["dropped_iterations{scenario:step_200}"]["values"]["count"] = 0
    report = k6_report.render([_record(raw)])
    assert "Every step was sustained: the limit is above **200 requests/s**." in report
    assert "Highest rate sustained" not in report


def _sample(at: int, api: float, postgres: float, locks: int = 0) -> dict[str, Any]:
    return {
        "at_seconds": at,
        "cpu_percent": {"api": api, "postgres": postgres, "redis": 3.0, "k6": 40.0, "kafka": 5.0},
        "postgres_sessions": {
            "active": 2,
            "idle_in_transaction": 9,
            "waiting_on_lock": locks,
            "idle": 1,
        },
    }


def test_the_busiest_sample_is_the_one_with_the_most_cpu_in_total() -> None:
    peak = k6_report.busiest(
        [_sample(5, 30.0, 10.0, locks=4), _sample(10, 99.6, 35.2), _sample(15, 60, 20)]
    )
    assert peak is not None
    assert peak["at_seconds"] == 10
    assert peak["cpu_percent"] == {"api": 100, "postgres": 35, "redis": 3, "k6": 40, "other": 5}
    assert peak["postgres_sessions"]["idle_in_transaction"] == 9
    assert peak["max_waiting_on_lock"] == 4
    assert k6_report.busiest([]) is None


def test_report_says_where_the_time_went() -> None:
    record = _record(_capacity(), "w4") | {
        "busiest": k6_report.busiest([_sample(10, 99.6, 35.2)]),
        "api_cpu_seconds_per_process": [41.5, 3.0],
    }
    report = k6_report.render([record])
    assert "API **100%** across 4 process(es), PostgreSQL **35%**" in report
    assert "9 holding a transaction open while waiting for the API" in report
    assert "CPU seconds used by each API process over the whole run: 41.5, 3." in report


def test_reports_from_before_sampling_still_render() -> None:
    assert "At the busiest moment" not in k6_report.render([_record(_capacity())])


def test_cpu_per_process_ignores_idle_processes() -> None:
    before = {"1": 2.0, "8": 10.0, "9": 10.0}
    after = {"1": 2.1, "8": 50.0, "9": 13.5, "77": 0.05}
    assert k6_run.cpu_per_process(before, after) == [40.0, 3.5]
    assert k6_run.cpu_per_process({}, {}) == []


# --- Phase 30: several API containers behind a load balancer ---------------------------


def test_compose_files_come_from_compose_file_when_set() -> None:
    assert k6_run.compose_command({}) == [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.loadtest.yml",
        "--profile",
        "loadtest",
    ]
    scaled = {"COMPOSE_FILE": "docker-compose.yml:docker-compose.loadtest.yml:x.yml"}
    assert k6_run.compose_command(scaled) == ["docker", "compose", "--profile", "loadtest"]


@pytest.mark.parametrize(
    ("container", "service"),
    [
        ("mirrormarket-api-1", "api"),
        ("mirrormarket-api-3", "api"),
        ("mirrormarket-embedding-worker-1", "embedding-worker"),
        ("mirrormarket-k6-run-0f3a2b", "k6"),
        ("mirrormarket-lb-1", "lb"),
    ],
)
def test_replicas_are_counted_as_their_service(container: str, service: str) -> None:
    assert k6_run.service_of(container) == service


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="reads /proc (Linux, as in the container)")
def test_the_probe_reports_cpu_and_connections_per_process() -> None:
    printed = subprocess.run(  # noqa: S603
        [sys.executable, str(k6_run.PROBE), "8000"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found = k6_run.parse_probe(printed, "0123456789abcdef")
    assert found, printed
    assert all(key.startswith("0123456789ab/") for key in found)
    assert all(cpu >= 0 and connections >= 0 for cpu, connections in found.values())
    assert k6_run.parse_probe("garbage\n12 x 3\n", "c") == {}


def test_each_process_keeps_its_cpu_and_its_connections() -> None:
    before = {"a/1": 1.0, "a/8": 10.0, "b/8": 0.0}
    after = {"a/1": 1.2, "a/8": 30.0, "b/8": 45.0}
    assert k6_run.process_table(before, after, {"a/8": 4, "b/8": 6}) == [
        {"cpu_seconds": 45.0, "peak_connections": 6},
        {"cpu_seconds": 20.0, "peak_connections": 4},
    ]


@pytest.mark.parametrize(
    ("concurrency", "containers", "label"),
    [("1", 1, "w1"), ("4", 1, "w4"), ("1", 3, "r3w1"), ("2", 2, "r2w2")],
)
def test_default_label_names_containers_and_processes(
    concurrency: str, containers: int, label: str
) -> None:
    assert k6_run.default_label({"WEB_CONCURRENCY": concurrency}, containers) == label


def test_report_describes_a_scaled_run() -> None:
    record = _record(_capacity(), "w1") | {
        "label": "r3w1",
        "busiest": k6_report.busiest(
            [
                _sample(10, 240.0, 50.0)
                | {"cpu_percent": {"api": 240.0, "postgres": 50.0, "lb": 20.4}}
            ]
        ),
        "api_processes": [
            {"cpu_seconds": 40.0, "peak_connections": 8},
            {"cpu_seconds": 38.5, "peak_connections": 7},
        ],
    }
    record["environment"]["api_containers"] = 3
    report = k6_report.render([record])
    assert "API: 3 containers behind an nginx load balancer, 1 process(es) each" in report
    assert "API **240%** across 3 process(es)" in report
    assert "load balancer 20%, everything else 0%" in report
    assert "40 s, 8 connection(s); 38.5 s, 7 connection(s)" in report


def test_scale_overlay_puts_every_api_container_behind_the_balancer() -> None:
    text = (ROOT / "docker-compose.scale.yml").read_text()
    assert "ports: !reset []" in text  # only the balancer publishes the API port
    overlay = yaml.safe_load(text.replace("!reset ", ""))
    services = overlay["services"]
    assert services["api"]["deploy"]["replicas"] == "${API_REPLICAS:-3}"
    assert services["k6"]["environment"]["BASE_URL"] == "http://lb:8000"
    lb = services["lb"]
    assert lb["ports"] == ["127.0.0.1:${API_HOST_PORT:-8000}:8000"]
    assert lb["read_only"] is True
    assert lb["cap_drop"] == ["ALL"]
    conf = (ROOT / "infrastructure" / "nginx" / "api-lb.conf").read_text()
    assert "least_conn;" in conf
    assert "server api:8000 resolve;" in conf
    assert "proxy_set_header X-Forwarded-For" in conf


def test_web_app_is_optional_in_compose() -> None:
    base = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    web = base["services"]["web"]
    assert web["profiles"] == ["web"]
    assert web["read_only"] is True
    assert web["ports"] == ["127.0.0.1:${WEB_HOST_PORT:-3000}:3000"]
